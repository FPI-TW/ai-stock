from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.symbol_deps import get_symbol_service
from app.api.symbol_errors import SymbolNotTradableError, UnknownSymbolError
from app.symbol_main import create_symbol_app


@pytest.fixture
def mock_symbol_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(mock_symbol_service: MagicMock) -> Generator[TestClient]:
    app = create_symbol_app()
    app.dependency_overrides[get_symbol_service] = lambda: mock_symbol_service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_intent_success(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.return_value = MagicMock()

    response = client.post(
        "/intents",
        json={"symbol": "2330", "side": "buy", "quantity": 1000},
    )

    assert response.status_code == 201
    assert response.json()["data"]["symbol"] == "2330"
    mock_symbol_service.get_tradable_symbol.assert_called_once_with("2330")


def test_create_intent_unknown_symbol(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.side_effect = UnknownSymbolError("0000")

    response = client.post(
        "/intents",
        json={"symbol": "0000", "side": "buy", "quantity": 1000},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_SYMBOL"


def test_create_intent_halted_symbol(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_tradable_symbol.side_effect = SymbolNotTradableError("9999", "halted")

    response = client.post(
        "/intents",
        json={"symbol": "9999", "side": "buy", "quantity": 1000},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SYMBOL_NOT_TRADABLE"
