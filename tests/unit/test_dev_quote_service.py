"""DevQuoteIngestService.get_snapshot() 單元測試。"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.domain.quote import QuoteValidator
from app.domain.trading_session import TradingSessionService
from app.services.quote import (
    DevelopmentQuoteProvider,
    DevQuoteIngestService,
    InMemoryQuoteStore,
)

RECEIVED = datetime(2026, 5, 19, 2, 14, 33, tzinfo=UTC)


class TestDevQuoteIngestServiceGetSnapshot:
    @pytest.fixture
    def store(self) -> InMemoryQuoteStore:
        return InMemoryQuoteStore()

    @pytest.fixture
    def provider(self, store: InMemoryQuoteStore) -> DevelopmentQuoteProvider:
        return DevelopmentQuoteProvider(store, clock=lambda: RECEIVED)

    @pytest.fixture
    def validator(self) -> QuoteValidator:
        return QuoteValidator(TradingSessionService())

    @pytest.fixture
    def symbol_service(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def service(
        self,
        symbol_service: MagicMock,
        validator: QuoteValidator,
        provider: DevelopmentQuoteProvider,
    ) -> DevQuoteIngestService:
        return DevQuoteIngestService(symbol_service, validator, provider)

    def test_get_snapshot_returns_none_when_symbol_not_seen(
        self,
        service: DevQuoteIngestService,
    ) -> None:
        assert service.get_snapshot("2330") is None

    def test_get_snapshot_returns_latest_after_ingest(
        self,
        service: DevQuoteIngestService,
    ) -> None:
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
        assert snapshot.received_at == RECEIVED

    def test_get_snapshot_isolated_per_symbol(
        self,
        service: DevQuoteIngestService,
    ) -> None:
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
