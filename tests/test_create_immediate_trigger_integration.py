"""Integration tests for immediate-trigger-on-create — BE-V0.5-09 step 7."""

from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_quote_provider, get_trading_session_service
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.core import Notification, Symbol, TriggerEvent
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from app.repositories.intent_repository import IntentRepository
from app.services.quote.base import QuoteSnapshot, QuoteUnavailableError
from app.services.quote.in_memory import InMemoryQuoteProvider

TAIPEI = ZoneInfo("Asia/Taipei")
# Monday inside the regular session.
SESSION_NOW_TAIPEI = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
SESSION_NOW_UTC = SESSION_NOW_TAIPEI.astimezone(UTC)
SESSION_QUOTE_TIME = datetime(2026, 5, 11, 9, 59, 55, tzinfo=TAIPEI)

# Saturday → outside regular session, so create() yields a scheduled intent.
WEEKEND_UTC = datetime(2026, 5, 16, 2, 0, tzinfo=UTC)

OWNER_USER_ID = UUID("00000000-0000-0000-0000-000000000001")  # matches LOCAL_USER_ID in conftest


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def create_engine_module() -> Generator[Engine]:
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
        # Clear intents (incl. market_*) before downgrade so the 202605280004
        # status guard doesn't block `downgrade base`.
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
        engine.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def db_session(create_engine_module: Engine) -> Generator[Session]:
    session = Session(create_engine_module)
    session.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repo(db_session: Session) -> IntentRepository:
    return IntentRepository(db_session)


@pytest.fixture
def quote_provider() -> InMemoryQuoteProvider:
    return InMemoryQuoteProvider()


class CurrentPriceOnlyQuoteProvider(InMemoryQuoteProvider):
    def __init__(self, snapshot: QuoteSnapshot) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.current_price_symbols: list[str] = []

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        raise QuoteUnavailableError(symbols[0])

    def get_current_price(self, symbol: str) -> QuoteSnapshot:
        self.current_price_symbols.append(symbol)
        return self.snapshot


def _build_client(quote_provider: InMemoryQuoteProvider, now_utc: datetime) -> Generator[TestClient]:
    session_with_clock = TradingSessionService(clock=lambda: now_utc)
    app = create_app()
    app.dependency_overrides[get_quote_provider] = lambda: quote_provider
    app.dependency_overrides[get_trading_session_service] = lambda: session_with_clock
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=OWNER_USER_ID, role="user")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client(quote_provider: InMemoryQuoteProvider) -> Generator[TestClient]:
    yield from _build_client(quote_provider, SESSION_NOW_UTC)


@pytest.fixture
def weekend_client(quote_provider: InMemoryQuoteProvider) -> Generator[TestClient]:
    yield from _build_client(quote_provider, WEEKEND_UTC)


def _snapshot(
    *,
    ask: str | None = None,
    bid: str | None = None,
    last: str | None = None,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="2330",
        quote_time=SESSION_QUOTE_TIME,
        received_at=SESSION_QUOTE_TIME,
        ask_price=Decimal(ask) if ask else None,
        bid_price=Decimal(bid) if bid else None,
        last_price=Decimal(last) if last else None,
    )


def _create_payload(target: str = "99.5") -> dict[str, object]:
    return {
        "symbol": "2330",
        "strategy": "buy_price_alert",
        "quantityLots": 1,
        "targetPrice": target,
    }


