import logging
from asyncio import CancelledError, Task, create_task
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routes.auth import router as auth_router
from app.api.routes.dev import router as dev_router
from app.api.routes.health import router as health_router
from app.api.routes.intents import router as intents_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.quotes import router as quotes_router
from app.api.routes.symbols import router as symbols_router
from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.core.config import get_settings
from app.core.ids import RequestIdMiddleware
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.services.quote import build_quote_provider
from app.services.quote_dispatcher import QuoteEvaluationDispatcher
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
    """

    settings = get_settings()
    provider = app.state.quote_provider
    provider.startup()

    twap_scheduler_task: Task[None] | None = None
    if settings.database_url:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()
        session_service = TradingSessionService()
        with session_factory() as db:
            intent_repo = IntentRepository(db)
            IntentLifecycleCommand(intent_repo, session_service).run()
            for symbol in intent_repo.active_or_scheduled_symbols():
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
        )
        provider.add_quote_listener(dispatcher.dispatch)

        if settings.twap_worker_enabled:
            scheduler = TwapSliceScheduler(
                session_factory=session_factory,
                quote_provider=provider,
                session_service=session_service,
                interval_seconds=settings.twap_worker_interval_seconds,
            )
            twap_scheduler_task = create_task(scheduler.run_forever())
            logger.info(
                "twap slice scheduler started",
                extra={"interval_seconds": settings.twap_worker_interval_seconds},
            )

    try:
        yield
    finally:
        if twap_scheduler_task is not None:
            twap_scheduler_task.cancel()
            try:
                await twap_scheduler_task
            except CancelledError:
                pass
        provider.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.state.request_id_header = settings.request_id_header
    app.state.quote_provider = build_quote_provider(settings)
    allow_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
    if allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestIdMiddleware, header_name=settings.request_id_header)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(symbols_router, prefix="/symbols", tags=["symbols"])
    app.include_router(quotes_router, prefix="/quotes", tags=["quotes"])
    app.include_router(intents_router, prefix="/trade-intents", tags=["trade-intents"])
    app.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
    if settings.local_mode:
        app.include_router(dev_router, prefix="/dev", tags=["dev"])
    return app


app = create_app()
