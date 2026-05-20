from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

VALID_TRIGGER_REFERENCE_PRICE_TYPES = frozenset({"ask", "bid", "last_fallback"})


class TriggerError(Exception):
    pass


class DuplicateTriggerError(TriggerError):
    def __init__(self, trade_intent_id: UUID) -> None:
        self.trade_intent_id = trade_intent_id
        super().__init__(f"Trigger already exists for intent {trade_intent_id}")


@dataclass(frozen=True)
class TriggerEventData:
    id: UUID
    trade_intent_id: UUID
    owner_user_id: UUID
    symbol: str
    quote_snapshot: dict[str, Any]
    target_price_effective: Decimal
    trigger_price: Decimal
    trigger_reference_price_type: str
    fallback_used: bool
    triggered_at: datetime
    created_at: datetime
