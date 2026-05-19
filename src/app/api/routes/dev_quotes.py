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
    summary="推送開發用 quote（local mode 限定）",
    description=(
        "本地與整合測試用 endpoint，將 quote 寫入 in-memory store。"
        "Validation 失敗時不會覆蓋既有 snapshot。"
        "LOCAL_MODE=false 時 router 不會被掛載，呼叫會 404。"
    ),
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
