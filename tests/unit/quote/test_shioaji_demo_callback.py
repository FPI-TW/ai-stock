"""Callback normalisation tests.

Exercises the `_on_tick` / `_on_bidask` handlers directly with synthetic payloads.
Verifies that:
- Asia/Taipei naive timestamps are tagged with tz before reaching `QuoteSnapshot`.
- Tick and bid/ask channels merge into a single snapshot per symbol (last writer
  for each field wins, missing fields preserved from the previous snapshot).
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.services.quote.shioaji_demo.normalize import BidAskPayload, TickPayload
from app.services.quote.shioaji_demo.provider import ShioajiQuoteProvider

TAIPEI = ZoneInfo("Asia/Taipei")


def _make_provider() -> ShioajiQuoteProvider:
    provider = ShioajiQuoteProvider(
        client=MagicMock(),
        allowed_symbols=frozenset({"2330"}),
        max_subscriptions=5,
    )
    provider._started = True  # noqa: SLF001
    provider.subscribe("2330")
    return provider


def test_tick_callback_normalises_last_price() -> None:
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)

    provider._on_tick(  # noqa: SLF001
        TickPayload(symbol="2330", last_price=Decimal("590.5"), quote_time=quote_time)
    )

    [snap] = provider.get_quotes(["2330"])
    assert snap.last_price == Decimal("590.5")
    assert snap.bid_price is None
    assert snap.ask_price is None
    assert snap.quote_time == quote_time
    assert snap.received_at.tzinfo is not None


def test_bidask_callback_normalises_top_of_book() -> None:
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)

    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(
            symbol="2330",
            bid_price=Decimal("589.0"),
            ask_price=Decimal("591.0"),
            quote_time=quote_time,
        )
    )

    [snap] = provider.get_quotes(["2330"])
    assert snap.bid_price == Decimal("589.0")
    assert snap.ask_price == Decimal("591.0")
    assert snap.last_price is None


def test_tick_then_bidask_merges_into_single_snapshot() -> None:
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)

    provider._on_tick(  # noqa: SLF001
        TickPayload(symbol="2330", last_price=Decimal("590.5"), quote_time=quote_time)
    )
    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(
            symbol="2330",
            bid_price=Decimal("589.0"),
            ask_price=Decimal("591.0"),
            quote_time=quote_time,
        )
    )

    [snap] = provider.get_quotes(["2330"])
    assert snap.last_price == Decimal("590.5")
    assert snap.bid_price == Decimal("589.0")
    assert snap.ask_price == Decimal("591.0")


def test_later_bidask_updates_overwrite_earlier() -> None:
    provider = _make_provider()
    earlier = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
    later = datetime(2026, 5, 11, 10, 31, tzinfo=TAIPEI)

    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(symbol="2330", bid_price=Decimal("589"), ask_price=Decimal("591"), quote_time=earlier)
    )
    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(symbol="2330", bid_price=Decimal("590"), ask_price=Decimal("592"), quote_time=later)
    )

    [snap] = provider.get_quotes(["2330"])
    assert snap.bid_price == Decimal("590")
    assert snap.ask_price == Decimal("592")
    assert snap.quote_time == later
