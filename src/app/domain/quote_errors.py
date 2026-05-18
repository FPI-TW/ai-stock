"""Domain errors emitted by QuoteValidator.

Symbol-related rejections continue to use UnknownSymbolError /
SymbolNotTradableError so the existing symbol service stays reusable.
"""

from datetime import datetime
from decimal import Decimal


class QuoteValidationError(Exception):
    """Base class for quote validation failures."""


class QuoteOutOfSessionError(QuoteValidationError):
    def __init__(self, quote_time: datetime) -> None:
        self.quote_time = quote_time


class QuoteCrossedError(QuoteValidationError):
    def __init__(self, bid: Decimal, ask: Decimal) -> None:
        self.bid = bid
        self.ask = ask


class QuoteNonPositivePriceError(QuoteValidationError):
    """A present price is <= 0. `field` is one of "bid" | "ask" | "last"."""

    def __init__(self, field: str, value: Decimal) -> None:
        self.field = field
        self.value = value


class QuoteInsufficientPricesError(QuoteValidationError):
    """All of bid / ask / last are missing for this symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
