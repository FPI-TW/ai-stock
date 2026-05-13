from pydantic import BaseModel


class IntentCreateRequest(BaseModel):
    symbol: str
    side: str
    quantity: int


class IntentResponseData(BaseModel):
    symbol: str
    side: str
    quantity: int
    status: str = "pending"


class IntentCreateResponse(BaseModel):
    data: IntentResponseData
