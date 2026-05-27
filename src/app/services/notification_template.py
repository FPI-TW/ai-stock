"""Notification rendering — `price_triggered` (BE-V0.5-09) and
`trailing_stop_triggered` (BE-V0.5-16) templates.

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

_POSITION_SIDE_LABELS = {
    "long": "多單",
    "short": "空單",
}

_TRIGGER_REF_LABELS = {
    "bid": "bid",
    "ask": "ask",
    "last_fallback": "last",
}

_DISCLAIMER = "僅通知、未下單、不保證成交。"


def _format_compact_decimal(value: Decimal) -> str:
    """Render a Decimal without trailing zeros (e.g. ``5`` not ``5.00``).

    `format_price_str` always pads to ≥2 decimals, which suits prices but
    reads oddly for percentages and the (100 ± trail) values embedded in the
    trailing notification formula. This compacts to the user-entered shape.
    """
    s = f"{value.normalize():f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


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


def render_trailing_stop_triggered(
    *,
    symbol: str,
    position_side: str,
    trail_mode: str,
    trail_value: Decimal,
    watermark: Decimal,
    dynamic_trigger_price: Decimal,
    trigger_price: Decimal,
    trigger_reference_price_type: str,
    quote_time: datetime,
) -> tuple[str, str]:
    """Return ``(title, body)`` for a `trailing_stop_triggered` notification.

    Spec §202-247: title is direction-agnostic ("移動出場已觸發"); body labels
    differ by position_side (多單→今日最高價, 空單→今日最低價) and trail_mode
    (percentage vs fixed_amount). The "觸發價" line embeds a short formula so
    users can verify how the dynamic price was derived from the watermark.
    """
    if position_side not in _POSITION_SIDE_LABELS:
        raise ValueError(f"Unsupported position_side for notification: {position_side!r}")
    if trail_mode not in ("percentage", "fixed_amount"):
        raise ValueError(f"Unsupported trail_mode for notification: {trail_mode!r}")
    if trigger_reference_price_type not in _TRIGGER_REF_LABELS:
        raise ValueError(f"Unsupported trigger_reference_price_type: {trigger_reference_price_type!r}")
    if quote_time.tzinfo is None:
        raise TypeError(f"quote_time must be timezone-aware: {quote_time!r}")

    side_label = _POSITION_SIDE_LABELS[position_side]
    quote_time_taipei = quote_time.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    ref_label = _TRIGGER_REF_LABELS[trigger_reference_price_type]

    if trail_mode == "percentage":
        trail_line = f"移動幅度：{_format_compact_decimal(trail_value)}%（百分比模式）"
        if position_side == "long":
            # long pct: dynamic = watermark × (1 − trail/100) → 顯示為 "最高 × (100-trail)%"
            formula = f"最高 × {_format_compact_decimal(Decimal(100) - trail_value)}%"
        else:
            formula = f"最低 × {_format_compact_decimal(Decimal(100) + trail_value)}%"
    else:
        trail_line = f"移動幅度：NT${format_price_str(trail_value)}（固定金額模式）"
        if position_side == "long":
            formula = f"最高 − {format_price_str(trail_value)}"
        else:
            formula = f"最低 + {format_price_str(trail_value)}"

    watermark_label = "今日最高價" if position_side == "long" else "今日最低價"

    title = f"{symbol} 移動出場已觸發"
    body_lines = [
        f"策略：移動出場（{side_label}）",
        trail_line,
        f"{watermark_label}：{format_price_str(watermark)}",
        f"觸發價（{formula}）：{format_price_str(dynamic_trigger_price)}",
        f"實際觸發成交價：{format_price_str(trigger_price)}（{ref_label}）",
        f"報價時間：{quote_time_taipei}",
        "",
        _DISCLAIMER,
    ]
    return title, "\n".join(body_lines)
