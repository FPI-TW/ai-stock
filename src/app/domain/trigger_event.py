from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.services.quote.base import QuoteSnapshot

VALID_TRIGGER_REFERENCE_PRICE_TYPES = frozenset({"ask", "bid", "last_fallback"})


def quote_snapshot_to_jsonb(snapshot: QuoteSnapshot) -> dict[str, Any]:
    """Serialize a QuoteSnapshot for the `quote_snapshot` JSONB column of both tracks
    (`trade_intent_triggers` on the new one, `trigger_events` on the legacy one).

    Prices kept as strings to preserve Decimal precision; datetimes as ISO 8601.
    Both broker times are recorded: `quote_time` gates the trigger itself, while
    `last_trade_time` is what the evaluator gates a `last_fallback` trigger on, so
    an auditor needs it to reconstruct why that price was accepted. It is written
    as an explicit null on book-only frames rather than omitted, so "no trade yet"
    is distinguishable from a row written before the key existed.

    Lives here (not next to QuoteSnapshot) because the serialization shape is
    dictated by the trigger storage contract, not the provider layer.
    """
    return {
        "symbol": snapshot.symbol,
        "bid_price": str(snapshot.bid_price) if snapshot.bid_price is not None else None,
        "ask_price": str(snapshot.ask_price) if snapshot.ask_price is not None else None,
        "last_price": str(snapshot.last_price) if snapshot.last_price is not None else None,
        "quote_time": snapshot.quote_time.isoformat(),
        "last_trade_time": snapshot.last_trade_time.isoformat() if snapshot.last_trade_time is not None else None,
        "received_at": snapshot.received_at.isoformat(),
    }


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
    filled_quantity_lots: int
    triggered_at: datetime
    created_at: datetime
    baseline_at_trigger: Decimal | None = None
    dynamic_trigger_price_at_trigger: Decimal | None = None
