from fastapi import APIRouter, status

from app.api.symbol_deps import SymbolServiceDep
from app.schemas.intent import IntentCreateRequest, IntentCreateResponse, IntentResponseData

router = APIRouter()


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_intent(
    service: SymbolServiceDep,
    request: IntentCreateRequest,
) -> IntentCreateResponse:
    # 僅用於驗證 symbol validation 整合
    # 呼叫 get_tradable_symbol 進行驗證
    service.get_tradable_symbol(request.symbol)

    return IntentCreateResponse(
        data=IntentResponseData(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
        )
    )
