"""Shioaji callback payload → QuoteSnapshot normalisation.

Lives in its own module so it can be unit-tested without importing the `shioaji`
SDK (`client.py` is the only module that imports `shioaji`). Splitting normalisation
out also makes the contract explicit: this is the data shape the provider's
callbacks operate on, regardless of how it's produced.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.services.quote.base import QuoteProviderUnavailableError, QuoteSnapshot

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class TickPayload:
    """Normalised tick callback payload (last-trade)."""

    symbol: str
    last_price: Decimal
    quote_time: datetime


@dataclass(frozen=True)
class BidAskPayload:
    """Normalised bid/ask callback payload (top of book)."""

    symbol: str
    bid_price: Decimal | None
    ask_price: Decimal | None
    quote_time: datetime


def to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if decimal_value <= Decimal(0):
        return None
    return decimal_value


def first(value: Any) -> Any:
    """Shioaji's bid/ask payloads expose 5-level depth as a list; we only need top of book."""

    if isinstance(value, list | tuple) and value:
        return value[0]
    return value


def to_taipei(raw_dt: Any) -> datetime:
    """Convert Shioaji's naive Asia/Taipei datetime payload into a tz-aware Asia/Taipei datetime.

    Shioaji pushes wall-clock time without tzinfo. Treat naive values as Taipei local time;
    pass aware values through after normalising to Taipei.
    """

    if not isinstance(raw_dt, datetime):
        raise QuoteProviderUnavailableError("shioaji", f"unexpected quote_time type: {type(raw_dt).__name__}")
    if raw_dt.tzinfo is None:
        return raw_dt.replace(tzinfo=_TAIPEI_TZ)
    return raw_dt.astimezone(_TAIPEI_TZ)


def now_utc() -> datetime:
    return datetime.now(tz=_UTC)


def build_snapshot(
    *,
    symbol: str,
    previous: QuoteSnapshot | None,
    bid_price: Decimal | None = None,
    ask_price: Decimal | None = None,
    last_price: Decimal | None = None,
    quote_time: datetime,
) -> QuoteSnapshot:
    """Merge an incoming partial update with the previous snapshot.

    Tick callbacks only know `last_price`; bidask callbacks only know `bid` / `ask`.
    We retain whatever field the other channel last set so `get_quotes` always
    surfaces the most recent value per field.
    """

    return QuoteSnapshot(
        symbol=symbol,
        bid_price=bid_price if bid_price is not None else (previous.bid_price if previous else None),
        ask_price=ask_price if ask_price is not None else (previous.ask_price if previous else None),
        last_price=last_price if last_price is not None else (previous.last_price if previous else None),
        quote_time=quote_time,
        received_at=now_utc(),
    )
