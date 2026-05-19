"""Request / response schema for /dev/quotes endpoints (BE-V0.5-08).

Endpoints are registered only when LOCAL_MODE=true. Request payload uses camelCase
via Pydantic alias; extra fields are rejected. Decimal price fields require
string input — bool / int / float are rejected to prevent silent precision loss
or `True → Decimal(1)` coercion.
"""

from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class DevQuoteUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    bid_price: Decimal | None = Field(default=None, alias="bidPrice")
    ask_price: Decimal | None = Field(default=None, alias="askPrice")
    last_price: Decimal | None = Field(default=None, alias="lastPrice")
    quote_time: AwareDatetime = Field(alias="quoteTime")

    @field_validator("bid_price", "ask_price", "last_price", mode="before")
    @classmethod
    def _require_string_or_none(cls, value: object) -> object:
        if value is None or isinstance(value, str):
            return value
        # 拒絕 bool/int/float：Pydantic 預設會把 True 視為 1 coerce 為 Decimal(1)，
        # float 也可能帶入二進位精度誤差；V0.5 規範要求價格用字串傳輸。
        raise ValueError(f"Price must be provided as a string for decimal precision; got {type(value).__name__}")


class DevQuoteUpsertResponseData(BaseModel):
    symbol: str
    updated: bool


class DevQuoteUpsertResponse(BaseModel):
    data: DevQuoteUpsertResponseData


class DevQuoteFetchResponseData(BaseModel):
    symbol: str
    bid_price: Decimal | None = Field(default=None, serialization_alias="bidPrice")
    ask_price: Decimal | None = Field(default=None, serialization_alias="askPrice")
    last_price: Decimal | None = Field(default=None, serialization_alias="lastPrice")
    quote_time: AwareDatetime = Field(serialization_alias="quoteTime")


class DevQuoteFetchResponse(BaseModel):
    data: DevQuoteFetchResponseData
