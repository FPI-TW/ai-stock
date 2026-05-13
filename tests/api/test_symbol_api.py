from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.symbol_deps import get_symbol_service
from app.api.symbol_errors import UnknownSymbolError
from app.db.models.core import Symbol
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


def test_get_symbols_search(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="2330",
        display_name="台積電",
        market="TWSE",
        instrument_type="stock",
        tradable_status="tradable",
    )
    mock_symbol_service.lookup.return_value = [mock_symbol]

    response = client.get("/symbols?q=2330")

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["symbol"] == "2330"
    assert data[0]["displayName"] == "台積電"
    mock_symbol_service.lookup.assert_called_once_with(q="2330", limit=20)


def test_get_symbol_by_id_success(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="2330",
        display_name="台積電",
        market="TWSE",
        instrument_type="stock",
        tradable_status="tradable",
    )
    mock_symbol_service.find_by_symbol.return_value = mock_symbol

    response = client.get("/symbols/2330")

    assert response.status_code == 200
    assert response.json()["data"]["symbol"] == "2330"
    mock_symbol_service.find_by_symbol.assert_called_once_with("2330")


def test_get_symbol_by_id_not_found(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.find_by_symbol.side_effect = UnknownSymbolError("9999")

    response = client.get("/symbols/9999")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "UNKNOWN_SYMBOL"
    assert body["error"]["details"] == {"symbol": "9999"}
    assert "requestId" in body["error"]
