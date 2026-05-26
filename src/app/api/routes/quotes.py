from fastapi import APIRouter

from app.api.deps import CurrentPriceProviderDep, CurrentPriceSymbolDep
from app.schemas.quote import CurrentPriceResponse, map_current_price

router = APIRouter()


@router.get("/current-price/{symbol}", response_model=CurrentPriceResponse)
def get_current_price(
    symbol: CurrentPriceSymbolDep,
    current_price_provider: CurrentPriceProviderDep,
) -> CurrentPriceResponse:
    """Test-only broker-backed current price lookup.

    This endpoint intentionally depends on the configured broker demo provider;
    it is not a standalone quote API and should be removed or redesigned with
    the V1 licensed provider.
    """

    return CurrentPriceResponse(data=map_current_price(current_price_provider.get_current_price(symbol)))
