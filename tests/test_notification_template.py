"""Unit tests for notification template — BE-V0.5-09."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.notification_template import (
    render_limit_order_triggered,
    render_market_order_triggered,
    render_price_triggered,
    render_trailing_stop_triggered,
    render_twap_price_followup,
    render_twap_slice,
)

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")
QUOTE_TIME = datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI)


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
    def test_limit_buy_body_contains_fill_and_disclaimer(self) -> None:
        title, body = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_buy_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("599.5"),
            filled_quantity_lots=1,
            quantity_lots=1,
            quote_time=QUOTE_TIME,
        )

        assert title == "2330 限價買單已觸發"
        assert "策略：限價買單" in body
        assert "成交 1 張 / 委託 1 張" in body
        assert "僅通知" in body

    def test_limit_sell_title(self) -> None:
        title, _ = render_limit_order_triggered(
            symbol="2330",
            strategy="limit_sell_order",
            target_price=Decimal("600"),
            trigger_price=Decimal("601"),
            filled_quantity_lots=2,
            quantity_lots=2,
            quote_time=QUOTE_TIME,
        )

        assert title == "2330 限價賣單已觸發"

    def test_naive_quote_time_raises(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            render_limit_order_triggered(
                symbol="2330",
                strategy="limit_buy_order",
                target_price=Decimal("600"),
                trigger_price=Decimal("599.5"),
                filled_quantity_lots=1,
                quantity_lots=1,
                quote_time=datetime(2026, 5, 11, 10, 0, 5),
            )


class TestRenderMarketOrderTriggered:
    def test_market_order_body_contains_fill_and_reference_price(self) -> None:
        title, body = render_market_order_triggered(
            symbol="2330",
            strategy="market_buy_order",
            trigger_price=Decimal("600"),
            filled_quantity_lots=1,
            quantity_lots=1,
            quote_time=QUOTE_TIME,
        )

        assert title == "2330 市價買單已觸發"
        assert "策略：市價買單" in body
        assert "成交參考價：600.00" in body
        assert "成交 1 張 / 委託 1 張" in body

    def test_naive_quote_time_raises(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            render_market_order_triggered(
                symbol="2330",
                strategy="market_order",
                trigger_price=Decimal("600"),
                filled_quantity_lots=1,
                quantity_lots=1,
                quote_time=datetime(2026, 5, 11, 10, 0, 5),
            )


class TestRenderTrailingStopTriggered:
    def test_percentage_body_contains_baseline_dynamic_price_and_disclaimer(self) -> None:
        title, body = render_trailing_stop_triggered(
            symbol="2330",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            baseline=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
            trigger_price=Decimal("94.8"),
            trigger_reference_price_type="bid",
            quote_time=QUOTE_TIME,
        )

        assert title == "2330 移動出場已觸發"
        assert "策略：移動出場" in body
        assert "今日最高價：100.00" in body
        assert "觸發價（最高 × 95%）：95.00" in body
        assert "95.00" in body
        assert "實際觸發成交價：94.80（bid）" in body
        assert "僅通知" in body

    def test_fixed_amount_body_contains_mode(self) -> None:
        _, body = render_trailing_stop_triggered(
            symbol="2330",
            trail_mode="fixed_amount",
            trail_value=Decimal("5"),
            baseline=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
            trigger_price=Decimal("94.8"),
            trigger_reference_price_type="bid",
            quote_time=QUOTE_TIME,
        )

        assert "固定點數模式" in body
        assert "NT$5.00" in body

    def test_naive_quote_time_raises(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            render_trailing_stop_triggered(
                symbol="2330",
                trail_mode="percentage",
                trail_value=Decimal("5"),
                baseline=Decimal("100"),
                dynamic_trigger_price=Decimal("95"),
                trigger_price=Decimal("94.8"),
                trigger_reference_price_type="bid",
                quote_time=datetime(2026, 5, 11, 10, 0, 5),
            )


class TestRenderTwapNotifications:
    def test_twap_slice_with_price_is_concise(self) -> None:
        title, body = render_twap_slice(
            symbol="2330",
            position_side="long",
            sequence_no=3,
            total_slices=10,
            planned_quantity_lots=10,
            reference_price=Decimal("590.5"),
            reference_price_type="ask",
            quote_time=QUOTE_TIME,
        )

        assert title == "2330 TWAP 第 3/10 筆"
        assert "多單建倉" in body
        assert "建議市價買入：10 張" in body
        assert "參考價：590.50（ask）" in body
        assert "報價時間：2026-05-11 10:00:05" in body

    def test_twap_slice_without_price_warns_to_confirm_market_price(self) -> None:
        _, body = render_twap_slice(
            symbol="2330",
            position_side="short",
            sequence_no=1,
            total_slices=2,
            planned_quantity_lots=1,
            reference_price=None,
            reference_price_type=None,
            quote_time=None,
        )

        assert "空單建倉" in body
        assert "建議市價賣出：1 張" in body
        assert "目前行情暫不可用，請自行確認市價。" in body

    def test_twap_followup_includes_sent_time(self) -> None:
        title, body = render_twap_price_followup(
            symbol="2330",
            sequence_no=3,
            total_slices=10,
            reference_price=Decimal("590.5"),
            reference_price_type="ask",
            sent_at=QUOTE_TIME,
        )

        assert title == "2330 TWAP 候補價格"
        assert "第 3/10 筆參考價：590.50（ask）" in body
        assert "補發時間：2026-05-11 10:00:05" in body
        assert "此為稍後補發的價格資訊。" in body
