"""Unit tests for notification template — BE-V0.5-09."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.notification_template import render_price_triggered, render_trailing_stop_triggered

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


class TestRenderTrailingStopTriggered:
    """Spec §202-247 wording requirements."""

    QUOTE_TIME = datetime(2026, 5, 25, 13, 25, 12, tzinfo=TAIPEI)

    def test_title_is_direction_agnostic(self) -> None:
        title, _ = render_trailing_stop_triggered(
            symbol="2330",
            position_side="long",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            watermark=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
            trigger_price=Decimal("94.8"),
            trigger_reference_price_type="bid",
            quote_time=self.QUOTE_TIME,
        )
        assert title == "2330 移動出場已觸發"

    def test_long_percentage_body_format(self) -> None:
        # Matches spec §219-229 example.
        _, body = render_trailing_stop_triggered(
            symbol="2330",
            position_side="long",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            watermark=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
            trigger_price=Decimal("94.8"),
            trigger_reference_price_type="bid",
            quote_time=self.QUOTE_TIME,
        )
        assert "策略：移動出場（多單）" in body
        assert "移動幅度：5%（百分比模式）" in body
        assert "今日最高價：100.00" in body
        assert "觸發價（最高 × 95%）：95.00" in body
        assert "實際觸發成交價：94.80（bid）" in body
        assert "報價時間：2026-05-25 13:25:12" in body
        assert "僅通知、未下單、不保證成交" in body

    def test_short_fixed_amount_body_format(self) -> None:
        # Matches spec §234-245 example structure.
        _, body = render_trailing_stop_triggered(
            symbol="2330",
            position_side="short",
            trail_mode="fixed_amount",
            trail_value=Decimal("5"),
            watermark=Decimal("100"),
            dynamic_trigger_price=Decimal("105"),
            trigger_price=Decimal("105.2"),
            trigger_reference_price_type="ask",
            quote_time=self.QUOTE_TIME,
        )
        assert "策略：移動出場（空單）" in body
        assert "固定金額模式" in body
        assert "今日最低價：100.00" in body
        # formula text uses format_price_str (≥2 decimals) for the trail value
        assert "觸發價（最低 + 5.00）：105.00" in body
        assert "實際觸發成交價：105.20（ask）" in body

    def test_last_fallback_label(self) -> None:
        _, body = render_trailing_stop_triggered(
            symbol="2330",
            position_side="long",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            watermark=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
            trigger_price=Decimal("94"),
            trigger_reference_price_type="last_fallback",
            quote_time=self.QUOTE_TIME,
        )
        assert "實際觸發成交價：94.00（last）" in body

    def test_unsupported_position_side_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported position_side"):
            render_trailing_stop_triggered(
                symbol="2330",
                position_side="both",
                trail_mode="percentage",
                trail_value=Decimal("5"),
                watermark=Decimal("100"),
                dynamic_trigger_price=Decimal("95"),
                trigger_price=Decimal("94"),
                trigger_reference_price_type="bid",
                quote_time=self.QUOTE_TIME,
            )

    def test_unsupported_trail_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported trail_mode"):
            render_trailing_stop_triggered(
                symbol="2330",
                position_side="long",
                trail_mode="absolute",
                trail_value=Decimal("5"),
                watermark=Decimal("100"),
                dynamic_trigger_price=Decimal("95"),
                trigger_price=Decimal("94"),
                trigger_reference_price_type="bid",
                quote_time=self.QUOTE_TIME,
            )

    def test_naive_quote_time_raises(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            render_trailing_stop_triggered(
                symbol="2330",
                position_side="long",
                trail_mode="percentage",
                trail_value=Decimal("5"),
                watermark=Decimal("100"),
                dynamic_trigger_price=Decimal("95"),
                trigger_price=Decimal("94"),
                trigger_reference_price_type="bid",
                quote_time=datetime(2026, 5, 25, 13, 25, 12),
            )
