import logging
from asyncio import CancelledError, Task, create_task
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.api.errors import register_exception_handlers
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.broker_account import router as broker_account_router
from app.api.routes.dev import router as dev_router
from app.api.routes.health import router as health_router
from app.api.routes.intents import router as intents_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.symbols import router as symbols_router
from app.api.routes.telegram import router as telegram_router
from app.commands.broker_account import restore_all_bound_users
from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.core.config import get_settings
from app.core.ids import RequestIdMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.broker_account_repository import BrokerAccountRepository
from app.repositories.intent_repository import IntentRepository
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.broker_session_pool import BrokerSessionPool
from app.services.broker_session_reconnect import BrokerSessionReconnectLoop
from app.services.idempotency_cleanup import IdempotencyCleanupScheduler
from app.services.kill_switch import KillSwitchProvider
from app.services.quote import InMemoryQuoteProvider, QuoteProvider, build_quote_provider
from app.services.quote_dispatcher import QuoteEvaluationDispatcher
from app.services.quote_dispatcher_core import TradeIntentCoreDispatcher
from app.services.twap_scheduler import TwapSliceScheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bring the quote source online, wire quotes to evaluation, start the loops.

    Two modes, decided by `QUOTE_PROVIDER` when `create_app()` built the pool:

    - **shared** (`in_memory` / the demo provider): one provider for everyone. Start it,
      reconcile both tracks' subscriptions from the DB, attach both dispatchers.
    - **per-user** (`fubon`): no system login. Log every stored binding in from
      `broker_accounts` (one user failing never stops the rest; nobody bound is a
      valid state — the admin binds the first user after boot), attach the core
      dispatcher scoped per owner, and run the reconnect loop that repairs dropped
      sockets / logins during trading hours. The legacy track has no session to
      read from: its dispatcher is not attached and the TWAP scheduler gets an empty
      provider (slices notify on time, without a reference price) until TWAP moves
      to the core track — see docs/technical-debt.md.

    Skipped entirely without DATABASE_URL (lightweight unit-test runs).

    SINGLE-PROCESS ONLY — do NOT run this under multiple uvicorn workers.
    Everything below (broker logins, quote subscriptions, and the TWAP /
    idempotency-cleanup / reconnect loops) runs once per worker process. With N
    workers you get N logins per user competing for the same broker account (many
    brokers evict the previous session on re-login, so workers repeatedly log each
    other out) and N copies of every background loop. The DB-mutating paths are
    row-lock safe (`SELECT ... FOR UPDATE`), so this would not double-trigger
    intents — but the broker connections are not protected and break. To scale
    horizontally, first split the broker/scheduler work into a single dedicated
    process, or gate it behind a Postgres advisory lock (leader election); only
    then raise `--workers`.
    """

    settings = get_settings()
    pool: BrokerSessionPool = app.state.broker_sessions
    shared = pool.shared_provider
    if shared is not None:
        shared.startup()

    tasks: list[Task[None]] = []
    # A shared startup() already holds a broker login; any failure below must
    # still reach shutdown() or that login leaks at the broker.
    try:
        if settings.database_url:
            from app.db.session import get_session_factory

            session_factory = get_session_factory()
            session_service = TradingSessionService()

            # Wire incoming quotes to the evaluator: every snapshot a session observes
            # drives evaluation of that symbol's active intents on a fresh short-lived
            # session, scoped to the session's owner in per-user mode.
            core_dispatcher = TradeIntentCoreDispatcher(
                session_factory=session_factory,
                evaluator=QuoteEvaluator(session_service),
                session_service=session_service,
                kill_switch=app.state.kill_switch_provider,
            )
            pool.set_quote_listener(core_dispatcher.dispatch)

            if shared is not None:
                _start_shared_provider(app, shared, session_factory, session_service)
                twap_quote_provider: QuoteProvider = shared
            else:

                def accounts_for(db: Session) -> BrokerAccountRepository:
                    return BrokerAccountRepository(db, encryption_key=settings.broker_credential_key)

                restore_all_bound_users(
                    session_factory=session_factory,
                    accounts_for=accounts_for,
                    core_intents_for=TradeIntentCoreRepository,
                    pool=pool,
                    now=datetime.now(UTC),
                )
                logger.info("broker sessions restored", extra={"live_sessions": len(pool.live_sessions())})
                # ponytail: legacy TWAP has no per-user session; empty provider until TWAP cutover.
                twap_quote_provider = InMemoryQuoteProvider()
                reconnect = BrokerSessionReconnectLoop(
                    pool=pool,
                    session_factory=session_factory,
                    accounts_for=accounts_for,
                    core_intents_for=TradeIntentCoreRepository,
                    session_service=session_service,
                )
                tasks.append(create_task(reconnect.run_forever()))
                logger.info("broker session reconnect loop started")

            if settings.twap_worker_enabled:
                scheduler = TwapSliceScheduler(
                    session_factory=session_factory,
                    quote_provider=twap_quote_provider,
                    session_service=session_service,
                    interval_seconds=settings.twap_worker_interval_seconds,
                    kill_switch=app.state.kill_switch_provider,
                )
                tasks.append(create_task(scheduler.run_forever()))
                logger.info(
                    "twap slice scheduler started",
                    extra={"interval_seconds": settings.twap_worker_interval_seconds},
                )

            if settings.idempotency_cleanup_enabled:
                cleanup = IdempotencyCleanupScheduler(
                    session_factory=session_factory,
                    interval_seconds=settings.idempotency_cleanup_interval_seconds,
                )
                tasks.append(create_task(cleanup.run_forever()))
                logger.info(
                    "idempotency cleanup scheduler started",
                    extra={"interval_seconds": settings.idempotency_cleanup_interval_seconds},
                )

        yield
    finally:
        for task in tasks:
            task.cancel()
            try:
                await task
            except CancelledError:
                pass
        pool.stop_all()
        if shared is not None:
            shared.shutdown()


def _start_shared_provider(
    app: FastAPI,
    provider: QuoteProvider,
    session_factory: Callable[[], Session],
    session_service: TradingSessionService,
) -> None:
    """Shared mode as it always was: reconcile both tracks' subscriptions and attach
    the legacy dispatcher (the core one is attached through the pool)."""
    with session_factory() as db:
        intent_repo = IntentRepository(db)
        IntentLifecycleCommand(intent_repo, session_service).run()
        for symbol in intent_repo.active_or_scheduled_symbols():
            provider.subscribe(symbol)
        # 模式丙：新軌（trade_intent_core）的 symbol 也要訂閱，否則 robot #2 收不到
        # 報價。subscribe 對重複 symbol 為 idempotent，新舊軌共用同一訂閱集。
        core_repo = TradeIntentCoreRepository(db)
        IntentLifecycleCommand(core_repo, session_service).run()
        for symbol in core_repo.active_or_scheduled_symbols():
            provider.subscribe(symbol)
    logger.info(
        "quote provider startup reconcile complete",
        extra={"active_subscriptions": sorted(provider.active_subscriptions())},
    )
    # 舊軌 dispatcher：每筆報價兩台各掃各表（舊 trade_intents / 新 trade_intent_core），
    # 一張單只存在一張表 → 不會重複觸發。共用 evaluator / kill switch。
    dispatcher = QuoteEvaluationDispatcher(
        session_factory=session_factory,
        evaluator=QuoteEvaluator(session_service),
        session_service=session_service,
        kill_switch=app.state.kill_switch_provider,
    )
    provider.add_quote_listener(dispatcher.dispatch)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.state.request_id_header = settings.request_id_header
    # Every quote source hangs off the pool: shared providers (in_memory and the
    # demo provider) serve every user through it; `fubon` is per-user, one session
    # per bound user logged in by the lifespan from `broker_accounts`.
    shared = None if settings.quote_provider == "fubon" else build_quote_provider(settings)
    app.state.broker_sessions = BrokerSessionPool(settings, shared=shared)
    # Kill-switch read cache (L2). Needs a session factory, so it only comes online
    # when a DATABASE_URL is configured; DB-less unit/api wiring leaves it None and
    # the create / dispatch paths treat "no provider" as "not halted".
    app.state.kill_switch_provider = None
    if settings.database_url:
        from app.db.session import get_session_factory

        app.state.kill_switch_provider = KillSwitchProvider(get_session_factory())
    allow_origins = settings.cors_allow_origins_list
    if allow_origins:
        # Credentialed (cookie-bearing) cross-origin calls require an explicit
        # allow-list — `*` is rejected by browsers when credentials are sent.
        # Headers list covers what the SPA sends: bearer auth, the CSRF echo,
        # the request-id and idempotency keys.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-Id", "Idempotency-Key"],
        )
    app.add_middleware(RequestIdMiddleware, header_name=settings.request_id_header)
    # Outermost so the hardening headers land on every response, including
    # error envelopes and CORS preflight. HSTS only over HTTPS (production).
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=not settings.local_mode)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
    app.include_router(broker_account_router, prefix="/me", tags=["broker-account"])
    app.include_router(symbols_router, prefix="/symbols", tags=["symbols"])
    app.include_router(quotes_router, prefix="/quotes", tags=["quotes"])
    app.include_router(intents_router, prefix="/trade-intents", tags=["trade-intents"])
    app.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
    app.include_router(telegram_router, prefix="/telegram", tags=["telegram"])
    if settings.local_mode:
        app.include_router(dev_router, prefix="/dev", tags=["dev"])
    return app


app = create_app()
