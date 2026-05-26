from fastapi import APIRouter, status

from app.api.deps import QuoteProviderDep, SettingsDep
from app.api.errors import ApiError, ErrorCode
from app.core.config import REQUIRED_CURRENT_PRICE_PROVIDER
from app.schemas.quote import CurrentPriceResponse, map_current_price

CURRENT_PRICE_ALLOWED_SYMBOLS: frozenset[str] = frozenset({"2330", "2317", "0050", "00878"})

router = APIRouter()


@router.get("/current-price/{symbol}", response_model=CurrentPriceResponse)
def get_current_price(
    symbol: str,
    settings: SettingsDep,
    quote_provider: QuoteProviderDep,
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
    if settings.quote_provider != REQUIRED_CURRENT_PRICE_PROVIDER:
        raise ApiError(
            code=ErrorCode.QUOTE_PROVIDER_UNAVAILABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="此測試 API 依賴 broker demo quote provider，無法在其他 provider 下使用",
            details={"requiredProvider": REQUIRED_CURRENT_PRICE_PROVIDER, "currentProvider": settings.quote_provider},
        )

    return CurrentPriceResponse(data=map_current_price(quote_provider.get_current_price(symbol)))
