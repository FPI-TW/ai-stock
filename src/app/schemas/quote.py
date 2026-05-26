from datetime import datetime

from pydantic import BaseModel, Field

from app.core.config import CURRENT_PRICE_SOURCE_NAME
from app.domain.price import format_price_str
from app.services.quote.base import QuoteSnapshot


class CurrentPriceData(BaseModel):
    symbol: str
    current_price: str | None = Field(serialization_alias="currentPrice")
    bid_price: str | None = Field(serialization_alias="bidPrice")
    ask_price: str | None = Field(serialization_alias="askPrice")
    quote_time: datetime = Field(serialization_alias="quoteTime")
    received_at: datetime = Field(serialization_alias="receivedAt")
    source: str
    test_feature: bool = Field(serialization_alias="testFeature")


class CurrentPriceResponse(BaseModel):
    data: CurrentPriceData


def map_current_price(snapshot: QuoteSnapshot) -> CurrentPriceData:
    return CurrentPriceData(
        symbol=snapshot.symbol,
        current_price=format_price_str(snapshot.last_price) if snapshot.last_price is not None else None,
        bid_price=format_price_str(snapshot.bid_price) if snapshot.bid_price is not None else None,
        ask_price=format_price_str(snapshot.ask_price) if snapshot.ask_price is not None else None,
        quote_time=snapshot.quote_time,
        received_at=snapshot.received_at,
        source=CURRENT_PRICE_SOURCE_NAME,
        test_feature=True,
    )
