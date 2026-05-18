"""Quote domain types and rule-based validator.

The validator lives in the domain layer (no DB, no SymbolService) so that the
same rules can be reused by a future licensed quote provider without duplicating
them inside the dev endpoint. Symbol existence / tradable checks belong at the
ingest boundary (e.g., DevQuoteIngestService).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.quote_errors import (
    QuoteCrossedError,
    QuoteInsufficientPricesError,
    QuoteNonPositivePriceError,
    QuoteOutOfSessionError,
)
from app.domain.trading_session import TradingSessionService


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    """Normalized quote at a point in time.

    Both quote_time (source-reported) and received_at (server-set on ingest)
    must be timezone-aware; the validator delegates to TradingSessionService,
    which enforces awareness.
    """

    symbol: str
    bid_price: Decimal | None
    ask_price: Decimal | None
    last_price: Decimal | None
    quote_time: datetime
    received_at: datetime


@dataclass(frozen=True, slots=True)
class ValidatedQuote:
    """Outcome of validating a QuoteSnapshot.

    needs_buy_side_fallback / needs_sell_side_fallback tell a downstream
    strategy evaluator whether its primary reference price is missing and the
    last_price fallback would be required (buy uses ask, sell uses bid).
    """

    snapshot: QuoteSnapshot
    has_bid: bool
    has_ask: bool
    has_last: bool
    needs_buy_side_fallback: bool
    needs_sell_side_fallback: bool


class QuoteValidator:
    def __init__(self, session: TradingSessionService) -> None:
        self._session = session

    def validate(self, snapshot: QuoteSnapshot) -> ValidatedQuote:
        if not self._session.is_within_regular_session(snapshot.quote_time):
            raise QuoteOutOfSessionError(quote_time=snapshot.quote_time)

        has_bid = snapshot.bid_price is not None
        has_ask = snapshot.ask_price is not None
        has_last = snapshot.last_price is not None
        if not (has_bid or has_ask or has_last):
            raise QuoteInsufficientPricesError(symbol=snapshot.symbol)

        for field, value in (
            ("bid", snapshot.bid_price),
            ("ask", snapshot.ask_price),
            ("last", snapshot.last_price),
        ):
            if value is not None and value <= 0:
                raise QuoteNonPositivePriceError(field=field, value=value)

        if (
            snapshot.bid_price is not None
            and snapshot.ask_price is not None
            and snapshot.bid_price > snapshot.ask_price
        ):
            raise QuoteCrossedError(bid=snapshot.bid_price, ask=snapshot.ask_price)

        return ValidatedQuote(
            snapshot=snapshot,
            has_bid=has_bid,
            has_ask=has_ask,
            has_last=has_last,
            needs_buy_side_fallback=not has_ask,
            needs_sell_side_fallback=not has_bid,
        )
