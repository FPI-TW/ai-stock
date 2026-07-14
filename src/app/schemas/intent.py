from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.api.schemas.base import OwnerScopedRequestModel
from app.domain.price import format_price_str
from app.domain.trade_intent import TradeIntentData, TwapSliceData
from app.domain.twap import TwapPlan


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


class MarketOrderCreateRequest(_BaseIntentCreateRequest):
    strategy: Literal["market_order", "market_buy_order", "market_sell_order"]
    transaction_mode: Literal["partial_fill_allowed"] = Field(
        default="partial_fill_allowed",
        validation_alias="transactionMode",
    )
    notification_mode: Literal["single"] = Field(
        default="single",
        validation_alias="notificationMode",
    )


class TrailingStopAlertCreateRequest(_BaseIntentCreateRequest):
    strategy: Literal["trailing_stop_alert"]
    trail_mode: Literal["percentage", "fixed_amount"] = Field(validation_alias="trailMode")
    trail_value: Decimal = Field(validation_alias="trailValue", gt=0)

    @model_validator(mode="after")
    def _validate_percentage_trail_value(self) -> "TrailingStopAlertCreateRequest":
        if self.trail_mode != "percentage":
            return self
        message: str | None = None
        if self.trail_value > Decimal("10"):
            message = "trailValue must be less than or equal to 10 when trailMode is percentage"
        elif self.trail_value.quantize(Decimal("0.0001")) != self.trail_value:
            # trail_value 入庫為 Numeric(9,4)，但 dedup_key 用未捨入輸入建指紋。若容許 >4 位，
            # 5.00001 與 5.00002 會產生不同 key、雙雙躲過去重，卻都捨入成 5.0000 → 兩筆等價
            # active intent 並存（違反去重不退化）。故輸入精度對齊儲存精度，超過即拒。
            message = "trailValue supports at most 4 decimal places"
        if message is not None:
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
    | MarketOrderCreateRequest
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
    twap: "TwapSummaryData | None" = None
    created_at: datetime = Field(serialization_alias="createdAt")
    cancelled_at: datetime | None = Field(default=None, serialization_alias="cancelledAt")


class TwapSummaryData(BaseModel):
    position_side: str = Field(serialization_alias="positionSide")
    interval_seconds: int = Field(serialization_alias="intervalSeconds")
    start_time: time = Field(serialization_alias="startTime")
    end_time: time = Field(serialization_alias="endTime")
    start_at: datetime = Field(serialization_alias="startAt")
    end_at: datetime = Field(serialization_alias="endAt")
    available_slice_count: int = Field(serialization_alias="availableSliceCount")
    materialized_slice_count: int = Field(serialization_alias="materializedSliceCount")


class TwapSliceResponseData(BaseModel):
    id: UUID | None = None
    sequence_no: int = Field(serialization_alias="sequenceNo")
    scheduled_at: datetime = Field(serialization_alias="scheduledAt")
    planned_quantity_lots: int = Field(serialization_alias="plannedQuantityLots")
    status: str | None = None
    primary_notification_id: UUID | None = Field(default=None, serialization_alias="primaryNotificationId")
    notified_at: datetime | None = Field(default=None, serialization_alias="notifiedAt")
    primary_price_available: bool | None = Field(default=None, serialization_alias="primaryPriceAvailable")
    primary_reference_price: str | None = Field(default=None, serialization_alias="primaryReferencePrice")
    primary_reference_price_type: str | None = Field(default=None, serialization_alias="primaryReferencePriceType")
    primary_quote_time: datetime | None = Field(default=None, serialization_alias="primaryQuoteTime")
    price_followup_required: bool | None = Field(default=None, serialization_alias="priceFollowupRequired")
    price_followup_attempts: int | None = Field(default=None, serialization_alias="priceFollowupAttempts")
    next_price_followup_at: datetime | None = Field(default=None, serialization_alias="nextPriceFollowupAt")
    price_followup_notification_id: UUID | None = Field(default=None, serialization_alias="priceFollowupNotificationId")
    price_followup_sent_at: datetime | None = Field(default=None, serialization_alias="priceFollowupSentAt")


class IntentCreateResponse(BaseModel):
    data: IntentResponseData


class IntentListResponse(BaseModel):
    data: list[IntentResponseData]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
    page_size: int = Field(serialization_alias="pageSize")


class IntentDetailResponseData(IntentResponseData):
    twap_slices: list[TwapSliceResponseData] | None = Field(default=None, serialization_alias="twapSlices")


class IntentDetailResponse(BaseModel):
    data: IntentDetailResponseData


