"""Fubon aggregates payload → QuoteSnapshot normalisation.

Pure-function tests: no SDK, no network. Payload shape follows the official
`aggregates` channel example (docs/vendor/fubon/fubon-llms-full.txt).
"""

from datetime import datetime
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
    assert snapshot.quote_time == datetime(2023, 5, 29, 13, 30, tzinfo=TAIPEI)  # 步驟 3 改名 last_trade_time 後改回
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
