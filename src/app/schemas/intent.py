from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas.base import OwnerScopedRequestModel
from app.domain.price import format_price_str
from app.domain.trade_intent import TradeIntentData


class IntentCreateRequest(OwnerScopedRequestModel):
    """extra='forbid' inherited — ownerUserId and unknown fields are rejected."""

    symbol: str
    strategy: Literal["buy_price_alert", "sell_price_alert"]
    quantity_lots: int = Field(validation_alias="quantityLots", ge=1)
    target_price: str = Field(validation_alias="targetPrice")


class IntentResponseData(BaseModel):
    """Response shape for a single trade intent.

    Note: ``triggered_at`` (present on ``TradeIntentData``) is intentionally omitted.
    V0.5 uses notify-only mode; the trigger flow and its timestamp are not yet exposed
    to clients. Add it here when the trigger detail endpoint is introduced.
    """

    id: UUID
    symbol: str
    strategy: str
    quantity_lots: int = Field(serialization_alias="quantityLots")
    target_price_original: str = Field(serialization_alias="targetPriceOriginal")
    target_price_effective: str = Field(serialization_alias="targetPriceEffective")
    trading_date: date = Field(serialization_alias="tradingDate")
    time_in_force: str = Field(serialization_alias="timeInForce")
    execution_mode: str = Field(serialization_alias="executionMode")
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")
    cancelled_at: datetime | None = Field(default=None, serialization_alias="cancelledAt")


class IntentCreateResponse(BaseModel):
    data: IntentResponseData


class IntentListResponse(BaseModel):
    data: list[IntentResponseData]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
    page_size: int = Field(serialization_alias="pageSize")


class IntentDetailResponse(BaseModel):
    data: IntentResponseData


def map_to_response_data(intent: TradeIntentData) -> IntentResponseData:
    return IntentResponseData(
        id=intent.id,
        symbol=intent.symbol,
        strategy=intent.strategy,
        quantity_lots=intent.quantity_lots,
        target_price_original=format_price_str(intent.target_price_original),
        target_price_effective=format_price_str(intent.target_price_effective),
        trading_date=intent.trading_date,
        time_in_force=intent.time_in_force,
        execution_mode=intent.execution_mode,
        status=intent.status,
        created_at=intent.created_at,
        cancelled_at=intent.cancelled_at,
    )
