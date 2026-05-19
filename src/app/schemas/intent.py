from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.api.schemas.base import OwnerScopedRequestModel
from app.domain.trade_intent import TradeIntentData


class IntentCreateRequest(OwnerScopedRequestModel):
    """extra='forbid' inherited — ownerUserId and unknown fields are rejected."""

    symbol: str
    strategy: Literal["buy_price_alert", "sell_price_alert"]
    quantityLots: int = Field(ge=1)
    targetPrice: str


class IntentResponseData(OwnerScopedRequestModel):
    model_config = OwnerScopedRequestModel.model_config  # type: ignore[assignment]

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


class IntentCreateResponse(OwnerScopedRequestModel):
    data: IntentResponseData


class IntentListResponse(OwnerScopedRequestModel):
    data: list[IntentResponseData]
    nextCursor: str | None = None
    pageSize: int


class IntentDetailResponse(OwnerScopedRequestModel):
    data: IntentResponseData


def _decimal_str(value: object) -> str:
    s = format(value, "f")  # type: ignore[call-overload]
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


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
    )
