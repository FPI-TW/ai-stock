from unittest.mock import MagicMock

from fastapi import status
from fastapi.testclient import TestClient

from app.domain.symbol_errors import SymbolNotTradableError, UnknownSymbolError


def test_create_intent_success(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.return_value = MagicMock()

    response = client.post(
        "/intents",
        json={"symbol": "2330", "side": "buy", "quantity": 1000},
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["data"]["symbol"] == "2330"
    mock_symbol_service.get_tradable_symbol.assert_called_once_with("2330")


def test_create_intent_unknown_symbol(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.side_effect = UnknownSymbolError("0000")

    response = client.post(
        "/intents",
        json={"symbol": "0000", "side": "buy", "quantity": 1000},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "UNKNOWN_SYMBOL"


def test_create_intent_halted_symbol(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.side_effect = SymbolNotTradableError("9999", "halted")

    response = client.post(
        "/intents",
        json={"symbol": "9999", "side": "buy", "quantity": 1000},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "SYMBOL_NOT_TRADABLE"
