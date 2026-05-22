"""Taiwan stock/ETF price domain service — tick-size tables, parsing, and validation."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum


def format_price_str(value: Decimal) -> str:
    """Render a Decimal as a user-facing string with at least two decimal places.

    Used wherever prices are surfaced to clients (API response, notification body).
    Keeps the original precision when more than two decimals are present.
    """
    s = format(value, "f")
    integer_part, _, decimal_part = s.partition(".")
    decimal_part = (decimal_part or "").rstrip("0").ljust(2, "0")
    return f"{integer_part}.{decimal_part}"


# TWSE stock tick-size table: (price_upper_bound_exclusive, tick_size)
# Source: https://www.twse.com.tw/zh/products/system/trading.html
_STOCK_TICK_TABLE: list[tuple[Decimal, Decimal]] = [
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.1")),
    (Decimal("500"), Decimal("0.5")),
    (Decimal("1000"), Decimal("1")),
]
_STOCK_TICK_ABOVE_1000 = Decimal("5")

# TWSE ETF tick-size table: (price_upper_bound_exclusive, tick_size)
# Source: https://www.twse.com.tw/zh/products/securities/etf/overview/rules.html
_ETF_TICK_TABLE: list[tuple[Decimal, Decimal]] = [
    (Decimal("50"), Decimal("0.01")),
]
_ETF_TICK_ABOVE_50 = Decimal("0.05")


class SecurityType(StrEnum):
    STOCK = "stock"
    ETF = "etf"


@dataclass(frozen=True)
class PriceRequest:
    type: SecurityType
    price: str  # always string to avoid float precision issues
    amount: int  # must be > 0


class InvalidPriceError(ValueError):
    def __init__(self, value: object, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid price {value!r}: {reason}")


class InvalidTickSizeError(InvalidPriceError):
    """Price is parseable but not a valid multiple of its TWSE tick size."""

    def __init__(self, value: object, reason: str, nearest_lower: Decimal, nearest_upper: Decimal) -> None:
        super().__init__(value, reason)
        self.nearest_lower = nearest_lower
        self.nearest_upper = nearest_upper


class InvalidAmountError(ValueError):
    def __init__(self, value: object, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid amount {value!r}: {reason}")


class InvalidTypeError(ValueError):
    def __init__(self, value: object, reason: str) -> None:
        self.value = value
        self.reason = reason
        super().__init__(f"Invalid type {value!r}: {reason}")


class PriceService:
    @staticmethod
    def parse(value: str) -> Decimal:
        """Parse a price string into a Decimal.

        Price must be passed as str to preserve exact decimal representation.
        """
        if not isinstance(value, str):
            raise InvalidPriceError(value, "Price must be a string to ensure precision")

        try:
            if value != value.strip():
                raise InvalidPriceError(value, "must not contain leading or trailing whitespace")
            if "," in value:
                raise InvalidPriceError(value, "Comma is not allowed in price")

            result = Decimal(value)
        except InvalidOperation:
            raise InvalidPriceError(value, "cannot be parsed as a number") from None
        if not result.is_finite():
            raise InvalidPriceError(value, "must be a finite number")
        if result <= 0:
            raise InvalidPriceError(value, "must be greater than zero")
        return result

    @staticmethod
    def lookup_tick_size(security_type: SecurityType, price: Decimal) -> Decimal:
        """Return the TWSE tick size for the given security type and price.

        Precondition: price must be > 0. Negative or zero values are not guarded
        here and will silently fall through to the highest tick bucket.
        Use validate() or parse() to ensure price is positive before calling directly.
        """
        if security_type == SecurityType.ETF:
            for upper, tick in _ETF_TICK_TABLE:
                if price < upper:
                    return tick
            return _ETF_TICK_ABOVE_50
        for upper, tick in _STOCK_TICK_TABLE:
            if price < upper:
                return tick
        return _STOCK_TICK_ABOVE_1000

    @classmethod
    def is_valid_tick(cls, security_type: SecurityType, price: Decimal) -> bool:
        """Return True if price is an exact multiple of its tick size."""
        tick = cls.lookup_tick_size(security_type, price)
        return (price % tick) == Decimal("0")

    @classmethod
    def nearest_lower(cls, security_type: SecurityType, price: Decimal) -> Decimal:
        """Return the largest valid tick multiple that is ≤ price, minimum one tick.

        The minimum of one tick ensures the result is always a positive, tradeable price.
        """
        tick = cls.lookup_tick_size(security_type, price)
        lower = (price // tick) * tick
        return max(lower, tick)

    @classmethod
    def nearest_upper(cls, security_type: SecurityType, price: Decimal) -> Decimal:
        """Return the smallest valid tick multiple that is ≥ price."""
        tick = cls.lookup_tick_size(security_type, price)
        lower = (price // tick) * tick
        return lower if lower == price else lower + tick

    @classmethod
    def validate(cls, request: PriceRequest) -> Decimal:
        """Validate all fields of a PriceRequest.

        Returns the parsed price Decimal on success.
        Raises InvalidAmountError or InvalidPriceError on failure.
        """
        if not isinstance(request.amount, int) or isinstance(request.amount, bool):
            raise InvalidAmountError(request.amount, "must be an integer")
        if request.amount <= 0:
            raise InvalidAmountError(request.amount, "must be greater than zero")
        try:
            SecurityType(request.type)
        except ValueError:
            raise InvalidTypeError(request.type, "must be stock or etf") from None
        price = cls.parse(request.price)
        if not cls.is_valid_tick(request.type, price):
            tick = cls.lookup_tick_size(request.type, price)
            raise InvalidTickSizeError(
                request.price,
                f"not a valid tick multiple (tick size for this range is {tick})",
                nearest_lower=cls.nearest_lower(request.type, price),
                nearest_upper=cls.nearest_upper(request.type, price),
            )
        return price
