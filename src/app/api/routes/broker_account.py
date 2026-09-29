"""The user's own read-only view of their broker binding. Bind / unbind are admin
operations and live under /admin/users/{id}/broker-account."""

from fastapi import APIRouter, status

from app.api.deps import ActiveUserDep, BrokerAccountRepoDep
from app.api.errors import ApiError, ErrorCode, ErrorResponse
from app.api.schemas.broker_account import BrokerAccountResponse
from app.domain.broker_account import BrokerAccountData

router = APIRouter()


def to_response(account: BrokerAccountData) -> BrokerAccountResponse:
    number = account.broker_account_no
    masked = "*" * max(len(number) - 4, 0) + number[-4:]
    return BrokerAccountResponse(
        broker=account.broker,
        broker_account_no=masked,
        status=account.status,
        cert_expires_at=account.cert_expires_at,
        last_login_at=account.last_login_at,
        last_error=account.last_error,
        updated_at=account.updated_at,
    )


@router.get(
    "/broker-account",
    response_model=BrokerAccountResponse,
    summary="查看自己的券商帳號綁定狀態（唯讀，不含任何機密）",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "尚未綁定券商帳號（NOT_FOUND）"},
    },
)
def get_my_broker_account(user: ActiveUserDep, accounts: BrokerAccountRepoDep) -> BrokerAccountResponse:
    account = accounts.get_by_user_id(user.user_id)
    if account is None:
        raise ApiError(code=ErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    return to_response(account)
