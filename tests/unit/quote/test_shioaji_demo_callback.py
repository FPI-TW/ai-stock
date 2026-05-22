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
    provider.mark_started_for_tests()
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


def test_missing_bidask_update_clears_previous_bidask_and_keeps_last() -> None:
    provider = _make_provider()
    earlier = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
    later = datetime(2026, 5, 11, 10, 31, tzinfo=TAIPEI)
    provider._on_tick(TickPayload(symbol="2330", last_price=Decimal("590.5"), quote_time=earlier))  # noqa: SLF001
    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(symbol="2330", bid_price=Decimal("589"), ask_price=Decimal("591"), quote_time=earlier)
    )

    provider._on_bidask(BidAskPayload(symbol="2330", bid_price=None, ask_price=None, quote_time=later))  # noqa: SLF001

    [snap] = provider.get_quotes(["2330"])
    assert snap.bid_price is None
    assert snap.ask_price is None
    assert snap.last_price == Decimal("590.5")
    assert snap.quote_time == later


def test_non_positive_bidask_update_is_preserved_for_validation() -> None:
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)

    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(symbol="2330", bid_price=Decimal("0"), ask_price=Decimal("-1"), quote_time=quote_time)
    )

    [snap] = provider.get_quotes(["2330"])
    assert snap.bid_price == Decimal("0")
    assert snap.ask_price == Decimal("-1")


def test_tick_callback_fires_registered_listeners() -> None:
    """Acceptance: Shioaji callback drives evaluator dispatch via the listener
    contract. The provider must invoke every registered listener with the new
    snapshot once it lands in cache.
    """
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)

    listener_a = MagicMock()
    listener_b = MagicMock()
    provider.add_quote_listener(listener_a)
    provider.add_quote_listener(listener_b)

    provider._on_tick(  # noqa: SLF001
        TickPayload(symbol="2330", last_price=Decimal("590.5"), quote_time=quote_time)
    )

    listener_a.assert_called_once()
    listener_b.assert_called_once()
    (snap_a,), _ = listener_a.call_args
    assert snap_a.symbol == "2330"
    assert snap_a.last_price == Decimal("590.5")


def test_bidask_callback_fires_registered_listeners() -> None:
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
    listener = MagicMock()
    provider.add_quote_listener(listener)

    provider._on_bidask(  # noqa: SLF001
        BidAskPayload(
            symbol="2330",
            bid_price=Decimal("589"),
            ask_price=Decimal("591"),
            quote_time=quote_time,
        )
    )

    listener.assert_called_once()
    (snap,), _ = listener.call_args
    assert snap.bid_price == Decimal("589")
    assert snap.ask_price == Decimal("591")


def test_listener_exception_does_not_block_subsequent_listeners() -> None:
    """A misbehaving listener must not kill the dispatch chain — broker callbacks
    can't tolerate uncaught exceptions reaching back into the SDK thread.
    """
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)

    boom = MagicMock(side_effect=RuntimeError("listener exploded"))
    survivor = MagicMock()
    provider.add_quote_listener(boom)
    provider.add_quote_listener(survivor)

    provider._on_tick(  # noqa: SLF001
        TickPayload(symbol="2330", last_price=Decimal("590.5"), quote_time=quote_time)
    )

    boom.assert_called_once()
    survivor.assert_called_once()


def test_remove_quote_listener_stops_further_notifications() -> None:
    provider = _make_provider()
    quote_time = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
    listener = MagicMock()
    provider.add_quote_listener(listener)

    provider._on_tick(  # noqa: SLF001
        TickPayload(symbol="2330", last_price=Decimal("590.5"), quote_time=quote_time)
    )
    listener.assert_called_once()

    provider.remove_quote_listener(listener)
    provider._on_tick(  # noqa: SLF001
        TickPayload(symbol="2330", last_price=Decimal("591"), quote_time=quote_time)
    )
    listener.assert_called_once()  # unchanged after removal


def test_remove_unregistered_listener_is_silent() -> None:
    provider = _make_provider()
    stranger = MagicMock()
    # Must not raise — idempotent per protocol contract.
    provider.remove_quote_listener(stranger)
