"""Unit tests for PriceService — parse, tick_size_for, is_valid_tick, validate."""

from decimal import Decimal

import pytest

from app.domain.price import (
    InvalidAmountError,
    InvalidPriceError,
    InvalidTickSizeError,
    InvalidTypeError,
    PriceRequest,
    PriceService,
    SecurityType,
)


class TestParse:
    def test_accepts_str_integer(self) -> None:
        assert PriceService.parse("100") == Decimal("100")

    def test_accepts_str_decimal(self) -> None:
        assert PriceService.parse("49.95") == Decimal("49.95")

    def test_returns_decimal_type(self) -> None:
        assert isinstance(PriceService.parse("0.1"), Decimal)

    def test_no_float_artifact(self) -> None:
        # Decimal arithmetic is exact — no IEEE 754 artifact
        assert PriceService.parse("0.1") + Decimal("0.2") == Decimal("0.3")

    def test_rejects_comma_in_price(self) -> None:
        with pytest.raises(InvalidPriceError, match="Comma is not allowed"):
            PriceService.parse("1,234.5")

    def test_rejects_leading_whitespace(self) -> None:
        with pytest.raises(InvalidPriceError, match="must not contain leading or trailing whitespace"):
            PriceService.parse("  50.05")

    def test_rejects_trailing_whitespace(self) -> None:
        with pytest.raises(InvalidPriceError, match="must not contain leading or trailing whitespace"):
            PriceService.parse("50.05  ")

    def test_rejects_non_string_int(self) -> None:
        with pytest.raises(InvalidPriceError, match="must be a string"):
            PriceService.parse(100)  # type: ignore[arg-type]

    def test_rejects_non_string_float(self) -> None:
        with pytest.raises(InvalidPriceError, match="must be a string"):
            PriceService.parse(49.95)  # type: ignore[arg-type]

    def test_rejects_non_numeric_string(self) -> None:
        with pytest.raises(InvalidPriceError, match="cannot be parsed"):
            PriceService.parse("abc")

    def test_rejects_zero(self) -> None:
        with pytest.raises(InvalidPriceError, match="greater than zero"):
            PriceService.parse("0")

    def test_rejects_negative(self) -> None:
        with pytest.raises(InvalidPriceError, match="greater than zero"):
            PriceService.parse("-1")

    def test_rejects_infinity_string(self) -> None:
        with pytest.raises(InvalidPriceError, match="finite"):
            PriceService.parse("Inf")

    def test_rejects_nan_string(self) -> None:
        with pytest.raises(InvalidPriceError, match="finite"):
            PriceService.parse("NaN")


class TestTickSizeForStock:
    @pytest.mark.parametrize(
        ("price", "expected_tick"),
        [
            # < 10 → 0.01
            ("0.01", "0.01"),
            ("1", "0.01"),
            ("9.99", "0.01"),
            # 10 – 50 → 0.05
            ("10", "0.05"),
            ("10.05", "0.05"),
            ("49.95", "0.05"),
            # 50 – 100 → 0.1
            ("50", "0.1"),
            ("99.9", "0.1"),
            # 100 – 500 → 0.5
            ("100", "0.5"),
            ("499.5", "0.5"),
            # 500 – 1000 → 1
            ("500", "1"),
            ("999", "1"),
            # ≥ 1000 → 5
            ("1000", "5"),
            ("2000", "5"),
        ],
    )
    def test_tick_boundaries(self, price: str, expected_tick: str) -> None:
        assert PriceService.tick_size_for(SecurityType.STOCK, Decimal(price)) == Decimal(expected_tick)


class TestTickSizeForEtf:
    @pytest.mark.parametrize(
        ("price", "expected_tick"),
        [
            # < 50 → 0.01
            ("0.01", "0.01"),
            ("1", "0.01"),
            ("49.99", "0.01"),
            # ≥ 50 → 0.05
            ("50", "0.05"),
            ("50.05", "0.05"),
            ("100", "0.05"),
            ("1000", "0.05"),
        ],
    )
    def test_tick_boundaries(self, price: str, expected_tick: str) -> None:
        assert PriceService.tick_size_for(SecurityType.ETF, Decimal(price)) == Decimal(expected_tick)


