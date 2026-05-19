"""DevQuoteIngestService.get_snapshot() 單元測試。"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.domain.quote import QuoteValidator
from app.domain.trading_session import TradingSessionService
from app.services.quote import (
    DevelopmentQuoteProvider,
    DevQuoteIngestService,
    InMemoryQuoteStore,
)

TAIPEI = ZoneInfo("Asia/Taipei")
MON_10AM = datetime(2026, 5, 19, 10, 0, tzinfo=TAIPEI)


@pytest.fixture
def store() -> InMemoryQuoteStore:
    return InMemoryQuoteStore()


@pytest.fixture
def provider(store: InMemoryQuoteStore) -> DevelopmentQuoteProvider:
    fixed = datetime(2026, 5, 19, 2, 14, 33, tzinfo=UTC)
    return DevelopmentQuoteProvider(store, clock=lambda: fixed)


@pytest.fixture
def service(provider: DevelopmentQuoteProvider) -> DevQuoteIngestService:
    symbol_service = MagicMock()
    validator = QuoteValidator(TradingSessionService())
    return DevQuoteIngestService(symbol_service, validator, provider)


def test_get_snapshot_returns_none_when_symbol_not_seen(service: DevQuoteIngestService) -> None:
    assert service.get_snapshot("2330") is None


def test_get_snapshot_returns_latest_after_ingest(service: DevQuoteIngestService) -> None:
    quote_time = datetime(2026, 5, 19, 2, 14, 33, tzinfo=UTC)
    service.ingest(
        symbol="2330",
        bid_price=Decimal("590.0"),
        ask_price=Decimal("591.0"),
        last_price=Decimal("590.5"),
        quote_time=quote_time,
    )

    snapshot = service.get_snapshot("2330")

    assert snapshot is not None
    assert snapshot.symbol == "2330"
    assert snapshot.bid_price == Decimal("590.0")
    assert snapshot.ask_price == Decimal("591.0")
    assert snapshot.last_price == Decimal("590.5")
    assert snapshot.quote_time == quote_time


def test_get_snapshot_isolated_per_symbol(service: DevQuoteIngestService) -> None:
    quote_time = datetime(2026, 5, 19, 2, 14, 33, tzinfo=UTC)
    service.ingest(
        symbol="2330",
        bid_price=Decimal("590"),
        ask_price=Decimal("591"),
        last_price=Decimal("590.5"),
        quote_time=quote_time,
    )

    assert service.get_snapshot("2454") is None
    assert service.get_snapshot("2330") is not None
