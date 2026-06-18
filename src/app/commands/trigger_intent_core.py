"""新軌觸發 persist（模式丙 PR2 chunk2）。

對應 legacy `commands/trigger_intent.persist_trigger`，但寫入新軌：
- 觸發稽核 → `TradeIntentTrigger`（FK→trade_intent_core）
- 通知 → 共用 `notifications`（`trade_intent_core_id` 指向新軌）
- 狀態 → `trade_intent_core.status` active→triggered（帶 WHERE status='active' 防競態）

重用共用元件、不複製：notification 文案模板、telegram 派送、`MARKET_ORDER_STRATEGIES`、
`TriggerError`。與 legacy `trigger_intent.py` 零互相 import，退役時不受影響。

入參用攤平的 `TradeIntentData`（dispatcher / 建單路徑都已持有），故毋需存取衛星
relationship。`triggered_at` 留給 DB `func.now()`：同一交易內每次 NOW() 回交易起始
時刻，trigger row 與 status UPDATE 的時間戳自然一致。
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, update
from sqlalchemy.orm import Session

from app.db.models.core import Notification as NotificationRow
from app.db.models.trade_intent_core import TradeIntentCore, TradeIntentTrigger
from app.domain.trade_intent import MARKET_ORDER_STRATEGIES, TradeIntentData
from app.domain.trigger_event import TriggerError
from app.services.notification_template import (
    render_limit_order_triggered,
    render_market_order_triggered,
    render_price_triggered,
    render_trailing_stop_triggered,
)
from app.services.telegram_notification import dispatch_notification_to_telegram


class CoreIntentNotActiveError(TriggerError):
    """新軌委託在觸發當下已非 active（被取消 / 已觸發 / 競態搶先）。"""

    def __init__(self, intent_id: UUID, current_status: str) -> None:
        self.intent_id = intent_id
        self.current_status = current_status
        super().__init__(f"Intent {intent_id} cannot be triggered: status is {current_status!r}")


@dataclass(frozen=True)
class TriggerCoreInput:
    intent_id: UUID
    trigger_price: Decimal
    trigger_reference_price_type: str
    fallback_used: bool
    quote_snapshot: dict[str, Any]
    quote_time: datetime


def persist_core_trigger(
    db: Session,
    intent: TradeIntentData,
    inp: TriggerCoreInput,
) -> tuple[TradeIntentTrigger, NotificationRow]:
    """寫 trigger + notification + 更新 status='triggered'，不 commit（caller 擁有交易）。

    `rowcount == 0` 代表 status guard 未過（已非 active）→ raise，讓 caller 回滾已暫存
    的 trigger / notification。`trade_intent_triggers.trade_intent_id` 的 UNIQUE 是最終
    防線（重複觸發），由 caller 把 IntegrityError 轉成對應錯誤。
    """

    notification_type = "price_triggered"
    target_price_effective = intent.target_price_effective
    baseline_at_trigger: Decimal | None = None
    dynamic_trigger_price_at_trigger: Decimal | None = None

    if intent.strategy in MARKET_ORDER_STRATEGIES:
        notification_type = "market_order_triggered"
        target_price_effective = inp.trigger_price
        title, body = render_market_order_triggered(
            symbol=intent.symbol,
            strategy=intent.strategy,
            trigger_price=inp.trigger_price,
            filled_quantity_lots=intent.quantity_lots,
            quantity_lots=intent.quantity_lots,
            quote_time=inp.quote_time,
        )
    elif intent.strategy in {"limit_buy_order", "limit_sell_order"}:
        if intent.target_price_effective is None:
            raise RuntimeError("limit order trigger requires target_price_effective")
        notification_type = "limit_order_triggered"
        title, body = render_limit_order_triggered(
            symbol=intent.symbol,
            strategy=intent.strategy,
            target_price=intent.target_price_effective,
            trigger_price=inp.trigger_price,
            filled_quantity_lots=intent.quantity_lots,
            quantity_lots=intent.quantity_lots,
            quote_time=inp.quote_time,
        )
    elif intent.strategy == "trailing_stop_alert":
        if intent.baseline is None or intent.dynamic_trigger_price is None:
            raise RuntimeError("trailing trigger requires baseline and dynamic_trigger_price")
        notification_type = "trailing_stop_triggered"
        target_price_effective = intent.dynamic_trigger_price
        baseline_at_trigger = intent.baseline
        dynamic_trigger_price_at_trigger = intent.dynamic_trigger_price
        title, body = render_trailing_stop_triggered(
            symbol=intent.symbol,
            trail_mode=cast(str, intent.trail_mode),
            trail_value=cast(Decimal, intent.trail_value),
            baseline=intent.baseline,
            dynamic_trigger_price=intent.dynamic_trigger_price,
            trigger_price=inp.trigger_price,
            trigger_reference_price_type=inp.trigger_reference_price_type,
            quote_time=inp.quote_time,
        )
    else:
        if intent.target_price_effective is None:
            raise RuntimeError("price alert trigger requires target_price_effective")
        title, body = render_price_triggered(
            symbol=intent.symbol,
            strategy=intent.strategy,
            target_price=intent.target_price_effective,
            trigger_price=inp.trigger_price,
            quote_time=inp.quote_time,
        )

    if target_price_effective is None:
        raise RuntimeError("trigger requires target_price_effective")

    trigger_row = TradeIntentTrigger(
        id=uuid4(),
        trade_intent_id=intent.id,
        owner_user_id=intent.owner_user_id,
        symbol=intent.symbol,
        quote_snapshot=inp.quote_snapshot,
        target_price_effective=target_price_effective,
        trigger_price=inp.trigger_price,
        trigger_reference_price_type=inp.trigger_reference_price_type,
        fallback_used=inp.fallback_used,
        filled_quantity_lots=intent.quantity_lots,
        baseline_at_trigger=baseline_at_trigger,
        dynamic_trigger_price_at_trigger=dynamic_trigger_price_at_trigger,
    )
    notification_row = NotificationRow(
        id=uuid4(),
        owner_user_id=intent.owner_user_id,
        trade_intent_core_id=intent.id,
        type=notification_type,
        rendered_title=title,
        rendered_body=body,
    )

    db.add(trigger_row)
    result = cast(
        CursorResult[Any],
        db.execute(
            update(TradeIntentCore)
            .where(TradeIntentCore.id == intent.id, TradeIntentCore.status == "active")
            .values(
                status="triggered",
                triggered_at=func.now(),
                updated_at=func.now(),
                filled_quantity_lots=intent.quantity_lots,
                last_fill_at=func.now(),
            ),
        ),
    )
    if result.rowcount == 0:
        raise CoreIntentNotActiveError(intent.id, "stale")
    db.add(notification_row)
    db.flush()
    dispatch_notification_to_telegram(notification_row)
    return trigger_row, notification_row
