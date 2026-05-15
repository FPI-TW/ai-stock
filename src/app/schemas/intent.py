from typing import Literal

from pydantic import BaseModel, Field


class IntentCreateRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int = Field(ge=1)


class IntentResponseData(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    status: str = "pending"


class IntentCreateResponse(BaseModel):
    data: IntentResponseData
