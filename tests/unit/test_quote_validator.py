"""Unit tests for app.domain.quote.QuoteValidator."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.domain.quote import QuoteSnapshot, QuoteValidator, ValidatedQuote
from app.domain.quote_errors import (
    QuoteCrossedError,
    QuoteInsufficientPricesError,
    QuoteNonPositivePriceError,
    QuoteOutOfSessionError,
)
from app.domain.trading_session import TradingSessionService

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")

# Monday 2026-05-18 is a weekday inside the Taiwan trading session.
MON_10AM = datetime(2026, 5, 18, 10, 0, tzinfo=TAIPEI)
MON_859 = datetime(2026, 5, 18, 8, 59, tzinfo=TAIPEI)
MON_1330 = datetime(2026, 5, 18, 13, 30, tzinfo=TAIPEI)
SAT_10AM = datetime(2026, 5, 23, 10, 0, tzinfo=TAIPEI)
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


@pytest.fixture
def validator() -> QuoteValidator:
    return QuoteValidator(TradingSessionService())


class TestValidQuotes:
    def test_full_quote_passes_without_fallback_flags(self, validator: QuoteValidator) -> None:
        result = validator.validate(make_snapshot())
        assert isinstance(result, ValidatedQuote)
        assert result.has_bid and result.has_ask and result.has_last
        assert not result.needs_buy_side_fallback
        assert not result.needs_sell_side_fallback

    def test_bid_ask_only_passes_without_fallback(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(last_price=None)
        result = validator.validate(snap)
        assert result.has_bid and result.has_ask and not result.has_last
        assert not result.needs_buy_side_fallback
        assert not result.needs_sell_side_fallback

    def test_last_only_passes_and_marks_both_fallbacks(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(bid_price=None, ask_price=None)
        result = validator.validate(snap)
        assert not result.has_bid
        assert not result.has_ask
        assert result.has_last
        assert result.needs_buy_side_fallback
        assert result.needs_sell_side_fallback

    def test_bid_and_last_passes_with_buy_side_fallback(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(ask_price=None)
        result = validator.validate(snap)
        assert result.needs_buy_side_fallback
        assert not result.needs_sell_side_fallback

    def test_ask_and_last_passes_with_sell_side_fallback(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(bid_price=None)
        result = validator.validate(snap)
        assert not result.needs_buy_side_fallback
        assert result.needs_sell_side_fallback

    def test_equal_bid_and_ask_is_allowed(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(bid_price=Decimal("600.00"), ask_price=Decimal("600.00"))
        result = validator.validate(snap)
        assert result.has_bid and result.has_ask


class TestCrossedQuote:
    def test_crossed_quote_raises(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(bid_price=Decimal("601.00"), ask_price=Decimal("600.00"))
        with pytest.raises(QuoteCrossedError) as exc_info:
            validator.validate(snap)
        assert exc_info.value.bid == Decimal("601.00")
        assert exc_info.value.ask == Decimal("600.00")


class TestNonPositivePrice:
    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("0.0"), Decimal("-0.01")])
    def test_non_positive_bid_rejected(self, validator: QuoteValidator, value: Decimal) -> None:
        snap = make_snapshot(bid_price=value)
        with pytest.raises(QuoteNonPositivePriceError) as exc_info:
            validator.validate(snap)
        assert exc_info.value.field == "bid"
        assert exc_info.value.value == value

    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("-0.01")])
    def test_non_positive_ask_rejected(self, validator: QuoteValidator, value: Decimal) -> None:
        snap = make_snapshot(ask_price=value)
        with pytest.raises(QuoteNonPositivePriceError) as exc_info:
            validator.validate(snap)
        assert exc_info.value.field == "ask"
        assert exc_info.value.value == value

    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("-0.01")])
    def test_non_positive_last_rejected(self, validator: QuoteValidator, value: Decimal) -> None:
        snap = make_snapshot(last_price=value)
        with pytest.raises(QuoteNonPositivePriceError) as exc_info:
            validator.validate(snap)
        assert exc_info.value.field == "last"
        assert exc_info.value.value == value


class TestOutOfSession:
    def test_before_open(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(quote_time=MON_859)
        with pytest.raises(QuoteOutOfSessionError) as exc_info:
            validator.validate(snap)
        assert exc_info.value.quote_time == MON_859

    def test_at_close_excluded(self, validator: QuoteValidator) -> None:
        # session window is [09:00, 13:30); 13:30:00 is excluded.
        snap = make_snapshot(quote_time=MON_1330)
        with pytest.raises(QuoteOutOfSessionError):
            validator.validate(snap)

    def test_weekend_rejected(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(quote_time=SAT_10AM)
        with pytest.raises(QuoteOutOfSessionError):
            validator.validate(snap)


class TestInsufficientPrices:
    def test_all_three_missing(self, validator: QuoteValidator) -> None:
        snap = make_snapshot(bid_price=None, ask_price=None, last_price=None)
        with pytest.raises(QuoteInsufficientPricesError) as exc_info:
            validator.validate(snap)
        assert exc_info.value.symbol == "2330"


class TestNaiveDatetime:
    def test_naive_quote_time_rejected(self, validator: QuoteValidator) -> None:
        naive = datetime(2026, 5, 18, 10, 0)
        snap = make_snapshot(quote_time=naive)
        with pytest.raises(TypeError):
            validator.validate(snap)
