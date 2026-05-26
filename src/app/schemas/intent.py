from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.api.schemas.base import OwnerScopedRequestModel
from app.domain.price import format_price_str
from app.domain.trade_intent import TradeIntentData


class _BaseIntentCreateRequest(OwnerScopedRequestModel):
    """extra='forbid' inherited — ownerUserId and unknown fields are rejected."""

    symbol: str
    quantity_lots: int = Field(validation_alias="quantityLots", ge=1)


class _TargetPriceIntentCreateRequest(_BaseIntentCreateRequest):
    target_price: str = Field(validation_alias="targetPrice")


class BuyPriceAlertCreateRequest(_TargetPriceIntentCreateRequest):
    strategy: Literal["buy_price_alert"]


class SellPriceAlertCreateRequest(_TargetPriceIntentCreateRequest):
    strategy: Literal["sell_price_alert"]


class _LimitOrderCreateRequest(_TargetPriceIntentCreateRequest):
    transaction_mode: Literal["single_notification", "partial_fill_allowed"] = Field(
        default="single_notification",
        validation_alias="transactionMode",
    )
    notification_mode: Literal["single"] = Field(
        default="single",
        validation_alias="notificationMode",
    )


class LimitBuyOrderCreateRequest(_LimitOrderCreateRequest):
    strategy: Literal["limit_buy_order"]


class LimitSellOrderCreateRequest(_LimitOrderCreateRequest):
    strategy: Literal["limit_sell_order"]


class TrailingStopAlertCreateRequest(_BaseIntentCreateRequest):
    strategy: Literal["trailing_stop_alert"]
    trail_mode: Literal["percentage", "fixed_amount"] = Field(validation_alias="trailMode")
    trail_value: Decimal = Field(validation_alias="trailValue", gt=0)

    @model_validator(mode="after")
    def _validate_percentage_trail_value(self) -> "TrailingStopAlertCreateRequest":
        if self.trail_mode == "percentage" and self.trail_value > Decimal("10"):
            message = "trailValue must be less than or equal to 10 when trailMode is percentage"
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                [
                    {
                        "type": "value_error",
                        "loc": ("trailValue",),
                        "input": self.trail_value,
                        "ctx": {"error": ValueError(message)},
                    }
                ],
            )
        return self


type IntentCreateRequest = Annotated[
    BuyPriceAlertCreateRequest
    | SellPriceAlertCreateRequest
    | LimitBuyOrderCreateRequest
    | LimitSellOrderCreateRequest
    | TrailingStopAlertCreateRequest,
    Field(discriminator="strategy"),
]


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
    target_price_original: str | None = Field(serialization_alias="targetPriceOriginal")
    target_price_effective: str | None = Field(serialization_alias="targetPriceEffective")
    trading_date: date = Field(serialization_alias="tradingDate")
    time_in_force: str = Field(serialization_alias="timeInForce")
    execution_mode: str = Field(serialization_alias="executionMode")
    status: str
    transaction_mode: str = Field(serialization_alias="transactionMode")
    notification_mode: str = Field(serialization_alias="notificationMode")
    filled_quantity_lots: int = Field(serialization_alias="filledQuantityLots")
    last_fill_at: datetime | None = Field(default=None, serialization_alias="lastFillAt")
    trail_mode: str | None = Field(default=None, serialization_alias="trailMode")
    trail_value: str | None = Field(default=None, serialization_alias="trailValue")
    baseline: str | None = None
    dynamic_trigger_price: str | None = Field(default=None, serialization_alias="dynamicTriggerPrice")
    baseline_updated_at: datetime | None = Field(default=None, serialization_alias="baselineUpdatedAt")
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
    target_price_original = (
        format_price_str(intent.target_price_original) if intent.target_price_original is not None else None
    )
    target_price_effective = (
        format_price_str(intent.target_price_effective) if intent.target_price_effective is not None else None
    )
    return IntentResponseData(
        id=intent.id,
        symbol=intent.symbol,
        strategy=intent.strategy,
        quantity_lots=intent.quantity_lots,
        target_price_original=target_price_original,
        target_price_effective=target_price_effective,
        trading_date=intent.trading_date,
        time_in_force=intent.time_in_force,
        execution_mode=intent.execution_mode,
        status=intent.status,
        transaction_mode=intent.transaction_mode,
        notification_mode=intent.notification_mode,
        filled_quantity_lots=intent.filled_quantity_lots,
        last_fill_at=intent.last_fill_at,
        trail_mode=intent.trail_mode,
        trail_value=format_price_str(intent.trail_value) if intent.trail_value else None,
        baseline=format_price_str(intent.baseline) if intent.baseline else None,
        dynamic_trigger_price=format_price_str(intent.dynamic_trigger_price) if intent.dynamic_trigger_price else None,
        baseline_updated_at=intent.baseline_updated_at,
        created_at=intent.created_at,
        cancelled_at=intent.cancelled_at,
    )
