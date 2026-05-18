from unittest.mock import MagicMock

from fastapi import status
from fastapi.testclient import TestClient

from app.db.models.core import Symbol
from app.domain.symbol_errors import UnknownSymbolError


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

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["symbol"] == "2330"
    assert data[0]["displayName"] == "台積電"
    mock_symbol_service.lookup.assert_called_once_with(q="2330", limit=20)


def test_get_symbols_without_query_uses_default_limit(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.lookup.return_value = []

    response = client.get("/symbols")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"data": []}
    mock_symbol_service.lookup.assert_called_once_with(q=None, limit=20)


def test_get_symbols_limit_at_max(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.lookup.return_value = []

    response = client.get("/symbols?limit=50")

    assert response.status_code == status.HTTP_200_OK
    mock_symbol_service.lookup.assert_called_once_with(q=None, limit=50)


def test_get_symbols_limit_above_max_rejected(client: TestClient, mock_symbol_service: MagicMock) -> None:
    response = client.get("/symbols?limit=51")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    mock_symbol_service.lookup.assert_not_called()


def test_get_symbols_limit_below_min_rejected(client: TestClient, mock_symbol_service: MagicMock) -> None:
    response = client.get("/symbols?limit=0")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    mock_symbol_service.lookup.assert_not_called()


def test_get_symbol_by_id_success(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol = Symbol(
        symbol="2330",
        display_name="台積電",
        market="TWSE",
        instrument_type="stock",
        tradable_status="tradable",
    )
    mock_symbol_service.get_by_symbol.return_value = mock_symbol

    response = client.get("/symbols/2330")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["symbol"] == "2330"
    mock_symbol_service.get_by_symbol.assert_called_once_with("2330")


def test_get_symbol_by_id_not_found(client: TestClient, mock_symbol_service: MagicMock) -> None:
    mock_symbol_service.get_by_symbol.side_effect = UnknownSymbolError("9999")

    response = client.get("/symbols/9999")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["error"]["code"] == "UNKNOWN_SYMBOL"
    assert body["error"]["details"] == {"symbol": "9999"}
    assert "requestId" in body["error"]
