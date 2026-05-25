from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    CancelTradeIntentCommandDep,
    CreateTradeIntentCommandDep,
    CurrentUserDep,
    IntentRepoDep,
)
from app.api.errors import ApiError, ErrorCode
from app.api.routes._pagination import validate_cursor
from app.commands.trade_intent import CancelTradeIntentInput, CreateTradeIntentInput
from app.domain.trade_intent import VALID_STATUSES
from app.schemas.intent import (
    IntentCreateRequest,
    IntentCreateResponse,
    IntentDetailResponse,
    IntentListResponse,
    map_to_response_data,
)

router = APIRouter()


def _parse_status_list(raw: list[str] | None) -> list[str] | None:
    """Accept both repeated params (?status=a&status=b) and comma-separated (?status=a,b)."""
    if not raw:
        return None
    result: list[str] = []
    for item in raw:
        result.extend(s.strip() for s in item.split(",") if s.strip())
    return result or None


def _validate_statuses(statuses: list[str] | None) -> None:
    if statuses is None:
        return
    invalid = [s for s in statuses if s not in VALID_STATUSES]
    if invalid:
        raise ApiError(
            code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details={"invalid_statuses": invalid, "valid_statuses": sorted(VALID_STATUSES)},
        )


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_intent(
    request: IntentCreateRequest,
    user: CurrentUserDep,
    command: CreateTradeIntentCommandDep,
) -> IntentCreateResponse:
    # Trailing's command / evaluator path lands in a later BE-V0.5-16 PR.
    # Pydantic has already validated the request shape; we just refuse at the
    # boundary with 501 so callers see a stable envelope instead of a 500.
    if request.strategy == "trailing_stop_alert":
        raise ApiError(
            code=ErrorCode.STRATEGY_NOT_IMPLEMENTED,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            details={"strategy": request.strategy},
        )
    intent = command.execute(
        CreateTradeIntentInput(
            symbol=request.symbol,
            strategy=request.strategy,
            quantity_lots=request.quantity_lots,
            target_price=request.target_price,
            owner_user_id=user.user_id,
        )
    )
    return IntentCreateResponse(data=map_to_response_data(intent))


@router.get("", response_model=IntentListResponse)
def list_intents(
    user: CurrentUserDep,
    intent_repo: IntentRepoDep,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    cursor: str | None = None,
    page_size: int = Query(default=50, ge=1, le=100, alias="pageSize"),
) -> IntentListResponse:
    statuses = _parse_status_list(status_filter)
    _validate_statuses(statuses)
    validate_cursor(cursor)

    items, next_cursor = intent_repo.list_by_owner(
        owner_user_id=user.user_id,
        statuses=statuses,
        cursor=cursor,
        page_size=page_size,
    )
    return IntentListResponse(
        data=[map_to_response_data(i) for i in items],
        next_cursor=next_cursor,
        page_size=page_size,
    )


@router.get("/{intent_id}", response_model=IntentDetailResponse)
def get_intent(
    intent_id: UUID,
    user: CurrentUserDep,
    intent_repo: IntentRepoDep,
) -> IntentDetailResponse:
    intent = intent_repo.find_by_id(intent_id, user.user_id)
    return IntentDetailResponse(data=map_to_response_data(intent))


@router.post("/{intent_id}/cancel", response_model=IntentDetailResponse)
def cancel_intent(
    intent_id: UUID,
    user: CurrentUserDep,
    command: CancelTradeIntentCommandDep,
) -> IntentDetailResponse:
    intent = command.execute(CancelTradeIntentInput(intent_id=intent_id, owner_user_id=user.user_id))
    return IntentDetailResponse(data=map_to_response_data(intent))
