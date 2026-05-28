"""GET /quotes — batch read of latest QuoteProvider snapshots.

Not gated behind LOCAL_MODE: V1 will expose this publicly so the gate would
just churn frontends. Symbol validation reuses SymbolService.get_by_symbol
(raises UnknownSymbolError → 404 via existing handler). Per-symbol snapshot
fetch absorbs QuoteUnavailableError into stale=True so a cold cache for one
symbol does not kill the batch.
"""

from fastapi import APIRouter, Query

from app.api.deps import CurrentPriceProviderDep, CurrentPriceSymbolDep, QuoteLookupServiceDep
from app.schemas.quote import CurrentPriceResponse, QuoteListResponse, map_current_price

router = APIRouter()


@router.get("", response_model=QuoteListResponse)
def list_quotes(
    service: QuoteLookupServiceDep,
    symbols: str | None = Query(None, description="逗號分隔的 symbol 代號，最多 50 個（例：2330,2317）"),
) -> QuoteListResponse:
    return QuoteListResponse(data=service.lookup(symbols))


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
