from pydantic import BaseModel, ConfigDict, Field


class SymbolBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    display_name: str = Field(serialization_alias="displayName")
    market: str
    instrument_type: str = Field(serialization_alias="instrumentType")
    tradable_status: str = Field(serialization_alias="tradableStatus")


class SymbolResponse(SymbolBase):
    pass


class SymbolListResponse(BaseModel):
    data: list[SymbolResponse]


class SingleSymbolResponse(BaseModel):
    data: SymbolResponse
