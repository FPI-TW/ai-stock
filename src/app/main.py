import logging
from asyncio import CancelledError, Task, create_task
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.dev import router as dev_router
from app.api.routes.health import router as health_router
from app.api.routes.intents import router as intents_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.symbols import router as symbols_router
from app.api.routes.telegram import router as telegram_router
from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.core.config import get_settings
from app.core.ids import RequestIdMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.idempotency_cleanup import IdempotencyCleanupScheduler
from app.services.kill_switch import KillSwitchProvider
from app.services.quote import build_quote_provider
from app.services.quote_dispatcher import QuoteEvaluationDispatcher
from app.services.quote_dispatcher_core import TradeIntentCoreDispatcher
from app.services.twap_scheduler import TwapSliceScheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Bring the quote provider online and reconcile subscriptions on startup.

    The provider itself is already built in `create_app()` (so DI works without
    waiting for the lifespan to fire — important for tests that don't use
    `with TestClient(app)`). This lifespan covers the side-effectful parts:

    1. `provider.startup()` — broker session setup; no-op for `InMemoryQuoteProvider`.
    2. Initial reconcile — read every active/scheduled intent from DB and
       `subscribe()` its symbol. Skipped when no DATABASE_URL is configured
       (e.g. lightweight unit-test runs).
    3. `provider.shutdown()` on exit — broker logout, clear local state.

    SINGLE-PROCESS ONLY — do NOT run this under multiple uvicorn workers.
    Everything below (broker login, quote subscriptions, and the TWAP /
    idempotency-cleanup / quote-evaluation schedulers) runs once per worker
    process. With N workers you get N broker logins competing for the same
    account (many brokers evict the previous session on re-login, so workers
    repeatedly log each other out) and N copies of every background loop. The
    DB-mutating paths are row-lock safe (`SELECT ... FOR UPDATE`), so this would
    not double-trigger intents — but the broker connection is not protected and
    breaks. To scale horizontally, first split the broker/scheduler work into a
    single dedicated process, or gate it behind a Postgres advisory lock
    (leader election); only then raise `--workers`.
    """

    settings = get_settings()
    provider = app.state.quote_provider
    provider.startup()

    twap_scheduler_task: Task[None] | None = None
    idempotency_cleanup_task: Task[None] | None = None
    if settings.database_url:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()
        session_service = TradingSessionService()
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

        # Wire incoming quotes to the evaluator: every snapshot the provider
        # observes (via broker callback or `push_quote`) now drives evaluation
        # of that symbol's active intents on a fresh short-lived session.
        # Without DATABASE_URL we have no intents to evaluate, so the listener
        # is only useful when the DB is configured.
        dispatcher = QuoteEvaluationDispatcher(
            session_factory=session_factory,
            evaluator=QuoteEvaluator(session_service),
            session_service=session_service,
            kill_switch=app.state.kill_switch_provider,
        )
        provider.add_quote_listener(dispatcher.dispatch)

        # 模式丙 robot #2：新軌的報價→觸發 dispatcher，作為第二個 listener 並行掛上。
        # 每筆報價兩台各掃各表（舊 trade_intents / 新 trade_intent_core），一張單只存在
        # 一張表 → 不會重複觸發。共用 evaluator / kill switch。
        core_dispatcher = TradeIntentCoreDispatcher(
            session_factory=session_factory,
            evaluator=QuoteEvaluator(session_service),
            session_service=session_service,
            kill_switch=app.state.kill_switch_provider,
        )
        provider.add_quote_listener(core_dispatcher.dispatch)

        if settings.twap_worker_enabled:
            scheduler = TwapSliceScheduler(
                session_factory=session_factory,
                quote_provider=provider,
                session_service=session_service,
                interval_seconds=settings.twap_worker_interval_seconds,
                kill_switch=app.state.kill_switch_provider,
            )
            twap_scheduler_task = create_task(scheduler.run_forever())
            logger.info(
                "twap slice scheduler started",
                extra={"interval_seconds": settings.twap_worker_interval_seconds},
            )

        if settings.idempotency_cleanup_enabled:
            cleanup = IdempotencyCleanupScheduler(
                session_factory=session_factory,
                interval_seconds=settings.idempotency_cleanup_interval_seconds,
            )
            idempotency_cleanup_task = create_task(cleanup.run_forever())
            logger.info(
                "idempotency cleanup scheduler started",
                extra={"interval_seconds": settings.idempotency_cleanup_interval_seconds},
            )

    try:
        yield
    finally:
        for task in (twap_scheduler_task, idempotency_cleanup_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except CancelledError:
                    pass
        provider.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.state.request_id_header = settings.request_id_header
    app.state.quote_provider = build_quote_provider(settings)
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
    app.include_router(symbols_router, prefix="/symbols", tags=["symbols"])
    app.include_router(quotes_router, prefix="/quotes", tags=["quotes"])
    app.include_router(intents_router, prefix="/trade-intents", tags=["trade-intents"])
    app.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
    app.include_router(telegram_router, prefix="/telegram", tags=["telegram"])
    if settings.local_mode:
        app.include_router(dev_router, prefix="/dev", tags=["dev"])
    return app


app = create_app()
