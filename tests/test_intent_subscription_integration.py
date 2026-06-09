"""Integration test for lifespan-driven quote subscription reconcile.

Covers:
- Startup reconcile: lifespan reads active/scheduled intents from DB and
  subscribes their symbols on the (in-memory) quote provider.
- Cancel reconcile: `POST /trade-intents/{id}/cancel` releases the broker
  subscription when no other intent on the same symbol stays active.

Requires PostgreSQL via the `DATABASE_URL` env (same as
`test_intent_repository_integration.py`).
"""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.core import Symbol, TradeIntent
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from app.services.quote.in_memory import InMemoryQuoteProvider
from tests.db_helpers import ensure_user


def _current_trading_date() -> date:
    """The trading_date a freshly created day-intent would carry right now, so the
    startup lifecycle (real clock) never expires the seeded intent."""
    service = TradingSessionService()
    return service.get_day_intent_trading_date(service.now_taipei())


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def int_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    with Session(engine) as session:
        session.add(
            Symbol(
                id=uuid4(),
                symbol="2330",
                display_name="台積電",
                market="TWSE",
                instrument_type="stock",
                tradable_status="tradable",
            )
        )
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def db_session(int_engine: Engine) -> Generator[Session]:
    session = Session(int_engine)
    try:
        # Each test starts with a clean intent table to make startup reconcile deterministic.
        session.execute(delete(TradeIntent))
        session.commit()
        yield session
    finally:
        session.execute(delete(TradeIntent))
        session.commit()
        session.close()


def _seed_intent(session: Session, *, owner_id: UUID, status: str = "active") -> UUID:
    ensure_user(session, owner_id)
    intent_id = uuid4()
    session.add(
        TradeIntent(
            id=intent_id,
            owner_user_id=owner_id,
            symbol="2330",
            strategy="buy_price_alert",
            execution_mode="notify_only",
            quantity_lots=1,
            target_price_original=Decimal("600.0000"),
            target_price_effective=Decimal("600.0000"),
            trigger_reference_price_type="ask",
            trading_date=_current_trading_date(),
            time_in_force="day",
            status=status,
        )
    )
    session.commit()
    return intent_id


@pytest.mark.integration
def test_lifespan_subscribes_active_intents_on_startup(db_session: Session) -> None:
    owner = uuid4()
    _seed_intent(db_session, owner_id=owner)

    app = create_app()
    with TestClient(app):
        provider = app.state.quote_provider
        assert isinstance(provider, InMemoryQuoteProvider)
        assert provider.active_subscriptions() == {"2330"}


@pytest.mark.integration
def test_lifespan_ignores_terminal_intents(db_session: Session) -> None:
    owner = uuid4()
    _seed_intent(db_session, owner_id=owner, status="cancelled")

    app = create_app()
    with TestClient(app):
        provider = app.state.quote_provider
        assert provider.active_subscriptions() == set()


@pytest.mark.integration
def test_cancel_releases_subscription_when_no_peers_remain(db_session: Session) -> None:
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_id=owner)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=owner, role="local")

    with TestClient(app) as client:
        provider = app.state.quote_provider
        assert provider.active_subscriptions() == {"2330"}

        response = client.post(f"/trade-intents/{intent_id}/cancel")
        assert response.status_code == 200
        assert provider.active_subscriptions() == set()


@pytest.mark.integration
def test_cancel_keeps_subscription_when_other_intent_still_active(db_session: Session) -> None:
    owner_a = uuid4()
    owner_b = uuid4()
    intent_a = _seed_intent(db_session, owner_id=owner_a)
    _seed_intent(db_session, owner_id=owner_b)  # peer intent on the same symbol

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=owner_a, role="local")

    with TestClient(app) as client:
        provider = app.state.quote_provider
        assert provider.active_subscriptions() == {"2330"}

        response = client.post(f"/trade-intents/{intent_a}/cancel")
        assert response.status_code == 200
        # Owner B still holds an active intent on the symbol → subscription preserved.
        assert provider.active_subscriptions() == {"2330"}
