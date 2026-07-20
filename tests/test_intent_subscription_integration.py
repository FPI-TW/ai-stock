"""Integration test for lifespan-driven quote subscription reconcile.

Covers:
- Startup reconcile: lifespan reads active/scheduled intents from DB and
  subscribes their symbols on the (in-memory) quote provider. 兩軌各一案例
  （模式丙：新軌 symbol 沒訂到 → robot #2 收不到報價 → 靜默不觸發）。
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

from app.api.deps import get_active_user, get_current_user, get_idempotency_key
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.core import Symbol, TradeIntent
from app.db.models.trade_intent_core import TradeIntentCore, TradeIntentPriceParams
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
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


def _wipe_intents(session: Session) -> None:
    session.execute(delete(TradeIntent))
    # 新軌：衛星先於核心（FK）。啟動 reconcile 掃兩張表 → 殘留任一軌都會污染訂閱集斷言。
    session.execute(delete(TradeIntentPriceParams))
    session.execute(delete(TradeIntentCore))
    session.commit()


@pytest.fixture
def db_session(int_engine: Engine) -> Generator[Session]:
    session = Session(int_engine)
    try:
        # Each test starts with a clean intent table to make startup reconcile deterministic.
        _wipe_intents(session)
        yield session
    finally:
        _wipe_intents(session)
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


def _seed_core_intent(session: Session, *, owner_id: UUID, symbol: str = "2330", status: str = "active") -> UUID:
    """新軌（trade_intent_core）建單，走 repo 以帶齊 dedup_key / 衛星列。"""
    ensure_user(session, owner_id)
    repo = TradeIntentCoreRepository(session)
    intent_id = repo.create(
        owner_user_id=owner_id,
        symbol=symbol,
        strategy="buy_price_alert",
        quantity_lots=1,
        target_price_original=Decimal("600.0000"),
        target_price_effective=Decimal("600.0000"),
        trigger_reference_price_type="ask",
        trading_date=_current_trading_date(),
        time_in_force="day",
        execution_mode="notify_only",
        status=status,
    )
    repo.commit()
    return intent_id


@pytest.mark.integration
def test_lifespan_subscribes_core_track_intents_on_startup(db_session: Session) -> None:
    """新軌單的 symbol 也要在啟動時訂到，否則 robot #2 收不到報價 → 整條新軌靜默不觸發。"""
    _seed_core_intent(db_session, owner_id=uuid4())

    app = create_app()
    with TestClient(app):
        provider = app.state.quote_provider
        assert provider.active_subscriptions() == {"2330"}


@pytest.mark.integration
def test_lifespan_ignores_terminal_core_track_intents(db_session: Session) -> None:
    _seed_core_intent(db_session, owner_id=uuid4(), status="cancelled")

    app = create_app()
    with TestClient(app):
        provider = app.state.quote_provider
        assert provider.active_subscriptions() == set()


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
    # T1 cutover 後 cancel endpoint 讀新軌，故單子種在 trade_intent_core
    owner = uuid4()
    intent_id = _seed_core_intent(db_session, owner_id=owner)

    app = create_app()
    principal = RequestUser(user_id=owner, role="local")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    app.dependency_overrides[get_idempotency_key] = lambda: str(uuid4())

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
    intent_a = _seed_core_intent(db_session, owner_id=owner_a)
    _seed_core_intent(db_session, owner_id=owner_b)  # peer intent on the same symbol

    app = create_app()
    principal = RequestUser(user_id=owner_a, role="local")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    app.dependency_overrides[get_idempotency_key] = lambda: str(uuid4())

    with TestClient(app) as client:
        provider = app.state.quote_provider
        assert provider.active_subscriptions() == {"2330"}

        response = client.post(f"/trade-intents/{intent_a}/cancel")
        assert response.status_code == 200
        # Owner B still holds an active intent on the symbol → subscription preserved.
        assert provider.active_subscriptions() == {"2330"}
