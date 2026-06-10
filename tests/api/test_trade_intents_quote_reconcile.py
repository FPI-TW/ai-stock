"""End-to-end API tests for quote-provider reconcile during intent CRUD.

Wires together: real `CreateTradeIntentCommand` / `CancelTradeIntentCommand`,
real `ShioajiQuoteProvider` (with a MagicMock SDK client so the allowlist and
quota rules apply without touching the network), real exception handlers.
The repository is mocked so we don't need a Postgres for these scenarios.
"""

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import (
    get_active_user,
    get_current_user,
    get_db,
    get_intent_repository,
    get_quote_provider,
    get_symbol_service,
)
from app.core.security import RequestUser
from app.domain.trade_intent import TradeIntentData
from app.main import create_app
from app.services.quote.shioaji_demo.provider import ShioajiQuoteProvider

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _tradable_symbol(symbol: str) -> MagicMock:
    obj = MagicMock()
    obj.symbol = symbol
    obj.instrument_type = "stock"
    obj.tradable_status = "tradable"
    return obj


def _intent(symbol: str, *, target_price: str = "600", id: UUID | None = None) -> TradeIntentData:
    now = datetime.now(tz=UTC)
    return TradeIntentData(
        id=id or uuid4(),
        owner_user_id=_OWNER_ID,
        symbol=symbol,
        strategy="buy_price_alert",
        execution_mode="notify_only",
        quantity_lots=1,
        target_price_original=Decimal(target_price),
        target_price_effective=Decimal(target_price),
        trigger_reference_price_type="ask",
        trading_date=date.today(),
        time_in_force="day",
        status="active",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def shioaji_provider() -> ShioajiQuoteProvider:
    """Real Shioaji provider gated on a MagicMock SDK client.

    The mock client absorbs `.subscribe` / `.unsubscribe` / `.logout` calls without
    contacting the network, but the provider's own allowlist + quota logic is
    unchanged from production.
    """

    provider = ShioajiQuoteProvider(
        client=MagicMock(),
        allowed_symbols=frozenset({"2330", "2317", "0050", "00878"}),
        max_subscriptions=5,
    )
    provider.mark_started_for_tests()  # skip the SDK login path
    return provider


@pytest.fixture
def mock_symbol_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_intent_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(
    shioaji_provider: ShioajiQuoteProvider,
    mock_symbol_service: MagicMock,
    mock_intent_repo: MagicMock,
) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_quote_provider] = lambda: shioaji_provider
    app.dependency_overrides[get_symbol_service] = lambda: mock_symbol_service
    app.dependency_overrides[get_intent_repository] = lambda: mock_intent_repo
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=_OWNER_ID, role="user")
    app.dependency_overrides[get_active_user] = lambda: RequestUser(user_id=_OWNER_ID, role="user")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(symbol: str = "2330", price: str = "600") -> dict[str, object]:
    return {
        "symbol": symbol,
        "strategy": "buy_price_alert",
        "quantityLots": 1,
        "targetPrice": price,
    }


# ----------------------------------------------------------------------
# Whitelist enforcement on POST /trade-intents
# ----------------------------------------------------------------------


def test_create_intent_with_non_demo_symbol_returns_422(
    client: TestClient,
    mock_symbol_service: MagicMock,
    mock_intent_repo: MagicMock,
    shioaji_provider: ShioajiQuoteProvider,
) -> None:
    mock_symbol_service.get_tradable_symbol.return_value = _tradable_symbol("1101")
    intent_1101 = _intent("1101")
    mock_intent_repo.create.return_value = intent_1101.id
    mock_intent_repo.find_by_id.return_value = intent_1101

    response = client.post("/trade-intents", json=_payload(symbol="1101"))

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()["error"]
    assert body["code"] == "SYMBOL_NOT_AVAILABLE_IN_DEMO"
    assert body["details"]["symbol"] == "1101"
    # The provider's allowlist is what gates this; the symbol must NOT be subscribed.
    assert "1101" not in shioaji_provider.active_subscriptions()


