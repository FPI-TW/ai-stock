from pydantic import BaseModel, ConfigDict, Field


class SymbolBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    symbol: str
    display_name: str = Field(alias="displayName")
    market: str
    instrument_type: str = Field(alias="instrumentType")
    tradable_status: str = Field(alias="tradableStatus")


class SymbolResponse(SymbolBase):
    pass


class SymbolListResponse(BaseModel):
    data: list[SymbolResponse]


class SingleSymbolResponse(BaseModel):
    data: SymbolResponse
