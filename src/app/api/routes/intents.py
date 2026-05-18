from fastapi import APIRouter, status

from app.api.deps import SymbolServiceDep
from app.schemas.intent import IntentCreateRequest, IntentCreateResponse, IntentResponseData

router = APIRouter()


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_intent(
    service: SymbolServiceDep,
    request: IntentCreateRequest,
) -> IntentCreateResponse:
    # 僅用於驗證 symbol validation 整合
    # TODO: 寫入 trade_intents 表時需要 owner_user_id（auth context 上線後補上）
    service.get_tradable_symbol(request.symbol)

    return IntentCreateResponse(
        data=IntentResponseData(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
        )
    )