# ----------------------------------------------------------------------
# Quota enforcement on POST /trade-intents
# ----------------------------------------------------------------------


def test_create_intent_beyond_5_subscriptions_returns_409(
    client: TestClient,
    mock_symbol_service: MagicMock,
    mock_intent_repo: MagicMock,
    shioaji_provider: ShioajiQuoteProvider,
) -> None:
    shioaji_provider._allowed = frozenset(  # noqa: SLF001
        {"2330", "2317", "0050", "00878", "2603", "6505"}
    )
    # Saturate the quota with 5 unrelated subscriptions ahead of time.
    for symbol in ("2330", "2317", "0050", "00878", "2603"):
        shioaji_provider.subscribe(symbol)

    # Now ask the API to create an intent on a 6th allowed symbol — quota should reject.
    mock_symbol_service.get_tradable_symbol.return_value = _tradable_symbol("6505")
    intent_6505 = _intent("6505")
    mock_intent_repo.create.return_value = intent_6505.id
    mock_intent_repo.find_by_id.return_value = intent_6505

    response = client.post("/trade-intents", json=_payload(symbol="6505"))

    assert response.status_code == status.HTTP_409_CONFLICT
    body = response.json()["error"]
    assert body["code"] == "QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED"
    assert body["details"]["symbol"] == "6505"
    assert body["details"]["limit"] == 5
    assert "6505" not in shioaji_provider.active_subscriptions()


# ----------------------------------------------------------------------
# Cancel reconcile path
# ----------------------------------------------------------------------


def test_cancel_intent_unsubscribes_when_no_other_intent_holds_symbol(
    client: TestClient,
    mock_intent_repo: MagicMock,
    shioaji_provider: ShioajiQuoteProvider,
) -> None:
    intent_id = uuid4()
    shioaji_provider.subscribe("2330")
    mock_intent_repo.cancel.return_value = _intent("2330", id=intent_id)
    mock_intent_repo.count_active_or_scheduled_for_symbol.return_value = 0

    response = client.post(f"/trade-intents/{intent_id}/cancel")

    assert response.status_code == status.HTTP_200_OK
    assert "2330" not in shioaji_provider.active_subscriptions()


def test_cancel_intent_keeps_subscription_when_peers_still_active(
    client: TestClient,
    mock_intent_repo: MagicMock,
    shioaji_provider: ShioajiQuoteProvider,
) -> None:
    intent_id = uuid4()
    shioaji_provider.subscribe("2330")
    mock_intent_repo.cancel.return_value = _intent("2330", id=intent_id)
    mock_intent_repo.count_active_or_scheduled_for_symbol.return_value = 2

    response = client.post(f"/trade-intents/{intent_id}/cancel")

    assert response.status_code == status.HTTP_200_OK
    # Peers still want the symbol; the broker subscription stays put.
    assert "2330" in shioaji_provider.active_subscriptions()


def test_cancel_intent_returns_200_when_post_cancel_count_query_fails(
    client: TestClient,
    mock_intent_repo: MagicMock,
    shioaji_provider: ShioajiQuoteProvider,
) -> None:
    """Regression for PR #12 r3272105995.

    Cancel SQL has already committed when the residual-count query blows up.
    The cancel API must still return 200 — any stale broker subscription will
    be reaped by the next startup reconcile, not by a 500 to the client.
    """
    intent_id = uuid4()
    shioaji_provider.subscribe("2330")
    mock_intent_repo.cancel.return_value = _intent("2330", id=intent_id)
    mock_intent_repo.count_active_or_scheduled_for_symbol.side_effect = SQLAlchemyError("boom")

    response = client.post(f"/trade-intents/{intent_id}/cancel")

    assert response.status_code == status.HTTP_200_OK
    # Cleanup deferred to startup reconciler; the subscription stays put for now.
    assert "2330" in shioaji_provider.active_subscriptions()
