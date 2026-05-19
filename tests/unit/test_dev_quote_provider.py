"""Unit tests for app.services.quote (InMemoryQuoteStore, DevelopmentQuoteProvider,
DevQuoteIngestService)."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.domain.quote import QuoteSnapshot, QuoteValidator
from app.domain.quote_errors import (
    QuoteInsufficientPricesError,
    QuoteOutOfSessionError,
)
from app.domain.symbol_errors import SymbolNotTradableError, UnknownSymbolError
from app.domain.trading_session import TradingSessionService
from app.services.quote import (
    DevelopmentQuoteProvider,
    DevQuoteIngestService,
    InMemoryQuoteStore,
)

TAIPEI = ZoneInfo("Asia/Taipei")
MON_10AM = datetime(2026, 5, 18, 10, 0, tzinfo=TAIPEI)
MON_859 = datetime(2026, 5, 18, 8, 59, tzinfo=TAIPEI)
RECEIVED = datetime(2026, 5, 18, 2, 0, tzinfo=UTC)


def make_snapshot(
    *,
    symbol: str = "2330",
    bid_price: Decimal | None = Decimal("599.00"),
    ask_price: Decimal | None = Decimal("600.00"),
    last_price: Decimal | None = Decimal("599.50"),
    quote_time: datetime = MON_10AM,
    received_at: datetime = RECEIVED,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=bid_price,
        ask_price=ask_price,
        last_price=last_price,
        quote_time=quote_time,
        received_at=received_at,
    )


class TestInMemoryQuoteStore:
    def test_upsert_then_get_returns_snapshot(self) -> None:
        store = InMemoryQuoteStore()
        snap = make_snapshot()
        store.upsert(snap)
        assert store.get("2330") == snap

    def test_get_returns_none_for_missing_symbol(self) -> None:
        store = InMemoryQuoteStore()
        assert store.get("2330") is None

    def test_upsert_same_symbol_overwrites(self) -> None:
        store = InMemoryQuoteStore()
        store.upsert(make_snapshot(last_price=Decimal("100.00")))
        store.upsert(make_snapshot(last_price=Decimal("200.00")))
        result = store.get("2330")
        assert result is not None
        assert result.last_price == Decimal("200.00")

    def test_get_many_skips_missing_symbols(self) -> None:
        store = InMemoryQuoteStore()
        store.upsert(make_snapshot(symbol="2330"))
        store.upsert(make_snapshot(symbol="0050"))
        result = store.get_many(["2330", "9999", "0050"])
        assert {s.symbol for s in result} == {"2330", "0050"}

    def test_get_many_empty_input(self) -> None:
        store = InMemoryQuoteStore()
        store.upsert(make_snapshot())
        assert store.get_many([]) == []

    def test_clear_removes_all(self) -> None:
        store = InMemoryQuoteStore()
        store.upsert(make_snapshot(symbol="2330"))
        store.upsert(make_snapshot(symbol="0050"))
        store.clear()
        assert store.get("2330") is None
        assert store.get("0050") is None

    def test_upsert_different_symbols_do_not_interfere(self) -> None:
        store = InMemoryQuoteStore()
        snap_a = make_snapshot(symbol="2330", last_price=Decimal("599.00"))
        snap_b = make_snapshot(symbol="0050", last_price=Decimal("200.00"))
        store.upsert(snap_a)
        store.upsert(snap_b)
        assert store.get("2330") == snap_a
        assert store.get("0050") == snap_b

    def test_updating_one_symbol_keeps_others_intact(self) -> None:
        store = InMemoryQuoteStore()
        store.upsert(make_snapshot(symbol="2330", last_price=Decimal("599.00")))
        store.upsert(make_snapshot(symbol="0050", last_price=Decimal("200.00")))
        store.upsert(make_snapshot(symbol="0050", last_price=Decimal("250.00")))
        snap_2330 = store.get("2330")
        snap_0050 = store.get("0050")
        assert snap_2330 is not None and snap_2330.last_price == Decimal("599.00")
        assert snap_0050 is not None and snap_0050.last_price == Decimal("250.00")


class TestDevelopmentQuoteProvider:
    def test_build_snapshot_uses_injected_clock(self) -> None:
        fixed = datetime(2026, 5, 18, 3, 0, tzinfo=UTC)
        provider = DevelopmentQuoteProvider(InMemoryQuoteStore(), clock=lambda: fixed)
        snap = provider.build_snapshot(
            symbol="2330",
            bid_price=Decimal("599.00"),
            ask_price=Decimal("600.00"),
            last_price=Decimal("599.50"),
            quote_time=MON_10AM,
        )
        assert snap.received_at == fixed
        assert snap.symbol == "2330"
        assert snap.bid_price == Decimal("599.00")
        assert snap.quote_time == MON_10AM

    def test_get_quotes_returns_stored_snapshots(self) -> None:
        store = InMemoryQuoteStore()
        provider = DevelopmentQuoteProvider(store)
        snap = make_snapshot()
        provider.store(snap)
        assert provider.get_quotes(["2330"]) == [snap]

    def test_get_quotes_for_missing_symbols(self) -> None:
        provider = DevelopmentQuoteProvider(InMemoryQuoteStore())
        assert provider.get_quotes(["unknown"]) == []


class TestDevQuoteIngestService:
    @pytest.fixture
    def store(self) -> InMemoryQuoteStore:
        return InMemoryQuoteStore()

    @pytest.fixture
    def provider(self, store: InMemoryQuoteStore) -> DevelopmentQuoteProvider:
        fixed = datetime(2026, 5, 18, 2, 0, tzinfo=UTC)
        return DevelopmentQuoteProvider(store, clock=lambda: fixed)

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

    def test_ingest_happy_path_persists_snapshot(
        self,
        service: DevQuoteIngestService,
        store: InMemoryQuoteStore,
        symbol_service: MagicMock,
    ) -> None:
        result = service.ingest(
            symbol="2330",
            bid_price=Decimal("599.00"),
            ask_price=Decimal("600.00"),
            last_price=Decimal("599.50"),
            quote_time=MON_10AM,
        )
        assert result.snapshot.symbol == "2330"
        assert store.get("2330") == result.snapshot
        symbol_service.get_tradable_symbol.assert_called_once_with("2330")

    def test_ingest_rejected_quote_does_not_persist(
        self,
        service: DevQuoteIngestService,
        store: InMemoryQuoteStore,
    ) -> None:
        with pytest.raises(QuoteOutOfSessionError):
            service.ingest(
                symbol="2330",
                bid_price=Decimal("599.00"),
                ask_price=Decimal("600.00"),
                last_price=Decimal("599.50"),
                quote_time=MON_859,
            )
        assert store.get("2330") is None

    def test_ingest_unknown_symbol_does_not_persist(
        self,
        store: InMemoryQuoteStore,
        validator: QuoteValidator,
        provider: DevelopmentQuoteProvider,
    ) -> None:
        symbol_service = MagicMock()
        symbol_service.get_tradable_symbol.side_effect = UnknownSymbolError("9999")
        service = DevQuoteIngestService(symbol_service, validator, provider)
        with pytest.raises(UnknownSymbolError):
            service.ingest(
                symbol="9999",
                bid_price=Decimal("1.00"),
                ask_price=Decimal("2.00"),
                last_price=Decimal("1.50"),
                quote_time=MON_10AM,
            )
        assert store.get("9999") is None

    def test_ingest_halted_symbol_does_not_persist(
        self,
        store: InMemoryQuoteStore,
        validator: QuoteValidator,
        provider: DevelopmentQuoteProvider,
    ) -> None:
        symbol_service = MagicMock()
        symbol_service.get_tradable_symbol.side_effect = SymbolNotTradableError("2330", "halted")
        service = DevQuoteIngestService(symbol_service, validator, provider)
        with pytest.raises(SymbolNotTradableError):
            service.ingest(
                symbol="2330",
                bid_price=Decimal("1.00"),
                ask_price=Decimal("2.00"),
                last_price=Decimal("1.50"),
                quote_time=MON_10AM,
            )
        assert store.get("2330") is None

    def test_ingest_insufficient_prices_does_not_persist(
        self,
        service: DevQuoteIngestService,
        store: InMemoryQuoteStore,
    ) -> None:
        with pytest.raises(QuoteInsufficientPricesError):
            service.ingest(
                symbol="2330",
                bid_price=None,
                ask_price=None,
                last_price=None,
                quote_time=MON_10AM,
            )
        assert store.get("2330") is None
