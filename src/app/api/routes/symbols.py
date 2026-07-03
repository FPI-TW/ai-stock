from fastapi import APIRouter, Query, status

from app.api.deps import SymbolServiceDep
from app.api.errors import ErrorResponse
from app.schemas.symbol import SingleSymbolResponse, SymbolListResponse, SymbolResponse

router = APIRouter()


@router.get(
    "",
    response_model=SymbolListResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "查詢參數不合法，例如 limit 超出 1–50（VALIDATION_ERROR）",
        },
    },
)
def list_symbols(
    service: SymbolServiceDep,
    q: str | None = Query(None, description="symbol prefix 或 display_name contains"),
    limit: int = Query(20, ge=1, le=50, description="預設 20，最大 50"),
) -> SymbolListResponse:
    symbols = service.lookup(q=q, limit=limit)
    return SymbolListResponse(data=[SymbolResponse.model_validate(s) for s in symbols])


@router.get(
    "/{symbol}",
    response_model=SingleSymbolResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "找不到此標的代號（UNKNOWN_SYMBOL）",
        },
    },
)
def get_symbol(
    service: SymbolServiceDep,
    symbol: str,
) -> SingleSymbolResponse:
    # 只做存在性查詢；tradable 驗證由 create intent 流程負責。
    symbol_obj = service.get_by_symbol(symbol)
    return SingleSymbolResponse(data=SymbolResponse.model_validate(symbol_obj))
