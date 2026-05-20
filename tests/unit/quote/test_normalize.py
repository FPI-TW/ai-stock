"""Direct tests on the SDK-agnostic normalisation helpers."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.quote.base import QuoteProviderUnavailableError, QuoteSnapshot
from app.services.quote.shioaji_demo.normalize import (
    build_snapshot,
    first,
    to_decimal,
    to_taipei,
)

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")


def test_to_decimal_handles_str_and_int() -> None:
    assert to_decimal("590.5") == Decimal("590.5")
    assert to_decimal(100) == Decimal("100")


def test_to_decimal_preserves_zero_and_negative_for_validation() -> None:
    assert to_decimal(0) == Decimal("0")
    assert to_decimal("-1") == Decimal("-1")
    assert to_decimal(None) is None


def test_to_decimal_returns_none_for_garbage() -> None:
    assert to_decimal("not-a-number") is None


def test_first_extracts_head_of_list_or_tuple() -> None:
    assert first([590, 591, 592]) == 590
    assert first((590, 591)) == 590
    assert first(590) == 590  # scalar passes through
    assert first([]) == []
    assert first(None) is None


def test_to_taipei_tags_naive_as_taipei() -> None:
    naive = datetime(2026, 5, 11, 10, 30)
    result = to_taipei(naive)
    assert result.tzinfo is not None
    assert result.utcoffset() == datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI).utcoffset()


def test_to_taipei_converts_utc_to_taipei() -> None:
    utc_dt = datetime(2026, 5, 11, 2, 30, tzinfo=UTC)
    result = to_taipei(utc_dt)
    assert result.hour == 10
    assert result.tzinfo is not None


def test_to_taipei_raises_for_non_datetime() -> None:
    with pytest.raises(QuoteProviderUnavailableError):
        to_taipei("2026-05-11T10:30:00")


def test_build_snapshot_keeps_previous_fields_on_partial_update() -> None:
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
    previous = QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal("589"),
        ask_price=Decimal("591"),
        last_price=Decimal("590"),
        quote_time=quote_time,
        received_at=datetime.now(tz=UTC),
    )
    later = datetime(2026, 5, 11, 10, 31, tzinfo=TAIPEI)
    result = build_snapshot(symbol="2330", previous=previous, last_price=Decimal("590.5"), quote_time=later)
    assert result.last_price == Decimal("590.5")
    assert result.bid_price == Decimal("589")  # carried over from previous
    assert result.ask_price == Decimal("591")  # carried over from previous
    assert result.quote_time == later


def test_build_snapshot_clears_bidask_when_bidask_channel_reports_missing_values() -> None:
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
    previous = QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal("589"),
        ask_price=Decimal("591"),
        last_price=Decimal("590"),
        quote_time=quote_time,
        received_at=datetime.now(tz=UTC),
    )
    later = datetime(2026, 5, 11, 10, 31, tzinfo=TAIPEI)

    result = build_snapshot(symbol="2330", previous=previous, bid_price=None, ask_price=None, quote_time=later)

    assert result.bid_price is None
    assert result.ask_price is None
    assert result.last_price == Decimal("590")
    assert result.quote_time == later
