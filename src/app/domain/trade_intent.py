from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from app.domain.price import SecurityType

CANCELLABLE_STATUSES = frozenset({"active", "scheduled"})
TERMINAL_STATUSES = frozenset({"triggered", "cancelled"})
VALID_STATUSES = frozenset({"active", "scheduled", "triggered", "cancelled"})
BUY_SIDE_STRATEGIES = frozenset({"buy_price_alert", "limit_buy_order"})
SELL_SIDE_STRATEGIES = frozenset({"sell_price_alert", "limit_sell_order", "trailing_stop_alert"})


class IntentError(Exception):
    pass


class DuplicateIntentError(IntentError):
    def __init__(self, owner_user_id: UUID, symbol: str, strategy: str) -> None:
        self.owner_user_id = owner_user_id
        self.symbol = symbol
        self.strategy = strategy
        super().__init__(f"Duplicate intent for {strategy}/{symbol}")


class IntentNotFoundError(IntentError):
    def __init__(self, intent_id: UUID) -> None:
        self.intent_id = intent_id
        super().__init__(f"Intent not found: {intent_id}")


class CancelNotAllowedError(IntentError):
    def __init__(self, intent_id: UUID, current_status: str) -> None:
        self.intent_id = intent_id
        self.current_status = current_status
        super().__init__(f"Cannot cancel intent {intent_id!r} with status {current_status!r}")


class InvalidCursorError(IntentError):
    def __init__(self, cursor_id: UUID) -> None:
        self.cursor_id = cursor_id
        super().__init__(f"Cursor not found or expired: {cursor_id}")


def derive_order_side(strategy: str) -> str:
    if strategy in BUY_SIDE_STRATEGIES:
        return "buy"
    if strategy in SELL_SIDE_STRATEGIES:
        return "sell"
    raise ValueError(f"Unsupported strategy: {strategy}")


@dataclass(frozen=True)
class TradeIntentData:
    id: UUID
    owner_user_id: UUID
    symbol: str
    strategy: str
    execution_mode: str
    quantity_lots: int
    target_price_original: Decimal | None
    target_price_effective: Decimal | None
    trigger_reference_price_type: str
    trading_date: date
    time_in_force: str
    status: str
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None
    triggered_at: datetime | None = None
    transaction_mode: str = "single_notification"
    notification_mode: str = "single"
    filled_quantity_lots: int = 0
    last_fill_at: datetime | None = None
    trail_mode: str | None = None
    trail_value: Decimal | None = None
    baseline: Decimal | None = None
    dynamic_trigger_price: Decimal | None = None
    baseline_updated_at: datetime | None = None
    security_type: SecurityType = SecurityType.STOCK
    position_side: str | None = None
    twap_interval_seconds: int | None = None
    twap_end_time: time | None = None
    twap_start_at: datetime | None = None
    twap_end_at: datetime | None = None
    twap_available_slice_count: int | None = None
    twap_materialized_slice_count: int | None = None


@dataclass(frozen=True)
class TwapSliceData:
    id: UUID
    trade_intent_id: UUID
    owner_user_id: UUID
    symbol: str
    sequence_no: int
    scheduled_at: datetime
    planned_quantity_lots: int
    status: str
    primary_notification_id: UUID | None
    notified_at: datetime | None
    primary_price_available: bool | None
    primary_reference_price: Decimal | None
    primary_reference_price_type: str | None
    primary_quote_time: datetime | None
    price_followup_required: bool
    price_followup_attempts: int
    next_price_followup_at: datetime | None
    price_followup_notification_id: UUID | None
    price_followup_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
