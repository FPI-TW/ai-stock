"""Tests for GET /quotes batch read endpoint."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from tests.conftest import ClientFactory

from app.services.quote.base import QuoteSnapshot


def _push_quote(
    client: TestClient,
    symbol: str,
    ask: str = "599.00",
    bid: str = "598.50",
    last: str = "599.00",
) -> None:
    """Seed the InMemoryQuoteProvider directly via app.state."""
    provider = cast(FastAPI, client.app).state.quote_provider
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


def test_get_quotes_single_symbol_with_snapshot(client_factory: ClientFactory) -> None:
    client = client_factory()
    _push_quote(client, "2330")
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
    assert row["displayName"]  # registry should have something


def test_get_quotes_multiple_symbols_preserve_order(client_factory: ClientFactory) -> None:
    client = client_factory()
    _push_quote(client, "2330", ask="599")
    _push_quote(client, "2317", ask="205.5")
    response = client.get("/quotes", params={"symbols": "2317,2330"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert [r["symbol"] for r in body["data"]] == ["2317", "2330"]


def test_get_quotes_unseeded_symbol_returns_stale(client_factory: ClientFactory) -> None:
    client = client_factory()
    # Do NOT push any quote — provider has nothing for 2330.
    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
    row = response.json()["data"][0]
    assert row["symbol"] == "2330"
    assert row["stale"] is True
    assert row["askPrice"] is None
    assert row["bidPrice"] is None
    assert row["lastPrice"] is None
    assert row["quoteTime"] is None


def test_get_quotes_missing_symbols_param_returns_400(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/quotes")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "MISSING_SYMBOLS"


def test_get_quotes_empty_symbols_param_returns_400(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/quotes", params={"symbols": " "})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "MISSING_SYMBOLS"


def test_get_quotes_too_many_symbols_returns_400(client_factory: ClientFactory) -> None:
    client = client_factory()
    too_many = ",".join(f"99{i:02d}" for i in range(51))
    response = client.get("/quotes", params={"symbols": too_many})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["error"]["code"] == "TOO_MANY_SYMBOLS"
    assert body["error"]["details"]["limit"] == 50
    assert body["error"]["details"]["received"] == 51


def test_get_quotes_unknown_symbol_returns_404(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/quotes", params={"symbols": "ZZZZ"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["error"]["code"] == "UNKNOWN_SYMBOL"
    assert body["error"]["details"]["symbol"] == "ZZZZ"


def test_get_quotes_works_in_production_mode(client_factory: ClientFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec: /quotes is NOT gated behind LOCAL_MODE; should 200 even when off."""
    monkeypatch.setenv("LOCAL_MODE", "false")
    client = client_factory()
    _push_quote(client, "2330")
    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