@pytest.mark.integration
def test_create_inside_session_with_condition_met_triggers_immediately(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    quote_provider.push_quote(_snapshot(ask="99.5"))  # ask == target, condition met

    response = client.post("/trade-intents", json=_create_payload(target="99.5"))

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["status"] == "triggered"

    intent_id = UUID(body["data"]["id"])
    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_price == Decimal("99.5000")
    assert trigger_row.trigger_reference_price_type == "ask"
    assert trigger_row.fallback_used is False
    assert trigger_row.quote_snapshot["ask_price"] == "99.5"

    notification_row = db_session.execute(
        select(Notification).where(Notification.trade_intent_id == intent_id)
    ).scalar_one()
    assert notification_row.type == "price_triggered"


@pytest.mark.integration
def test_create_inside_session_with_condition_not_met_stays_active(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    quote_provider.push_quote(_snapshot(ask="100.0"))  # above target 99.5

    response = client.post("/trade-intents", json=_create_payload(target="99.5"))

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["status"] == "active"

    intent_id = UUID(body["data"]["id"])
    assert db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).first() is None
    assert db_session.execute(select(Notification).where(Notification.trade_intent_id == intent_id)).first() is None


@pytest.mark.integration
def test_create_inside_session_with_quote_unavailable_stays_active(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    # Provider intentionally empty — no quote for 2330.
    response = client.post("/trade-intents", json=_create_payload(target="99.5"))

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["status"] == "active"

    intent_id = UUID(body["data"]["id"])
    assert db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).first() is None


@pytest.mark.integration
def test_create_outside_session_yields_scheduled_intent_without_trigger_attempt(
    db_session: Session,
    weekend_client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    # Even if quote is set, scheduled intents must not consult the provider.
    quote_provider.push_quote(_snapshot(ask="99.5"))

    response = weekend_client.post("/trade-intents", json=_create_payload(target="99.5"))

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["status"] == "scheduled"

    intent_id = UUID(body["data"]["id"])
    assert db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).first() is None


@pytest.mark.integration
def test_create_with_last_fallback_triggers_and_records_metadata(
    db_session: Session,
    client: TestClient,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    # ask absent → evaluator falls back to last_price.
    quote_provider.push_quote(_snapshot(last="99.5"))

    response = client.post("/trade-intents", json=_create_payload(target="99.5"))

    assert response.status_code == 201
    intent_id = UUID(response.json()["data"]["id"])
    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_reference_price_type == "last_fallback"
    assert trigger_row.fallback_used is True


@pytest.mark.integration
def test_market_order_create_triggers_immediately(
    db_session: Session,
    client: TestClient,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    quote_provider.push_quote(_snapshot(ask="600"))

    response = client.post(
        "/trade-intents",
        json={
            "symbol": "2330",
            "strategy": "market_buy_order",
            "quantityLots": 2,
        },
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["strategy"] == "market_buy_order"
    assert body["status"] == "triggered"
    assert body["transactionMode"] == "partial_fill_allowed"
    assert body["targetPriceOriginal"] is None
    assert body["filledQuantityLots"] == 2

    intent_id = UUID(body["id"])
    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.target_price_effective == Decimal("600.0000")
    assert trigger_row.trigger_price == Decimal("600.0000")
    assert trigger_row.trigger_reference_price_type == "ask"

    notification_row = db_session.execute(
        select(Notification).where(Notification.trade_intent_id == intent_id)
    ).scalar_one()
    assert notification_row.type == "market_order_triggered"


@pytest.mark.integration
def test_market_order_create_uses_current_price_snapshot_when_stream_cache_is_cold(
    db_session: Session,
) -> None:
    quote_provider = CurrentPriceOnlyQuoteProvider(_snapshot(ask="600"))
    client_context = _build_client(quote_provider, SESSION_NOW_UTC)
    client = next(client_context)

    try:
        response = client.post(
            "/trade-intents",
            json={
                "symbol": "2330",
                "strategy": "market_buy_order",
                "quantityLots": 2,
            },
        )
    finally:
        client_context.close()

    assert response.status_code == 201
    body = response.json()["data"]
    intent_id = UUID(body["id"])
    assert body["status"] == "triggered"
    # Market orders carry no target price (the target_price_presence CHECK forbids it);
    # the execution price lives on the TriggerEvent, asserted below.
    assert body["targetPriceEffective"] is None
    assert body["filledQuantityLots"] == 2
    assert quote_provider.current_price_symbols == ["2330"]
    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_price == Decimal("600.0000")
    assert trigger_row.target_price_effective == Decimal("600.0000")
