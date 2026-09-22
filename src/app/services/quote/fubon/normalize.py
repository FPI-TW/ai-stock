"""Fubon market-data payload → QuoteSnapshot normalisation.

Pure functions only — no `fubon_neo` import — so the contract can be unit-tested
without the SDK. Both the websocket `aggregates` channel and the REST
`intraday/quote` endpoint share the same dict shape.

Rules (docs/orders/per-user-broker-sessions.md, PR1):
- `last_price` comes from `lastTrade.price`; `lastPrice` includes pre-open trial
  matching and must not be used for triggers.
- Top of book is `bids[0]` / `asks[0]`.
- `quote_time` is the frame's `lastUpdated` (moves on book-only updates, which is
  what keeps thin stocks evaluable); `last_trade_time` is `lastTrade.time`.
- Timestamps are epoch microseconds; expose them as tz-aware Asia/Taipei.
"""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.services.quote.base import QuoteSnapshot

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _micros_to_taipei(micros: int) -> datetime:
    return datetime.fromtimestamp(micros / 1_000_000, tz=UTC).astimezone(_TAIPEI_TZ)


def _top_of_book(levels: Any) -> Decimal | None:
    """`bids` / `asks` are 5-level depth arrays; empty or missing outside the session."""
    if not isinstance(levels, list) or not levels:
        return None
    return _to_decimal(levels[0].get("price"))


def aggregates_to_snapshot(data: dict[str, Any]) -> QuoteSnapshot:
    received_at = datetime.now(tz=UTC)
    last_trade = data.get("lastTrade") or {}
    trade_time = last_trade.get("time")
    last_updated = data.get("lastUpdated")  # not a mandatory field in the vendor spec
    return QuoteSnapshot(
        symbol=data["symbol"],
        bid_price=_top_of_book(data.get("bids")),
        ask_price=_top_of_book(data.get("asks")),
        last_price=_to_decimal(last_trade.get("price")),
        quote_time=_micros_to_taipei(last_updated) if last_updated is not None else received_at.astimezone(_TAIPEI_TZ),
        last_trade_time=_micros_to_taipei(trade_time) if trade_time is not None else None,
        received_at=received_at,
    )