class TestIsValidTickStock:
    @pytest.mark.parametrize(
        "price",
        [
            "0.01",
            "9.99",
            "10.00",
            "10.05",
            "49.95",
            "50.0",
            "50.1",
            "99.9",
            "100.0",
            "100.5",
            "499.5",
            "500",
            "501",
            "999",
            "1000",
            "1005",
            "2000",
        ],
    )
    def test_valid_prices(self, price: str) -> None:
        assert PriceService.is_valid_tick(SecurityType.STOCK, Decimal(price)) is True

    @pytest.mark.parametrize(
        "price",
        [
            "9.999",  # < 10 range: 3 decimal places
            "10.01",  # 10–50 range: tick 0.05
            "10.04",
            "50.05",  # 50–100 range: tick 0.1
            "100.1",  # 100–500 range: tick 0.5
            "100.25",
            "500.5",  # 500–1000 range: tick 1
            "1001",  # ≥ 1000 range: tick 5
            "1002",
        ],
    )
    def test_invalid_prices(self, price: str) -> None:
        assert PriceService.is_valid_tick(SecurityType.STOCK, Decimal(price)) is False


class TestIsValidTickEtf:
    @pytest.mark.parametrize(
        "price",
        [
            "0.01",
            "1.00",
            "49.99",  # < 50 range: tick 0.01
            "50.00",
            "50.05",
            "100.00",  # ≥ 50 range: tick 0.05
            "1000.00",
        ],
    )
    def test_valid_prices(self, price: str) -> None:
        assert PriceService.is_valid_tick(SecurityType.ETF, Decimal(price)) is True

    @pytest.mark.parametrize(
        "price",
        [
            "49.999",  # < 50 range: 3 decimal places
            "50.01",  # ≥ 50 range: tick 0.05
            "50.04",
            "100.01",
            "100.04",
        ],
    )
    def test_invalid_prices(self, price: str) -> None:
        assert PriceService.is_valid_tick(SecurityType.ETF, Decimal(price)) is False


class TestValidate:
    def test_valid_stock_request(self) -> None:
        req = PriceRequest(type=SecurityType.STOCK, price="49.95", amount=500)
        assert PriceService.validate(req) == Decimal("49.95")

    def test_valid_etf_request(self) -> None:
        req = PriceRequest(type=SecurityType.ETF, price="50.05", amount=1000)
        assert PriceService.validate(req) == Decimal("50.05")

    def test_no_float_artifact(self) -> None:
        req = PriceRequest(type=SecurityType.STOCK, price="49.95", amount=500)
        assert str(PriceService.validate(req)) == "49.95"

    def test_invalid_tick_stock_raises_with_nearest(self) -> None:
        req = PriceRequest(type=SecurityType.STOCK, price="10.01", amount=500)
        with pytest.raises(InvalidTickSizeError) as exc_info:
            PriceService.validate(req)
        assert exc_info.value.nearest_lower == Decimal("10.00")
        assert exc_info.value.nearest_upper == Decimal("10.05")

    def test_invalid_tick_etf_raises_with_nearest(self) -> None:
        req = PriceRequest(type=SecurityType.ETF, price="50.01", amount=1000)
        with pytest.raises(InvalidTickSizeError) as exc_info:
            PriceService.validate(req)
        assert exc_info.value.nearest_lower == Decimal("50.00")
        assert exc_info.value.nearest_upper == Decimal("50.05")

    def test_unparseable_price_raises(self) -> None:
        req = PriceRequest(type=SecurityType.STOCK, price="bad", amount=500)
        with pytest.raises(InvalidPriceError, match="cannot be parsed"):
            PriceService.validate(req)

    def test_zero_amount_raises(self) -> None:
        req = PriceRequest(type=SecurityType.STOCK, price="49.95", amount=0)
        with pytest.raises(InvalidAmountError, match="greater than zero"):
            PriceService.validate(req)

    def test_negative_amount_raises(self) -> None:
        req = PriceRequest(type=SecurityType.STOCK, price="49.95", amount=-100)
        with pytest.raises(InvalidAmountError, match="greater than zero"):
            PriceService.validate(req)

    def test_amount_validated_before_price(self) -> None:
        # amount 錯誤優先於 price 錯誤拋出
        req = PriceRequest(type=SecurityType.STOCK, price="bad", amount=0)
        with pytest.raises(InvalidAmountError):
            PriceService.validate(req)

    def test_invalid_type_raises(self) -> None:
        # dataclass 不會在 runtime 強制型別，所以傳入非法 type 字串要被擋住
        req = PriceRequest(type="bond", price="50.05", amount=500)  # type: ignore[arg-type]
        with pytest.raises(InvalidTypeError, match="must be stock or etf"):
            PriceService.validate(req)

    def test_type_validated_before_price(self) -> None:
        # type 錯誤優先於 price 格式錯誤拋出
        req = PriceRequest(type="bond", price="bad", amount=500)  # type: ignore[arg-type]
        with pytest.raises(InvalidTypeError):
            PriceService.validate(req)

    def test_stock_vs_etf_same_price_different_validity(self) -> None:
        # 10.01 在 stock（tick 0.05）是非法的，但在 ETF（tick 0.01）是合法的
        stock_req = PriceRequest(type=SecurityType.STOCK, price="10.01", amount=500)
        etf_req = PriceRequest(type=SecurityType.ETF, price="10.01", amount=500)

        with pytest.raises(InvalidTickSizeError):
            PriceService.validate(stock_req)
        assert PriceService.validate(etf_req) == Decimal("10.01")


