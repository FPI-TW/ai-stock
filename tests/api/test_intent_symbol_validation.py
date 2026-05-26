"""Tests that symbol validation errors propagate correctly to the create-intent endpoint."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import status
from fastapi.testclient import TestClient

from app.domain.symbol_errors import SymbolNotTradableError, UnknownSymbolError

_VALID_PAYLOAD = {
    "symbol": "2330",
    "strategy": "buy_price_alert",
    "quantityLots": 1,
    "targetPrice": "600",
}


def _make_intent_mock(symbol: str = "2330") -> MagicMock:
    intent = MagicMock()
    intent.id = uuid4()
    intent.symbol = symbol
    intent.strategy = "buy_price_alert"
    intent.quantity_lots = 1
    intent.target_price_original = Decimal("600")
    intent.target_price_effective = Decimal("600")
    intent.trading_date = date.today()
    intent.time_in_force = "day"
    intent.execution_mode = "notify_only"
    intent.status = "active"
    intent.cancelled_at = None
    intent.transaction_mode = "single_notification"
    intent.notification_mode = "single"
    intent.filled_quantity_lots = 0
    intent.last_fill_at = None
    intent.trail_mode = None
    intent.trail_value = None
    intent.baseline = None
    intent.dynamic_trigger_price = None
    intent.baseline_updated_at = None
    return intent


def test_create_intent_success(
    client: TestClient, mock_symbol_service: MagicMock, mock_intent_repository: MagicMock
) -> None:
    mock_symbol_service.get_tradable_symbol.return_value = MagicMock(instrument_type="stock")
    intent_mock = _make_intent_mock()
    mock_intent_repository.create.return_value = intent_mock.id
    mock_intent_repository.find_by_id.return_value = intent_mock

    response = client.post("/trade-intents", json=_VALID_PAYLOAD)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["data"]["symbol"] == "2330"
    mock_symbol_service.get_tradable_symbol.assert_called_once_with("2330")


def test_create_intent_unknown_symbol(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.side_effect = UnknownSymbolError("0000")

    response = client.post("/trade-intents", json={**_VALID_PAYLOAD, "symbol": "0000"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "UNKNOWN_SYMBOL"


def test_create_intent_unsupported_symbol(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.side_effect = SymbolNotTradableError("9999", "halted")

    response = client.post("/trade-intents", json={**_VALID_PAYLOAD, "symbol": "9999"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "SYMBOL_NOT_TRADABLE"
