"""Tests for GET /quotes batch read endpoint."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_quote_provider, get_symbol_service
from app.db.models.core import Symbol
from app.domain.symbol_errors import UnknownSymbolError
from app.main import create_app
from app.services.quote.base import QuoteSnapshot
from app.services.quote.in_memory import InMemoryQuoteProvider
from app.services.quote_lookup import MAX_SYMBOLS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _push_quote(
    provider: InMemoryQuoteProvider,
    symbol: str,
    ask: str = "599.00",
    bid: str = "598.50",
    last: str = "599.00",
) -> None:
    """Seed the InMemoryQuoteProvider with a quote snapshot."""
    provider.push_quote(
        QuoteSnapshot(
            symbol=symbol,
            ask_price=Decimal(ask),
            bid_price=Decimal(bid),
            last_price=Decimal(last),
            quote_time=datetime(2026, 5, 27, 1, 30, tzinfo=UTC),
            received_at=datetime(2026, 5, 27, 1, 30, tzinfo=UTC),
        )
    )


def _mock_symbol(symbol: str, display_name: str) -> Symbol:
    return Symbol(
        symbol=symbol,
        display_name=display_name,
        market="TWSE",
        instrument_type="stock",
        tradable_status="tradable",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_quotes_single_symbol_with_snapshot(
    client: TestClient,
    mock_symbol_service: MagicMock,
    in_memory_quote_provider: InMemoryQuoteProvider,
) -> None:
    mock_symbol_service.get_by_symbol.return_value = _mock_symbol("2330", "台積電")
    _push_quote(in_memory_quote_provider, "2330")

    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["symbol"] == "2330"
    assert row["askPrice"] == "599.00"
    assert row["bidPrice"] == "598.50"
    assert row["lastPrice"] == "599.00"
    assert row["stale"] is False
    assert row["displayName"] == "台積電"


def test_get_quotes_multiple_symbols_preserve_order(
    client: TestClient,
    mock_symbol_service: MagicMock,
    in_memory_quote_provider: InMemoryQuoteProvider,
) -> None:
    mock_symbol_service.get_by_symbol.side_effect = lambda s: _mock_symbol(s, {"2330": "台積電", "2317": "鴻海"}[s])
    _push_quote(in_memory_quote_provider, "2330", ask="599")
    _push_quote(in_memory_quote_provider, "2317", ask="205.5")

    response = client.get("/quotes", params={"symbols": "2317,2330"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert [r["symbol"] for r in body["data"]] == ["2317", "2330"]


def test_get_quotes_unseeded_symbol_returns_stale(
    client: TestClient,
    mock_symbol_service: MagicMock,
    in_memory_quote_provider: InMemoryQuoteProvider,
) -> None:
    mock_symbol_service.get_by_symbol.return_value = _mock_symbol("2330", "台積電")
    # Do NOT push any quote — provider has nothing for 2330.

    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
    row = response.json()["data"][0]
    assert row["symbol"] == "2330"
    assert row["stale"] is True
    assert row["displayName"] == "台積電"
    assert row["askPrice"] is None
    assert row["bidPrice"] is None
    assert row["lastPrice"] is None
    assert row["quoteTime"] is None


def test_get_quotes_missing_symbols_param_returns_400(
    client: TestClient,
) -> None:
    response = client.get("/quotes")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "MISSING_SYMBOLS"


def test_get_quotes_empty_symbols_param_returns_400(
    client: TestClient,
) -> None:
    response = client.get("/quotes", params={"symbols": " "})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "MISSING_SYMBOLS"


def test_get_quotes_too_many_symbols_returns_400(
    client: TestClient,
) -> None:
    too_many = ",".join(f"99{i:02d}" for i in range(MAX_SYMBOLS + 1))
    response = client.get("/quotes", params={"symbols": too_many})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["error"]["code"] == "TOO_MANY_SYMBOLS"
    assert body["error"]["details"]["limit"] == MAX_SYMBOLS
    assert body["error"]["details"]["received"] == MAX_SYMBOLS + 1


def test_get_quotes_unknown_symbol_returns_404(
    client: TestClient,
    mock_symbol_service: MagicMock,
) -> None:
    mock_symbol_service.get_by_symbol.side_effect = UnknownSymbolError("ZZZZ")

    response = client.get("/quotes", params={"symbols": "ZZZZ"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["error"]["code"] == "UNKNOWN_SYMBOL"
    assert body["error"]["details"]["symbol"] == "ZZZZ"


def test_get_quotes_mixed_valid_and_unknown_returns_404(
    client: TestClient,
    mock_symbol_service: MagicMock,
    in_memory_quote_provider: InMemoryQuoteProvider,
) -> None:
    """Any single unknown symbol in the batch fails the whole request (per spec §3.3)."""
    _push_quote(in_memory_quote_provider, "2330")

    def lookup(symbol: str) -> Symbol:
        if symbol == "ZZZZ":
            raise UnknownSymbolError("ZZZZ")
        return _mock_symbol(symbol, "台積電")

    mock_symbol_service.get_by_symbol.side_effect = lookup
    response = client.get("/quotes", params={"symbols": "2330,ZZZZ"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "UNKNOWN_SYMBOL"


def test_get_quotes_works_in_production_mode(
    monkeypatch: pytest.MonkeyPatch,
    mock_symbol_service: MagicMock,
    in_memory_quote_provider: InMemoryQuoteProvider,
) -> None:
    """Spec: /quotes is NOT gated behind LOCAL_MODE; route works even when off."""
    monkeypatch.setenv("LOCAL_MODE", "false")
    mock_symbol_service.get_by_symbol.return_value = _mock_symbol("2330", "台積電")
    _push_quote(in_memory_quote_provider, "2330")

    app = create_app()
    app.dependency_overrides[get_symbol_service] = lambda: mock_symbol_service
    app.dependency_overrides[get_quote_provider] = lambda: in_memory_quote_provider
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/quotes", params={"symbols": "2330"})
            assert response.status_code == status.HTTP_200_OK
    finally:
        app.dependency_overrides.clear()