class TestNearest:
    @pytest.mark.parametrize(
        ("price", "expected_lower", "expected_upper"),
        [
            # < 10 → tick 0.01
            ("9.999", "9.99", "10.00"),
            ("0.005", "0.01", "0.01"),  # floor is 0.00 but clamped to 1 tick minimum
            # 10–50 → tick 0.05
            ("10.01", "10.00", "10.05"),
            ("10.04", "10.00", "10.05"),
            ("49.96", "49.95", "50.00"),
            # 50–100 → tick 0.1
            ("50.05", "50.0", "50.1"),
            ("99.95", "99.9", "100.0"),
            # 100–500 → tick 0.5
            ("100.1", "100.0", "100.5"),
            ("499.9", "499.5", "500.0"),
            # 500–1000 → tick 1
            ("500.5", "500", "501"),
            ("999.9", "999", "1000"),
            # ≥ 1000 → tick 5
            ("1001", "1000", "1005"),
            ("1003", "1000", "1005"),
        ],
    )
    def test_stock_nearest(self, price: str, expected_lower: str, expected_upper: str) -> None:
        p = Decimal(price)
        assert PriceService.nearest_lower(SecurityType.STOCK, p) == Decimal(expected_lower)
        assert PriceService.nearest_upper(SecurityType.STOCK, p) == Decimal(expected_upper)

    @pytest.mark.parametrize(
        ("price", "expected_lower", "expected_upper"),
        [
            # < 50 → tick 0.01
            ("49.999", "49.99", "50.00"),
            ("0.005", "0.01", "0.01"),  # floor is 0.00 but clamped to 1 tick minimum
            # ≥ 50 → tick 0.05
            ("50.01", "50.00", "50.05"),
            ("50.04", "50.00", "50.05"),
            ("100.01", "100.00", "100.05"),
        ],
    )
    def test_etf_nearest(self, price: str, expected_lower: str, expected_upper: str) -> None:
        p = Decimal(price)
        assert PriceService.nearest_lower(SecurityType.ETF, p) == Decimal(expected_lower)
        assert PriceService.nearest_upper(SecurityType.ETF, p) == Decimal(expected_upper)

    def test_valid_price_nearest_lower_equals_upper_equals_price(self) -> None:
        # 合法價格的 nearest_lower == nearest_upper == price 本身
        price = Decimal("49.95")
        assert PriceService.nearest_lower(SecurityType.STOCK, price) == price
        assert PriceService.nearest_upper(SecurityType.STOCK, price) == price
