from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import (
    ActiveUserDep,
    CancelTradeIntentCommandDep,
    CreateTradeIntentCommandDep,
    IdempotencyKeyDep,
    IdempotencyManagerDep,
    IntentLifecycleCommandDep,
    IntentRepoDep,
    TwapConfirmCommandDep,
    TwapPlanCommandDep,
    enforce_mutation_rate_limit,
)
from app.api.errors import ApiError, ErrorCode, get_request_id
from app.api.routes._pagination import validate_cursor
from app.commands.trade_intent import CancelTradeIntentInput, CreateTradeIntentInput
from app.commands.twap import TwapPlanInput
from app.domain.trade_intent import VALID_STATUSES
from app.schemas.intent import (
    IntentCreateRequest,
    IntentCreateResponse,
    IntentDetailResponse,
    IntentListResponse,
    TwapConfirmResponse,
    TwapPlanRequest,
    TwapPreviewResponse,
    map_to_detail_response_data,
    map_to_response_data,
    map_twap_plan,
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


@router.post(
    "",
    response_model=IntentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_mutation_rate_limit)],
)
def create_intent(
    http_request: Request,
    request: IntentCreateRequest,
    user: ActiveUserDep,
    command: CreateTradeIntentCommandDep,
    idempotency_key: IdempotencyKeyDep,
    idempotency: IdempotencyManagerDep,
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        intent = command.execute(
            CreateTradeIntentInput(
                symbol=request.symbol,
                strategy=request.strategy,
                quantity_lots=request.quantity_lots,
                target_price=getattr(request, "target_price", None),
                transaction_mode=getattr(request, "transaction_mode", "single_notification"),
                notification_mode=getattr(request, "notification_mode", "single"),
                trail_mode=getattr(request, "trail_mode", None),
                trail_value=getattr(request, "trail_value", None),
                owner_user_id=user.user_id,
                request_id=get_request_id(http_request),
            )
        )
        return IntentCreateResponse(data=map_to_response_data(intent)).model_dump(mode="json")

    return idempotency.run(
        user_id=user.user_id,
        key=idempotency_key,
        endpoint="create_intent",
        payload=request.model_dump(mode="json"),
        now=datetime.now(UTC),
        execute=execute,
    )


@router.get("", response_model=IntentListResponse)
def list_intents(
    user: ActiveUserDep,
    intent_repo: IntentRepoDep,
    lifecycle: IntentLifecycleCommandDep,
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    trading_date: Annotated[date | None, Query(alias="tradingDate")] = None,
    cursor: str | None = None,
    page_size: int = Query(default=50, ge=1, le=100, alias="pageSize"),
) -> IntentListResponse:
    statuses = _parse_status_list(status_filter)
    _validate_statuses(statuses)
    validate_cursor(cursor)
    lifecycle.run()

    items, next_cursor = intent_repo.list_by_owner(
        owner_user_id=user.user_id,
        statuses=statuses,
        trading_date=trading_date,
        cursor=cursor,
        page_size=page_size,
    )
    return IntentListResponse(
        data=[map_to_response_data(i) for i in items],
        next_cursor=next_cursor,
        page_size=page_size,
    )


@router.post("/twap/preview", response_model=TwapPreviewResponse)
def preview_twap(
    request: TwapPlanRequest,
    user: ActiveUserDep,
    command: TwapPlanCommandDep,
) -> TwapPreviewResponse:
    plan = command.preview(
        TwapPlanInput(
            symbol=request.symbol,
            position_side=request.position_side,
            quantity_lots=request.quantity_lots,
            interval_seconds=request.interval_seconds,
            start_time=request.start_time,
            end_time=request.end_time,
            owner_user_id=user.user_id,
        )
    )
    return TwapPreviewResponse(data=map_twap_plan(request.symbol, plan))


@router.post(
    "/twap/confirm",
    response_model=TwapConfirmResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_mutation_rate_limit)],
)
def confirm_twap(
    http_request: Request,
    request: TwapPlanRequest,
    user: ActiveUserDep,
    command: TwapConfirmCommandDep,
    intent_repo: IntentRepoDep,
    idempotency_key: IdempotencyKeyDep,
    idempotency: IdempotencyManagerDep,
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        output = command.execute(
            TwapPlanInput(
                symbol=request.symbol,
                position_side=request.position_side,
                quantity_lots=request.quantity_lots,
                interval_seconds=request.interval_seconds,
                start_time=request.start_time,
                end_time=request.end_time,
                owner_user_id=user.user_id,
                request_id=get_request_id(http_request),
            )
        )
        slices = intent_repo.list_twap_slices(output.intent.id, user.user_id)
        return TwapConfirmResponse(data=map_to_detail_response_data(output.intent, slices)).model_dump(mode="json")

    return idempotency.run(
        user_id=user.user_id,
        key=idempotency_key,
        endpoint="confirm_twap",
        payload=request.model_dump(mode="json"),
        now=datetime.now(UTC),
        execute=execute,
    )


@router.get("/{intent_id}", response_model=IntentDetailResponse)
def get_intent(
    intent_id: UUID,
    user: ActiveUserDep,
    intent_repo: IntentRepoDep,
    lifecycle: IntentLifecycleCommandDep,
) -> IntentDetailResponse:
    lifecycle.run()
    intent = intent_repo.find_by_id(intent_id, user.user_id)
    slices = intent_repo.list_twap_slices(intent_id, user.user_id) if intent.strategy == "twap_order" else None
    return IntentDetailResponse(data=map_to_detail_response_data(intent, slices))


@router.post(
    "/{intent_id}/cancel",
    response_model=IntentDetailResponse,
    dependencies=[Depends(enforce_mutation_rate_limit)],
)
def cancel_intent(
    http_request: Request,
    intent_id: UUID,
    user: ActiveUserDep,
    command: CancelTradeIntentCommandDep,
    intent_repo: IntentRepoDep,
    lifecycle: IntentLifecycleCommandDep,
    idempotency_key: IdempotencyKeyDep,
    idempotency: IdempotencyManagerDep,
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        lifecycle.run()
        intent = command.execute(
            CancelTradeIntentInput(
                intent_id=intent_id,
                owner_user_id=user.user_id,
                request_id=get_request_id(http_request),
            )
        )
        slices = intent_repo.list_twap_slices(intent_id, user.user_id) if intent.strategy == "twap_order" else None
        return IntentDetailResponse(data=map_to_detail_response_data(intent, slices)).model_dump(mode="json")

    return idempotency.run(
        user_id=user.user_id,
        key=idempotency_key,
        endpoint="cancel_intent",
        payload={"intent_id": str(intent_id)},
        now=datetime.now(UTC),
        execute=execute,
    )
