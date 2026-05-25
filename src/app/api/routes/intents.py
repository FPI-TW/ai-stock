from typing import Annotated, Never
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    CancelTradeIntentCommandDep,
    CreateTradeIntentCommandDep,
    CurrentUserDep,
    IntentRepoDep,
)
from app.api.errors import ApiError, ErrorCode
from app.commands.trade_intent import CancelTradeIntentInput, CreateTradeIntentInput
from app.domain.trade_intent import VALID_STATUSES
from app.schemas.intent import (
    IntentBatchRequest,
    IntentCreateRequest,
    IntentCreateResponse,
    IntentDetailResponse,
    IntentListResponse,
    map_to_response_data,
)

router = APIRouter()

# Batch endpoint 上限。超出回 CSV_BATCH_LIMIT_EXCEEDED（工單 §90）。V1-10 拉到 100。
_BATCH_ROW_LIMIT = 20

# Stub envelope code for the batch handler.
# Replaced when the row-level transaction loop lands (post PR #15 merge).
# 工單 §60-99 / Implementation note 兩條（subscription reconcile cleanup、intra-batch dedup）。
_BATCH_STUB_CODE = "BATCH_NOT_IMPLEMENTED_STUB"


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


def _validate_cursor(cursor: str | None) -> None:
    if cursor is None:
        return
    try:
        UUID(cursor)
    except ValueError as exc:
        raise ApiError(
            code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"cursor": "must be a valid UUID"},
        ) from exc


@router.post("", response_model=IntentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_intent(
    request: IntentCreateRequest,
    user: CurrentUserDep,
    command: CreateTradeIntentCommandDep,
) -> IntentCreateResponse:
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


@router.post(
    "/batch",
    response_model=None,
    responses={
        422: {
            "description": (
                "CSV_BATCH_LIMIT_EXCEEDED — rows 超過 _BATCH_ROW_LIMIT。"
                "PR #15 後此 status 亦會包含 BATCH_ROW_REJECTED（row-level 驗證失敗時）。"
            ),
        },
        501: {
            "description": (
                "BATCH_NOT_IMPLEMENTED_STUB — PR #15 (`execute_within_tx`) merge 前的暫時行為。"
                "PR #15 後此 entry 應從 responses 移除。"
            ),
        },
    },
)
def create_intents_batch(
    request: IntentBatchRequest,
    user: CurrentUserDep,
) -> Never:
    """Batch create entrypoint — structural validation layer only.

    Stub state（現況）
        Row-level business validation (symbol / tick / session / duplicate /
        quota) 與 same-transaction loop 等 PR #15 (`execute_within_tx` 抽出)
        merge 後接入，外加工單兩條 Implementation note
        (`docs/orders/v0.5/BE-V0.5-14-csv-batch-frontend-parse.md` §229 起)。

    Future-state contract（給接前端的人 — 工單 §101 起的完整定義）
        - 全成功 → `200` + `{ data: { createdRows, rows: [{ rowNumber,
          tradeIntentId, status }] } }`（工單 §101-§121）。
        - 任一 row 失敗 → 整批 rollback，envelope code `BATCH_ROW_REJECTED`，
          HTTP status 取首失敗 row 的 single-create status（422 或 409）。
          Per-row error 格式 `{ rowNumber, field, code, message, details }`
          （工單 §128-§155）。
        - Rows 超上限 → `422 CSV_BATCH_LIMIT_EXCEEDED`（已在本 stub 生效）。

    完整 error code 矩陣與 envelope sample 見工單 §101-§180。
    當 stub 被替換時：把 return type 改回實際 response model、移除
    `response_model=None` 與 501 responses entry、把 raise 換成
    工單 §97 / §255 / §334 的 row loop。
    """

    if len(request.rows) > _BATCH_ROW_LIMIT:
        raise ApiError(
            code=ErrorCode.CSV_BATCH_LIMIT_EXCEEDED,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details={"actual": len(request.rows), "limit": _BATCH_ROW_LIMIT},
        )

    raise ApiError(
        code=_BATCH_STUB_CODE,
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        message=(
            "批次端點尚未完整實作（BE-V0.5-14）：結構驗證已通過，"
            "row-level validation 與 transaction loop 等 PR #15 merge 後接入。"
        ),
        details={"receivedRows": len(request.rows)},
    )


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
    _validate_cursor(cursor)

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
