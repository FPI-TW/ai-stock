"""Domain values for the small Telegram-assisted intent workflow."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

CapabilityId = Literal["limit_buy", "limit_sell", "market_buy", "market_sell"]
DecisionType = Literal["unsupported", "needs_clarification", "draft_intent", "ambiguous"]
InteractionKind = Literal["clarification", "draft"]
InteractionStatus = Literal["pending", "confirming", "confirmed", "cancelled", "expired", "superseded"]

LIMIT_CAPABILITIES: frozenset[str] = frozenset({"limit_buy", "limit_sell"})
MARKET_CAPABILITIES: frozenset[str] = frozenset({"market_buy", "market_sell"})

CAPABILITY_TO_NOTIFY_ONLY_STRATEGY: dict[CapabilityId, str] = {
    "limit_buy": "limit_buy_order",
    "limit_sell": "limit_sell_order",
    "market_buy": "market_buy_order",
    "market_sell": "market_sell_order",
}
CAPABILITY_TO_TRANSACTION_MODE: dict[CapabilityId, str] = {
    "limit_buy": "single_notification",
    "limit_sell": "single_notification",
    "market_buy": "partial_fill_allowed",
    "market_sell": "partial_fill_allowed",
}
CAPABILITY_LABELS: dict[CapabilityId, str] = {
    "limit_buy": "限價買進提醒",
    "limit_sell": "限價賣出提醒",
    "market_buy": "市價買進提醒",
    "market_sell": "市價賣出提醒",
}


class TelegramIntentError(Exception):
    """Base error for user-safe Telegram intent failures."""


class TelegramIntentLlmError(TelegramIntentError):
    """The classifier response could not be trusted."""


@dataclass(frozen=True, slots=True)
class TelegramIntentDecision:
    decision: DecisionType
    capability_id: CapabilityId | None = None
    symbol: str | None = None
    quantity_lots: int | None = None
    target_price: Decimal | None = None
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TelegramIntentInteractionData:
    id: UUID
    owner_user_id: UUID
    telegram_chat_id: str
    kind: InteractionKind
    capability_id: CapabilityId
    strategy: str
    payload: dict[str, object]
    missing_fields: list[str]
    clarification_attempt_count: int
    status: InteractionStatus
    expires_at: datetime
    created_trade_intent_id: UUID | None = None
    bot_message_id: int | None = None
    confirmed_at: datetime | None = None
