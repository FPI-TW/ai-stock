"""Request / response schema for POST /dev/quotes (BE-V0.5-08).

Endpoint is registered only when LOCAL_MODE=true. Request payload uses camelCase
via Pydantic alias; extra fields are rejected.
"""

from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class DevQuoteUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    bid_price: Decimal | None = Field(default=None, alias="bidPrice")
    ask_price: Decimal | None = Field(default=None, alias="askPrice")
    last_price: Decimal | None = Field(default=None, alias="lastPrice")
    quote_time: AwareDatetime = Field(alias="quoteTime")


class DevQuoteUpsertResponseData(BaseModel):
    symbol: str
    updated: bool


class DevQuoteUpsertResponse(BaseModel):
    data: DevQuoteUpsertResponseData
