from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_quote_provider
from app.core.config import get_settings
from app.main import create_app
from app.services.quote.base import QuoteListener, QuoteSnapshot

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")


class FakeCurrentPriceProvider:
    def __init__(self, snapshot: QuoteSnapshot) -> None:
        self.snapshot = snapshot
        self.requested_symbols: list[str] = []

    def get_current_price(self, symbol: str) -> QuoteSnapshot:
        self.requested_symbols.append(symbol)
        return self.snapshot

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        return [self.snapshot for _ in symbols]

    def subscribe(self, symbol: str) -> None:
        return None

    def unsubscribe(self, symbol: str) -> None:
        return None

    def active_subscriptions(self) -> set[str]:
        return set()

    def startup(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def add_quote_listener(self, listener: QuoteListener) -> None:
        return None

    def remove_quote_listener(self, listener: QuoteListener) -> None:
        return None


def _snapshot(symbol: str = "2330") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=Decimal("590"),
        ask_price=Decimal("591"),
        last_price=Decimal("590.5"),
        quote_time=datetime(2026, 5, 26, 10, 30, tzinfo=TAIPEI),
        received_at=datetime(2026, 5, 26, 2, 30, 1, tzinfo=UTC),
    )


def _client(monkeypatch: pytest.MonkeyPatch, provider: FakeCurrentPriceProvider) -> TestClient:
    monkeypatch.setenv("QUOTE_PROVIDER", "shioaji_demo")
    monkeypatch.setenv("SHIOAJI_API_KEY", "dummy")
    monkeypatch.setenv("SHIOAJI_SECRET_KEY", "dummy")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_quote_provider] = lambda: provider
    return TestClient(app, raise_server_exceptions=False)


def test_current_price_returns_shioaji_snapshot_for_allowed_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeCurrentPriceProvider(_snapshot())
    client = _client(monkeypatch, provider)

    response = client.get("/quotes/current-price/2330")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "data": {
            "symbol": "2330",
            "currentPrice": "590.50",
            "bidPrice": "590.00",
            "askPrice": "591.00",
            "quoteTime": "2026-05-26T10:30:00+08:00",
            "receivedAt": "2026-05-26T02:30:01Z",
            "source": "shioaji",
            "testFeature": True,
        }
    }
    assert provider.requested_symbols == ["2330"]


def test_current_price_rejects_symbol_outside_test_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, FakeCurrentPriceProvider(_snapshot()))

    response = client.get("/quotes/current-price/1101")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "CURRENT_PRICE_SYMBOL_NOT_ALLOWED"
    assert body["error"]["details"] == {"symbol": "1101", "allowed": ["0050", "00878", "2317", "2330"]}


def test_current_price_rejects_symbol_before_provider_check(client_factory: Callable[[], TestClient]) -> None:
    response = client_factory().get("/quotes/current-price/2230")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "CURRENT_PRICE_SYMBOL_NOT_ALLOWED"
    assert body["error"]["details"] == {"symbol": "2230", "allowed": ["0050", "00878", "2317", "2330"]}


def test_current_price_requires_shioaji_provider(client_factory: Callable[[], TestClient]) -> None:
    response = client_factory().get("/quotes/current-price/2330")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    body = response.json()
    assert body["error"]["code"] == "QUOTE_PROVIDER_UNAVAILABLE"
    assert body["error"]["details"] == {
        "requiredProvider": "shioaji_demo",
        "currentProvider": "in_memory",
    }
