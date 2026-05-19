"""Dev quote endpoints — local-only (LOCAL_MODE=true).

Router is included by app.main.create_app() only when settings.local_mode is
true; when local_mode is false the routes do not exist and FastAPI returns
404 by default. See BE-V0.5-08.
"""

from fastapi import APIRouter, status

from app.api.deps import DevQuoteIngestServiceDep
from app.api.errors import ApiError, ErrorCode
from app.schemas.dev_quote import (
    DevQuoteFetchResponse,
    DevQuoteFetchResponseData,
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


@router.get(
    "/quotes/{symbol}",
    response_model=DevQuoteFetchResponse,
    status_code=status.HTTP_200_OK,
    summary="查詢開發用 quote snapshot（local mode 限定）",
    description=(
        "讀取 in-memory store 的目前 snapshot。查無資料回 404。LOCAL_MODE=false 時 router 不會被掛載，呼叫會 404。"
    ),
)
def get_dev_quote(
    service: DevQuoteIngestServiceDep,
    symbol: str,
) -> DevQuoteFetchResponse:
    snapshot = service.get_snapshot(symbol)
    if snapshot is None:
        raise ApiError(ErrorCode.QUOTE_NOT_FOUND, status.HTTP_404_NOT_FOUND)
    return DevQuoteFetchResponse(
        data=DevQuoteFetchResponseData(
            symbol=snapshot.symbol,
            bid_price=snapshot.bid_price,
            ask_price=snapshot.ask_price,
            last_price=snapshot.last_price,
            quote_time=snapshot.quote_time,
        )
    )
