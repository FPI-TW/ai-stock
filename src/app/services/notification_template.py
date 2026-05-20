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
