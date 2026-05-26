"""Notification rendering — BE-V0.5-09 minimal `price_triggered` template.

Keeps title/body construction out of the evaluator and trigger transaction.
V1 will introduce template versioning and rendered snapshot metadata; until
then this module is the single source of truth for the wording.
"""

from datetime import datetime
from decimal import Decimal

from app.domain.price import format_price_str
from app.domain.trading_session import TAIPEI_TZ

_STRATEGY_LABELS = {
    "buy_price_alert": "買進到價提醒",
    "sell_price_alert": "賣出到價提醒",
}

_LIMIT_ORDER_LABELS = {
    "limit_buy_order": "限價買單",
    "limit_sell_order": "限價賣單",
}

_DISCLAIMER = "僅通知、未下單、不保證成交。"


def render_price_triggered(
    *,
    symbol: str,
    strategy: str,
    target_price: Decimal,
    trigger_price: Decimal,
    quote_time: datetime,
) -> tuple[str, str]:
    """Return ``(title, body)`` for a `price_triggered` notification."""
    if strategy not in _STRATEGY_LABELS:
        raise ValueError(f"Unsupported strategy for notification: {strategy!r}")
    if quote_time.tzinfo is None:
        raise TypeError(f"quote_time must be timezone-aware: {quote_time!r}")

    title = f"{symbol} 到價提醒已觸發"
    quote_time_taipei = quote_time.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    body_lines = [
        _STRATEGY_LABELS[strategy],
        "",
        f"標的：{symbol}",
        f"目標價：{format_price_str(target_price)}",
        f"觸發價：{format_price_str(trigger_price)}",
        f"報價時間：{quote_time_taipei}",
        "",
        _DISCLAIMER,
    ]
    return title, "\n".join(body_lines)


def render_limit_order_triggered(
    *,
    symbol: str,
    strategy: str,
    target_price: Decimal,
    trigger_price: Decimal,
    filled_quantity_lots: int,
    quantity_lots: int,
    quote_time: datetime,
) -> tuple[str, str]:
    if strategy not in _LIMIT_ORDER_LABELS:
        raise ValueError(f"Unsupported limit order strategy for notification: {strategy!r}")
    if quote_time.tzinfo is None:
        raise TypeError(f"quote_time must be timezone-aware: {quote_time!r}")

    label = _LIMIT_ORDER_LABELS[strategy]
    quote_time_taipei = quote_time.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    title = f"{symbol} {label}已觸發"
    body_lines = [
        f"策略：{label}",
        f"標的：{symbol}",
        f"目標價：{format_price_str(target_price)}",
        f"觸發價：{format_price_str(trigger_price)}",
        f"成交 {filled_quantity_lots} 張 / 委託 {quantity_lots} 張",
        f"報價時間：{quote_time_taipei}",
        "",
        _DISCLAIMER,
    ]
    return title, "\n".join(body_lines)


def render_trailing_stop_triggered(
    *,
    symbol: str,
    trail_mode: str,
    trail_value: Decimal,
    baseline: Decimal,
    dynamic_trigger_price: Decimal,
    trigger_price: Decimal,
    trigger_reference_price_type: str,
    quote_time: datetime,
) -> tuple[str, str]:
    if trail_mode not in {"percentage", "fixed_amount"}:
        raise ValueError(f"Unsupported trail mode for notification: {trail_mode!r}")
    if quote_time.tzinfo is None:
        raise TypeError(f"quote_time must be timezone-aware: {quote_time!r}")

    quote_time_taipei = quote_time.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    if trail_mode == "percentage":
        mode_label = "百分比模式"
        trail_text = f"{format_price_str(trail_value).rstrip('0').rstrip('.')}%"
        trigger_formula = f"最高 x {format_price_str(Decimal('100') - trail_value).rstrip('0').rstrip('.')}%"
    else:
        mode_label = "固定點數模式"
        trail_text = f"NT${format_price_str(trail_value)}"
        trigger_formula = f"最高 - {format_price_str(trail_value).rstrip('0').rstrip('.')}"

    title = f"{symbol} 移動出場已觸發"
    body_lines = [
        "策略：移動出場",
        f"移動幅度：{trail_text}（{mode_label}）",
        f"今日最高價：{format_price_str(baseline)}",
        f"觸發價（{trigger_formula}）：{format_price_str(dynamic_trigger_price)}",
        f"實際觸發成交價：{format_price_str(trigger_price)}（{trigger_reference_price_type}）",
        f"報價時間：{quote_time_taipei}",
        "",
        _DISCLAIMER,
    ]
    return title, "\n".join(body_lines)
