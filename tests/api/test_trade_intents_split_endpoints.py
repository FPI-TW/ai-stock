"""API tests for the per-strategy create endpoints (POST /trade-intents/<strategy>).

These cover the split endpoints added alongside the legacy POST /trade-intents.
The legacy endpoint and its tests in test_trade_intents.py stay untouched.

Each test asserts the endpoint builds the right CreateTradeIntentInput (no
discriminated-union getattr), and that the typed request model rejects bad bodies.
"""

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import (
    enforce_mutation_rate_limit,
    get_active_user,
    get_create_trade_intent_command,
    get_current_user,
    get_idempotency_key,
    get_idempotency_manager,
    get_intent_repository,
)
from app.commands.trade_intent import CreateTradeIntentInput
from app.core.security import RequestUser
from app.domain.trade_intent import TradeIntentData
from app.main import create_app

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class _PassthroughIdempotency:
    def run(self, *, execute: object, **_: object) -> object:
        return execute()  # type: ignore[operator]


def _make_intent(
    *,
    strategy: str,
    quantity_lots: int = 1,
    price: str | None = "600",
    trail_mode: str | None = None,
    trail_value: Decimal | None = None,
    transaction_mode: str = "single_notification",
) -> TradeIntentData:
    now = datetime.now(tz=UTC)
    return TradeIntentData(
        id=uuid4(),
        owner_user_id=_OWNER_ID,
        symbol="2330",
        strategy=strategy,
        execution_mode="notify_only",
        quantity_lots=quantity_lots,
        target_price_original=Decimal(price) if price is not None else None,
        target_price_effective=Decimal(price) if price is not None else None,
        trigger_reference_price_type="ask",
        trading_date=date.today(),
        time_in_force="day",
        status="active",
        created_at=now,
        updated_at=now,
        trail_mode=trail_mode,
        trail_value=trail_value,
        transaction_mode=transaction_mode,
    )


@pytest.fixture
def mock_create_command() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(mock_create_command: MagicMock) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_create_trade_intent_command] = lambda: mock_create_command
    app.dependency_overrides[enforce_mutation_rate_limit] = lambda: None
    app.dependency_overrides[get_idempotency_key] = lambda: "test-idempotency-key"
    app.dependency_overrides[get_idempotency_manager] = lambda: _PassthroughIdempotency()
    app.dependency_overrides[get_intent_repository] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=_OWNER_ID, role="user")
    app.dependency_overrides[get_active_user] = lambda: RequestUser(user_id=_OWNER_ID, role="user")
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sent_input(mock_create_command: MagicMock) -> CreateTradeIntentInput:
    inp = mock_create_command.execute.call_args.args[0]
    assert isinstance(inp, CreateTradeIntentInput)
    return inp


def test_buy_price_alert_builds_input(client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="buy_price_alert")

    resp = client.post(
        "/trade-intents/buy-price-alert",
        json={"symbol": "2330", "strategy": "buy_price_alert", "quantityLots": 1, "targetPrice": "600"},
    )

    assert resp.status_code == status.HTTP_201_CREATED
    inp = _sent_input(mock_create_command)
    assert inp.strategy == "buy_price_alert"
    assert inp.symbol == "2330"
    assert inp.quantity_lots == 1
    assert inp.target_price == "600"
    assert inp.owner_user_id == _OWNER_ID
    # alert carries no transaction/notification overrides -> command defaults apply.
    assert inp.transaction_mode == "single_notification"
    assert inp.trail_mode is None


def test_sell_price_alert_builds_input(client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="sell_price_alert", quantity_lots=2, price="650")

    resp = client.post(
        "/trade-intents/sell-price-alert",
        json={"symbol": "2330", "strategy": "sell_price_alert", "quantityLots": 2, "targetPrice": "650"},
    )

    assert resp.status_code == status.HTTP_201_CREATED
    inp = _sent_input(mock_create_command)
    assert inp.strategy == "sell_price_alert"
    assert inp.quantity_lots == 2
    assert inp.target_price == "650"


def test_limit_buy_order_builds_input(client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="limit_buy_order")

    resp = client.post(
        "/trade-intents/limit-buy-order",
        json={
            "symbol": "2330",
            "strategy": "limit_buy_order",
            "quantityLots": 1,
            "targetPrice": "600",
            "transactionMode": "partial_fill_allowed",
            "notificationMode": "single",
        },
    )

    assert resp.status_code == status.HTTP_201_CREATED
    inp = _sent_input(mock_create_command)
    assert inp.strategy == "limit_buy_order"
    assert inp.target_price == "600"
    assert inp.transaction_mode == "partial_fill_allowed"
    assert inp.notification_mode == "single"


def test_limit_sell_order_builds_input(client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="limit_sell_order")

    resp = client.post(
        "/trade-intents/limit-sell-order",
        json={
            "symbol": "2330",
            "strategy": "limit_sell_order",
            "quantityLots": 1,
            "targetPrice": "640",
        },
    )

    assert resp.status_code == status.HTTP_201_CREATED
    inp = _sent_input(mock_create_command)
    assert inp.strategy == "limit_sell_order"
    assert inp.target_price == "640"
    # transactionMode omitted -> model default.
    assert inp.transaction_mode == "single_notification"


def test_market_order_builds_input(client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="market_buy_order", price=None)

    resp = client.post(
        "/trade-intents/market-order",
        json={"symbol": "2330", "strategy": "market_buy_order", "quantityLots": 1},
    )

    assert resp.status_code == status.HTTP_201_CREATED
    inp = _sent_input(mock_create_command)
    assert inp.strategy == "market_buy_order"
    assert inp.target_price is None
    # market default transaction mode comes from the request model.
    assert inp.transaction_mode == "partial_fill_allowed"


def test_trailing_stop_alert_builds_input(client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(
        strategy="trailing_stop_alert", price=None, trail_mode="percentage", trail_value=Decimal("5.0")
    )

    resp = client.post(
        "/trade-intents/trailing-stop-alert",
        json={
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "quantityLots": 1,
            "trailMode": "percentage",
            "trailValue": "5.0",
        },
    )

    assert resp.status_code == status.HTTP_201_CREATED
    inp = _sent_input(mock_create_command)
    assert inp.strategy == "trailing_stop_alert"
    assert inp.trail_mode == "percentage"
    assert inp.trail_value == Decimal("5.0")
    assert inp.target_price is None


def test_buy_price_alert_rejects_wrong_strategy(client: TestClient, mock_create_command: MagicMock) -> None:
    # The typed model pins strategy to its own literal; a mismatched body is 422.
    resp = client.post(
        "/trade-intents/buy-price-alert",
        json={"symbol": "2330", "strategy": "sell_price_alert", "quantityLots": 1, "targetPrice": "600"},
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    mock_create_command.execute.assert_not_called()


def test_buy_price_alert_requires_target_price(client: TestClient, mock_create_command: MagicMock) -> None:
    resp = client.post(
        "/trade-intents/buy-price-alert",
        json={"symbol": "2330", "strategy": "buy_price_alert", "quantityLots": 1},
    )

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    mock_create_command.execute.assert_not_called()