class TwapPlanRequest(OwnerScopedRequestModel):
    symbol: str
    position_side: Literal["long", "short"] = Field(validation_alias="positionSide")
    quantity_lots: int = Field(validation_alias="quantityLots")
    interval_seconds: int = Field(validation_alias="intervalSeconds")
    start_time: time | None = Field(default=None, validation_alias="startTime")
    end_time: time = Field(validation_alias="endTime")

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _validate_time_precision(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            parts = value.split(":")
            if len(parts) == 2 and all(len(part) == 2 and part.isdigit() for part in parts):
                hour = int(parts[0])
                minute = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return time(hour, minute)
        raise PydanticCustomError("twap_time_format", "startTime and endTime must use HH:MM format")


class TwapPlanData(BaseModel):
    symbol: str
    strategy: Literal["twap_order"] = "twap_order"
    position_side: Literal["long", "short"] = Field(serialization_alias="positionSide")
    trading_phase: str = Field(serialization_alias="tradingPhase")
    trading_date: date = Field(serialization_alias="tradingDate")
    target_quantity_lots: int = Field(serialization_alias="targetQuantityLots")
    interval_seconds: int = Field(serialization_alias="intervalSeconds")
    start_time: time = Field(serialization_alias="startTime")
    end_time: time = Field(serialization_alias="endTime")
    start_at: datetime = Field(serialization_alias="startAt")
    end_at: datetime = Field(serialization_alias="endAt")
    available_slice_count: int = Field(serialization_alias="availableSliceCount")
    materialized_slice_count: int = Field(serialization_alias="materializedSliceCount")
    slices: list[TwapSliceResponseData]


class TwapPreviewResponse(BaseModel):
    data: TwapPlanData


class TwapConfirmResponse(BaseModel):
    data: IntentDetailResponseData


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
        twap=map_twap_summary(intent),
        created_at=intent.created_at,
        cancelled_at=intent.cancelled_at,
    )


def map_to_detail_response_data(
    intent: TradeIntentData,
    twap_slices: list[TwapSliceData] | None = None,
) -> IntentDetailResponseData:
    base = map_to_response_data(intent)
    return IntentDetailResponseData(
        **base.model_dump(),
        twap_slices=[map_twap_slice_to_response_data(s) for s in twap_slices] if twap_slices is not None else None,
    )


def map_twap_summary(intent: TradeIntentData) -> TwapSummaryData | None:
    if intent.strategy != "twap_order":
        return None
    if (
        intent.position_side is None
        or intent.twap_interval_seconds is None
        or intent.twap_end_time is None
        or intent.twap_start_at is None
        or intent.twap_end_at is None
        or intent.twap_available_slice_count is None
        or intent.twap_materialized_slice_count is None
    ):
        raise RuntimeError(f"TWAP intent missing summary fields: {intent.id}")
    return TwapSummaryData(
        position_side=intent.position_side,
        interval_seconds=intent.twap_interval_seconds,
        start_time=intent.twap_start_at.timetz().replace(tzinfo=None),
        end_time=intent.twap_end_time,
        start_at=intent.twap_start_at,
        end_at=intent.twap_end_at,
        available_slice_count=intent.twap_available_slice_count,
        materialized_slice_count=intent.twap_materialized_slice_count,
    )


def map_twap_plan(symbol: str, plan: TwapPlan) -> TwapPlanData:
    return TwapPlanData(
        symbol=symbol,
        position_side=cast(Literal["long", "short"], plan.position_side),
        trading_phase=plan.trading_phase.value,
        trading_date=plan.trading_date,
        target_quantity_lots=plan.target_quantity_lots,
        interval_seconds=plan.interval_seconds,
        start_time=plan.requested_start_time,
        end_time=plan.requested_end_time,
        start_at=plan.start_at,
        end_at=plan.end_at,
        available_slice_count=plan.available_slice_count,
        materialized_slice_count=plan.materialized_slice_count,
        slices=[
            TwapSliceResponseData(
                sequence_no=s.sequence_no,
                scheduled_at=s.scheduled_at,
                planned_quantity_lots=s.planned_quantity_lots,
            )
            for s in plan.slices
        ],
    )


def map_twap_slice_to_response_data(slice_data: TwapSliceData) -> TwapSliceResponseData:
    return TwapSliceResponseData(
        id=slice_data.id,
        sequence_no=slice_data.sequence_no,
        scheduled_at=slice_data.scheduled_at,
        planned_quantity_lots=slice_data.planned_quantity_lots,
        status=slice_data.status,
        primary_notification_id=slice_data.primary_notification_id,
        notified_at=slice_data.notified_at,
        primary_price_available=slice_data.primary_price_available,
        primary_reference_price=(
            format_price_str(slice_data.primary_reference_price)
            if slice_data.primary_reference_price is not None
            else None
        ),
        primary_reference_price_type=slice_data.primary_reference_price_type,
        primary_quote_time=slice_data.primary_quote_time,
        price_followup_required=slice_data.price_followup_required,
        price_followup_attempts=slice_data.price_followup_attempts,
        next_price_followup_at=slice_data.next_price_followup_at,
        price_followup_notification_id=slice_data.price_followup_notification_id,
        price_followup_sent_at=slice_data.price_followup_sent_at,
    )
