from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas.base import OwnerScopedRequestModel
from app.domain.trade_intent import TradeIntentData


class IntentCreateRequest(OwnerScopedRequestModel):
    """extra='forbid' inherited — ownerUserId and unknown fields are rejected."""

    symbol: str
    strategy: Literal["buy_price_alert", "sell_price_alert"]
    quantityLots: int = Field(ge=1)
    targetPrice: str


class IntentResponseData(BaseModel):
    id: UUID
    symbol: str
    strategy: str
    quantityLots: int
    targetPriceOriginal: str
    targetPriceEffective: str
    tradingDate: date
    timeInForce: str
    executionMode: str
    status: str
    createdAt: datetime
    cancelledAt: datetime | None = None


class IntentCreateResponse(BaseModel):
    data: IntentResponseData


class IntentListResponse(BaseModel):
    data: list[IntentResponseData]
    nextCursor: str | None = None
    pageSize: int


class IntentDetailResponse(BaseModel):
    data: IntentResponseData


def _decimal_str(value: Decimal) -> str:
    s = format(value, "f")
    integer_part, _, decimal_part = s.partition(".")
    decimal_part = (decimal_part or "").rstrip("0").ljust(2, "0")
    return f"{integer_part}.{decimal_part}"


def map_to_response_data(intent: TradeIntentData) -> IntentResponseData:
    return IntentResponseData(
        id=intent.id,
        symbol=intent.symbol,
        strategy=intent.strategy,
        quantityLots=intent.quantity_lots,
        targetPriceOriginal=_decimal_str(intent.target_price_original),
        targetPriceEffective=_decimal_str(intent.target_price_effective),
        tradingDate=intent.trading_date,
        timeInForce=intent.time_in_force,
        executionMode=intent.execution_mode,
        status=intent.status,
        createdAt=intent.created_at,
        cancelledAt=intent.cancelled_at,
    )
