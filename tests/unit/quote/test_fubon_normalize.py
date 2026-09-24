"""Fubon aggregates payload → QuoteSnapshot normalisation.

Pure-function tests: no SDK, no network. Payload shape follows the official
`aggregates` channel example (docs/vendor/fubon/fubon-llms-full.txt).
"""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services.quote.fubon.normalize import aggregates_to_snapshot

TAIPEI = ZoneInfo("Asia/Taipei")


def test_aggregates_to_snapshot_uses_last_trade_not_trial_price() -> None:
    # `lastPrice` includes pre-open trial matching; the trigger source of truth
    # is `lastTrade.price`. Make them differ so the test cannot pass by accident.
    payload = {
        "symbol": "2330",
        "lastPrice": 999,
        "bids": [{"price": 567, "size": 87}, {"price": 566, "size": 2454}],
        "asks": [{"price": 568, "size": 800}, {"price": 569, "size": 806}],
        "lastTrade": {"bid": 567, "ask": 568, "price": 568, "size": 4778, "time": 1685338200000000, "serial": 6652422},
        "lastUpdated": 1685338200000000,
    }

    snapshot = aggregates_to_snapshot(payload)

    assert snapshot.symbol == "2330"
    assert snapshot.last_price == Decimal("568")
    assert snapshot.bid_price == Decimal("567")
    assert snapshot.ask_price == Decimal("568")
    # `lastTrade.time` is epoch microseconds; expose it as tz-aware Asia/Taipei.
    assert snapshot.last_trade_time == datetime(2023, 5, 29, 13, 30, tzinfo=TAIPEI)
    assert snapshot.received_at.tzinfo is not None


def _payload_with_trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "2330",
        "lastPrice": 568,
        "bids": [{"price": 567, "size": 87}],
        "asks": [{"price": 568, "size": 800}],
        "lastTrade": {"price": 568, "size": 1, "time": 1685338200000000},
    }
    base.update(overrides)
    return base


def test_empty_book_yields_none_bid_ask() -> None:
    # Pre-open / after-close frames carry empty depth arrays; the validator
    # falls back to last_price, so both sides must be None rather than a crash.
    snapshot = aggregates_to_snapshot(_payload_with_trade(bids=[], asks=[]))

    assert snapshot.bid_price is None
    assert snapshot.ask_price is None
    assert snapshot.last_price == Decimal("568")


def test_missing_book_keys_yield_none_bid_ask() -> None:
    payload = _payload_with_trade()
    del payload["bids"]
    del payload["asks"]

    snapshot = aggregates_to_snapshot(payload)

    assert snapshot.bid_price is None
    assert snapshot.ask_price is None


def test_missing_last_trade_yields_none_price_and_time() -> None:
    # Before the first match of the day the frame has a book but no `lastTrade`;
    # the order forbids falling back to `lastPrice` (trial matching).
    payload = _payload_with_trade()
    del payload["lastTrade"]

    snapshot = aggregates_to_snapshot(payload)

    assert snapshot.last_price is None
    assert snapshot.last_trade_time is None
    assert snapshot.bid_price == Decimal("567")


def test_quote_time_follows_last_updated_not_last_trade() -> None:
    # Thin stock: the book keeps moving (`lastUpdated`) while the last match is
    # old. Freshness must follow the frame, or every book-only update is stale.
    trade_micros = 1685338200000000  # 13:30:00
    later_micros = trade_micros + 600_000_000  # +10 min, book-only update
    payload = _payload_with_trade(lastUpdated=later_micros)

    snapshot = aggregates_to_snapshot(payload)

    assert snapshot.quote_time == datetime(2023, 5, 29, 13, 40, tzinfo=TAIPEI)
    assert snapshot.last_trade_time == datetime(2023, 5, 29, 13, 30, tzinfo=TAIPEI)


def test_missing_last_updated_falls_back_to_received_at() -> None:
    # `lastUpdated` is not a mandatory field in the vendor spec.
    snapshot = aggregates_to_snapshot(_payload_with_trade())

    assert snapshot.quote_time == snapshot.received_at.astimezone(TAIPEI)
    assert snapshot.quote_time.tzinfo is not None
    assert snapshot.received_at.tzinfo is UTC
