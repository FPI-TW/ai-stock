from fastapi import APIRouter, Query

from app.api.symbol_deps import SymbolServiceDep
from app.schemas.symbol import SingleSymbolResponse, SymbolListResponse, SymbolResponse

router = APIRouter()


@router.get("", response_model=SymbolListResponse)
def list_symbols(
    service: SymbolServiceDep,
    q: str | None = Query(None, description="symbol prefix 或 display_name contains"),
    limit: int = Query(20, ge=1, le=50, description="預設 20，最大 50"),
) -> SymbolListResponse:
    symbols = service.lookup(q=q, limit=limit)
    return SymbolListResponse(data=[SymbolResponse.model_validate(s) for s in symbols])


@router.get("/{symbol}", response_model=SingleSymbolResponse)
def get_symbol(
    service: SymbolServiceDep,
    symbol: str,
) -> SingleSymbolResponse:
    # 只做存在性查詢；tradable 驗證由 create intent 流程負責。
    symbol_obj = service.find_by_symbol(symbol)
    return SingleSymbolResponse(data=SymbolResponse.model_validate(symbol_obj))
