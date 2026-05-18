"""POST /dev/quotes — local-only endpoint to push quotes into the dev provider.

Router is included by app.main.create_app() only when settings.local_mode is
true; when local_mode is false the route does not exist and FastAPI returns
404 by default. See BE-V0.5-08.
"""

from fastapi import APIRouter, status

from app.api.deps import DevQuoteIngestServiceDep
from app.schemas.dev_quote import (
    DevQuoteUpsertRequest,
    DevQuoteUpsertResponse,
    DevQuoteUpsertResponseData,
)

router = APIRouter()


@router.post(
    "/quotes",
    response_model=DevQuoteUpsertResponse,
    status_code=status.HTTP_200_OK,
)
def upsert_dev_quote(
    service: DevQuoteIngestServiceDep,
    request: DevQuoteUpsertRequest,
) -> DevQuoteUpsertResponse:
    service.ingest(
        symbol=request.symbol,
        bid_price=request.bid_price,
        ask_price=request.ask_price,
        last_price=request.last_price,
        quote_time=request.quote_time,
    )
    return DevQuoteUpsertResponse(data=DevQuoteUpsertResponseData(symbol=request.symbol, updated=True))
