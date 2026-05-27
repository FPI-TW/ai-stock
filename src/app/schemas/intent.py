from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic_core import PydanticCustomError

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
    position_side: Literal["long", "short"] = Field(validation_alias="positionSide")
    trail_mode: Literal["percentage", "fixed_amount"] = Field(validation_alias="trailMode")
    trail_value: Decimal = Field(validation_alias="trailValue", gt=0)

    @model_validator(mode="after")
    def _validate_percentage_trail_value(self) -> "TrailingStopAlertCreateRequest":
        if self.trail_mode == "percentage" and self.trail_value > Decimal("50"):
            # `PydanticCustomError` keeps the loc pointing at `trailValue` while
            # producing a JSON-serializable representation in `exc.errors()` —
            # the raw `value_error` + `ctx={"error": ValueError(...)}` path
            # leaks a Decimal/ValueError into the API envelope JSON encoder.
            raise ValidationError.from_exception_data(
                self.__class__.__name__,
                [
                    {
                        "type": PydanticCustomError(
                            "value_error",
                            "trailValue must be less than or equal to 50 when trailMode is percentage",
                        ),
                        "loc": ("trailValue",),
                        "input": str(self.trail_value),
                    }
                ],
            )
        return self


# V0.5 階段 union 暴露 buy/sell_price_alert 與 trailing_stop_alert。
# LimitBuy/Sell 子 schema 定義保留供 BE-V0.5-15 接手納入 union;
# evaluator / migration 上線前不開放,避免 schema 通過後 command 邊界以 500 收尾。
# trailing 的 command 路徑寫入 DB,evaluator (watermark / dynamic trigger /
# 即時觸發 / 通知 template) 由後續 PR 接;在那之前 trailing row 建立後處於
# active 狀態但不會被觸發。
type IntentCreateRequest = Annotated[
    BuyPriceAlertCreateRequest | SellPriceAlertCreateRequest | TrailingStopAlertCreateRequest,
    Field(discriminator="strategy"),
]


class IntentResponseData(BaseModel):
    """Response shape for a single trade intent.

    Note: ``triggered_at`` (present on ``TradeIntentData``) is intentionally omitted.
    V0.5 uses notify-only mode; the trigger flow and its timestamp are not yet exposed
    to clients. Add it here when the trigger detail endpoint is introduced.

    Trailing-only fields (positionSide / trailMode / trailValue / watermarkHigh /
    watermarkLow / dynamicTriggerPrice / watermarkUpdatedAt) are None for
    buy/sell rows; target_price_* are None for trailing rows. The DB ck
    `trailing_field_exclusivity` enforces this invariant.
    """

    id: UUID
    symbol: str
    strategy: str
    quantity_lots: int = Field(serialization_alias="quantityLots")
    target_price_original: str | None = Field(default=None, serialization_alias="targetPriceOriginal")
    target_price_effective: str | None = Field(default=None, serialization_alias="targetPriceEffective")
    trading_date: date = Field(serialization_alias="tradingDate")
    time_in_force: str = Field(serialization_alias="timeInForce")
    execution_mode: str = Field(serialization_alias="executionMode")
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")
    cancelled_at: datetime | None = Field(default=None, serialization_alias="cancelledAt")
    position_side: str | None = Field(default=None, serialization_alias="positionSide")
    trail_mode: str | None = Field(default=None, serialization_alias="trailMode")
    trail_value: str | None = Field(default=None, serialization_alias="trailValue")
    watermark_high: str | None = Field(default=None, serialization_alias="watermarkHigh")
    watermark_low: str | None = Field(default=None, serialization_alias="watermarkLow")
    dynamic_trigger_price: str | None = Field(default=None, serialization_alias="dynamicTriggerPrice")
    watermark_updated_at: datetime | None = Field(default=None, serialization_alias="watermarkUpdatedAt")


class IntentCreateResponse(BaseModel):
    data: IntentResponseData


class IntentListResponse(BaseModel):
    data: list[IntentResponseData]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
    page_size: int = Field(serialization_alias="pageSize")


class IntentDetailResponse(BaseModel):
    data: IntentResponseData


def map_to_response_data(intent: TradeIntentData) -> IntentResponseData:
    # Mutually-exclusive field shape per DB ck `trailing_field_exclusivity`:
    # buy/sell rows expose target_price_*, trailing rows expose the trailing
    # triple + watermark/dynamic fields. The mapper just forwards what the
    # domain object carries; the DB guarantees both column groups are never
    # populated simultaneously.
    return IntentResponseData(
        id=intent.id,
        symbol=intent.symbol,
        strategy=intent.strategy,
        quantity_lots=intent.quantity_lots,
        target_price_original=(
            format_price_str(intent.target_price_original) if intent.target_price_original is not None else None
        ),
        target_price_effective=(
            format_price_str(intent.target_price_effective) if intent.target_price_effective is not None else None
        ),
        trading_date=intent.trading_date,
        time_in_force=intent.time_in_force,
        execution_mode=intent.execution_mode,
        status=intent.status,
        created_at=intent.created_at,
        cancelled_at=intent.cancelled_at,
        position_side=intent.position_side,
        trail_mode=intent.trail_mode,
        # trail_value is NOT a price — `percentage` mode carries a percentage,
        # `fixed_amount` mode carries a TWD amount. `format_price_str` pads to
        # ≥2 decimals which is wrong for both; surface the Decimal directly so
        # the response preserves DB precision (Numeric(9, 4)).
        trail_value=(str(intent.trail_value) if intent.trail_value is not None else None),
        watermark_high=(format_price_str(intent.watermark_high) if intent.watermark_high is not None else None),
        watermark_low=(format_price_str(intent.watermark_low) if intent.watermark_low is not None else None),
        dynamic_trigger_price=(
            format_price_str(intent.dynamic_trigger_price) if intent.dynamic_trigger_price is not None else None
        ),
        watermark_updated_at=intent.watermark_updated_at,
    )
