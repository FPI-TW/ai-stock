from fastapi import APIRouter, status

from app.api.deps import CurrentPriceProviderDep
from app.api.errors import ApiError, ErrorCode
from app.schemas.quote import CurrentPriceResponse, map_current_price

CURRENT_PRICE_ALLOWED_SYMBOLS: frozenset[str] = frozenset({"2330", "2317", "0050", "00878"})

router = APIRouter()


@router.get("/current-price/{symbol}", response_model=CurrentPriceResponse)
def get_current_price(
    symbol: str,
    current_price_provider: CurrentPriceProviderDep,
) -> CurrentPriceResponse:
    """Test-only broker-backed current price lookup.

    This endpoint intentionally depends on the configured broker demo provider;
    it is not a standalone quote API and should be removed or redesigned with
    the V1 licensed provider.
    """

    if symbol not in CURRENT_PRICE_ALLOWED_SYMBOLS:
        raise ApiError(
            code=ErrorCode.CURRENT_PRICE_SYMBOL_NOT_ALLOWED,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details={"symbol": symbol, "allowed": sorted(CURRENT_PRICE_ALLOWED_SYMBOLS)},
        )
    return CurrentPriceResponse(data=map_current_price(current_price_provider.get_current_price(symbol)))
