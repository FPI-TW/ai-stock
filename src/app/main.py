import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.routes.dev import router as dev_router
from app.api.routes.health import router as health_router
from app.api.routes.intents import router as intents_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.symbols import router as symbols_router
from app.api.routes.test_page import router as test_page_router
from app.core.config import get_settings
from app.core.ids import RequestIdMiddleware
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.services.quote import build_quote_provider
from app.services.quote_dispatcher import QuoteEvaluationDispatcher

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

    if settings.database_url:
        from app.db.session import get_session_factory

        session_factory = get_session_factory()
        with session_factory() as db:
            for symbol in IntentRepository(db).active_or_scheduled_symbols():
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
        #
        # Use the same TradingSessionService instance the API deps hand out
        # (stored on app.state) so /dev/set-clock affects both API-driven
        # evaluation and listener-driven evaluation. Without this the
        # dispatcher would see system time, drop pushed quotes as stale, and
        # the /test page demo flow would silently never trigger.
        session_service = app.state.session_service
        dispatcher = QuoteEvaluationDispatcher(
            session_factory=session_factory,
            evaluator=QuoteEvaluator(session_service),
            session_service=session_service,
        )
        provider.add_quote_listener(dispatcher.dispatch)

    try:
        yield
    finally:
        provider.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    app.state.request_id_header = settings.request_id_header
    app.state.quote_provider = build_quote_provider(settings)
    app.state.session_service = TradingSessionService()
    app.add_middleware(RequestIdMiddleware, header_name=settings.request_id_header)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(symbols_router, prefix="/symbols", tags=["symbols"])
    app.include_router(intents_router, prefix="/trade-intents", tags=["trade-intents"])
    app.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
    if settings.local_mode:
        app.include_router(dev_router, prefix="/dev", tags=["dev"])
        app.include_router(test_page_router, tags=["test-ui"])
        app.mount(
            "/test-assets",
            StaticFiles(directory=Path(__file__).parent / "static" / "test"),
            name="test-assets",
        )
    return app


app = create_app()
