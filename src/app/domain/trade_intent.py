from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

CANCELLABLE_STATUSES = frozenset({"active", "scheduled"})
TERMINAL_STATUSES = frozenset({"triggered", "cancelled"})
VALID_STATUSES = frozenset({"active", "scheduled", "triggered", "cancelled"})


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


@dataclass(frozen=True)
class TradeIntentData:
    id: UUID
    owner_user_id: UUID
    symbol: str
    strategy: str
    execution_mode: str
    quantity_lots: int
    target_price_original: Decimal
    target_price_effective: Decimal
    trigger_reference_price_type: str
    trading_date: date
    time_in_force: str
    status: str
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None
    triggered_at: datetime | None = None
