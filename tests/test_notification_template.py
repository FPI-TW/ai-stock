"""Unit tests for notification template — BE-V0.5-09."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.notification_template import (
    render_limit_order_triggered,
    render_price_triggered,
)

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")


class TestRenderPriceTriggered:
    def test_buy_title_contains_symbol_and_trigger_phrase(self) -> None:
        title, _ = render_price_triggered(
            symbol="2330",
            strategy="buy_price_alert",
            target_price=Decimal("100"),
            trigger_price=Decimal("99"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
        )
        assert title == "2330 到價提醒已觸發"

    def test_buy_body_contains_required_fields(self) -> None:
        _, body = render_price_triggered(
            symbol="2330",
            strategy="buy_price_alert",
            target_price=Decimal("100"),
            trigger_price=Decimal("99.5"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
        )
        assert "買進到價提醒" in body
        assert "2330" in body
        assert "100.00" in body
        assert "99.50" in body
        assert "2026-05-11 10:00:05" in body
        assert "僅通知、未下單、不保證成交" in body

    def test_sell_body_uses_sell_label(self) -> None:
        _, body = render_price_triggered(
            symbol="2330",
            strategy="sell_price_alert",
            target_price=Decimal("100"),
            trigger_price=Decimal("101"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
        )
        assert "賣出到價提醒" in body
        assert "買進到價提醒" not in body

    def test_quote_time_renders_in_taipei_timezone(self) -> None:
        # UTC 02:00 = Taipei 10:00 — body must show Taipei wall-clock time.
        _, body = render_price_triggered(
            symbol="2330",
            strategy="buy_price_alert",
            target_price=Decimal("100"),
            trigger_price=Decimal("99"),
            quote_time=datetime(2026, 5, 11, 2, 0, 5, tzinfo=UTC),
        )
        assert "2026-05-11 10:00:05" in body
        assert "2026-05-11 02:00:05" not in body

    def test_price_with_no_decimals_pads_to_two(self) -> None:
        _, body = render_price_triggered(
            symbol="2330",
            strategy="buy_price_alert",
            target_price=Decimal("100"),
            trigger_price=Decimal("99"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
        )
        assert "100.00" in body
        assert "99.00" in body

    def test_price_preserves_extra_precision(self) -> None:
        _, body = render_price_triggered(
            symbol="2330",
            strategy="buy_price_alert",
            target_price=Decimal("100.1234"),
            trigger_price=Decimal("99.5"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
        )
        assert "100.1234" in body

    def test_unsupported_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported strategy"):
            render_price_triggered(
                symbol="2330",
                strategy="take_profit_alert",
                target_price=Decimal("100"),
                trigger_price=Decimal("99"),
                quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
            )

    def test_naive_quote_time_raises(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            render_price_triggered(
                symbol="2330",
                strategy="buy_price_alert",
                target_price=Decimal("100"),
                trigger_price=Decimal("99"),
                quote_time=datetime(2026, 5, 11, 10, 0, 5),
            )


class TestRenderLimitOrderTriggered:
    """BE-V0.5-15 — limit_order_triggered notification body must include strategy
    label, fill/order quantity, Taipei-formatted quote time, and the
    notify-only disclaimer."""

    def test_limit_buy_title(self) -> None:
        title, _ = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_buy_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("599"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
            quantity_lots=2,
            filled_quantity_lots=2,
        )
        assert title == "2330 限價買單已觸發"

    def test_limit_sell_title(self) -> None:
        title, _ = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_sell_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("601"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
            quantity_lots=1,
            filled_quantity_lots=1,
        )
        assert title == "2330 限價賣單已觸發"

    def test_body_contains_strategy_label_and_quantities(self) -> None:
        _, body = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_buy_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("599"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
            quantity_lots=3,
            filled_quantity_lots=3,
        )
        assert "限價買單" in body
        assert "成交 3 張 / 委託 3 張" in body
        assert "600.00" in body
        assert "599.00" in body
        assert "2026-05-11 10:00:05" in body
        assert "僅通知、未下單、不保證成交" in body

    def test_body_uses_sell_label_for_limit_sell(self) -> None:
        _, body = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_sell_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("601"),
            quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
            quantity_lots=1,
            filled_quantity_lots=1,
        )
        assert "限價賣單" in body
        assert "限價買單" not in body

    def test_quote_time_renders_in_taipei_timezone(self) -> None:
        _, body = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_buy_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("599"),
            quote_time=datetime(2026, 5, 11, 2, 0, 5, tzinfo=UTC),
            quantity_lots=1,
            filled_quantity_lots=1,
        )
        assert "2026-05-11 10:00:05" in body

    def test_unsupported_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported strategy"):
            render_limit_order_triggered(
                symbol="2330",
                strategy="buy_price_alert",
                target_price=Decimal("100"),
                trigger_price=Decimal("99"),
                quote_time=datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI),
                quantity_lots=1,
                filled_quantity_lots=1,
            )

    def test_naive_quote_time_raises(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            render_limit_order_triggered(
                symbol="2330",
                strategy="limit_buy_order",
                target_price=Decimal("100"),
                trigger_price=Decimal("99"),
                quote_time=datetime(2026, 5, 11, 10, 0, 5),
                quantity_lots=1,
                filled_quantity_lots=1,
            )
