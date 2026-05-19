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
        super().__init__(f"Quote time {quote_time.isoformat()} is outside regular session")


class QuoteCrossedError(QuoteValidationError):
    def __init__(self, bid: Decimal, ask: Decimal) -> None:
        self.bid = bid
        self.ask = ask
        super().__init__(f"Quote is crossed: bid {bid} is greater than ask {ask}")


class QuoteNonPositivePriceError(QuoteValidationError):
    """A present price is not finite or <= 0. `field` is one of "bid" | "ask" | "last"."""

    def __init__(self, field: str, value: Decimal) -> None:
        self.field = field
        self.value = value
        super().__init__(f"Quote {field} price must be finite and greater than 0: {value}")


class QuoteInsufficientPricesError(QuoteValidationError):
    """All of bid / ask / last are missing for this symbol."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        super().__init__(f"Quote for {symbol} must include at least one of bid, ask, or last price")
