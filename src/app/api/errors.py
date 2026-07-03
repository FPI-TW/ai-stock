import logging
from enum import StrEnum
from math import ceil
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.auth import (
    AccountError,
    AccountNotDisabledError,
    AuthError,
    CsrfFailedError,
    EmailAlreadyExistsError,
    InvitationConsumedError,
    InvitationExpiredError,
    InvitationInvalidError,
    LoginFailedError,
    LoginLockedError,
    MfaAlreadyEnabledError,
    MfaInvalidCodeError,
    MfaNotSetupError,
    MfaRequiredError,
    PasswordResetInvalidError,
    RateLimitedError,
    RefreshInvalidError,
    RefreshReuseDetectedError,
    TermsNotAcceptedError,
    UnauthenticatedError,
    UserNotFoundError,
    WeakPasswordError,
)
from app.domain.notification import NotificationNotFoundError
from app.domain.price import InvalidAmountError, InvalidPriceError, InvalidTickSizeError, InvalidTypeError
from app.domain.symbol_errors import SymbolError, SymbolNotTradableError, UnknownSymbolError
from app.domain.trade_intent import (
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentLimitExceededError,
    IntentNotFoundError,
    InvalidCursorError,
    SymbolIntentLimitExceededError,
)
from app.domain.twap import TwapDuplicateActivePlanError, TwapError
from app.services.idempotency import IdempotencyConflictError
from app.services.quote.base import QuoteProviderError

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    SYMBOL_NOT_TRADABLE = "SYMBOL_NOT_TRADABLE"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    INVALID_TYPE = "INVALID_TYPE"
    DUPLICATE_INTENT = "DUPLICATE_INTENT"
    USER_INTENT_LIMIT_EXCEEDED = "USER_INTENT_LIMIT_EXCEEDED"
    SYMBOL_INTENT_LIMIT_EXCEEDED = "SYMBOL_INTENT_LIMIT_EXCEEDED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    CANCEL_NOT_ALLOWED = "CANCEL_NOT_ALLOWED"
    INVALID_CURSOR = "INVALID_CURSOR"
    QUOTE_PROVIDER_UNAVAILABLE = "QUOTE_PROVIDER_UNAVAILABLE"
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    CURRENT_PRICE_SYMBOL_NOT_ALLOWED = "CURRENT_PRICE_SYMBOL_NOT_ALLOWED"
    TWAP_END_TIME_OUTSIDE_SESSION = "TWAP_END_TIME_OUTSIDE_SESSION"
    TWAP_END_TIME_ALREADY_PASSED = "TWAP_END_TIME_ALREADY_PASSED"
    TWAP_INSUFFICIENT_SLICES = "TWAP_INSUFFICIENT_SLICES"
    TWAP_TOO_MANY_SLICES = "TWAP_TOO_MANY_SLICES"
    TWAP_INVALID_INTERVAL = "TWAP_INVALID_INTERVAL"
    TWAP_INVALID_QUANTITY = "TWAP_INVALID_QUANTITY"
    TWAP_DUPLICATE_ACTIVE_PLAN = "TWAP_DUPLICATE_ACTIVE_PLAN"
    # L1 auth
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    SESSION_REVOKED = "SESSION_REVOKED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    REFRESH_INVALID = "REFRESH_INVALID"
    REFRESH_REUSE_DETECTED = "REFRESH_REUSE_DETECTED"
    CSRF_FAILED = "CSRF_FAILED"
    MFA_REQUIRED = "MFA_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    # L1 account lifecycle
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    INVITATION_INVALID = "INVITATION_INVALID"
    INVITATION_EXPIRED = "INVITATION_EXPIRED"
    INVITATION_CONSUMED = "INVITATION_CONSUMED"
    WEAK_PASSWORD = "WEAK_PASSWORD"
    TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
    PASSWORD_RESET_INVALID = "PASSWORD_RESET_INVALID"
    MFA_INVALID_CODE = "MFA_INVALID_CODE"
    MFA_ALREADY_ENABLED = "MFA_ALREADY_ENABLED"
    MFA_NOT_SETUP = "MFA_NOT_SETUP"
    ACCOUNT_NOT_DISABLED = "ACCOUNT_NOT_DISABLED"


DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INTERNAL_ERROR: "發生未預期錯誤",
    ErrorCode.VALIDATION_ERROR: "請求資料不合法",
    ErrorCode.DATABASE_UNAVAILABLE: "資料庫暫時無法使用",
    ErrorCode.UNKNOWN_SYMBOL: "找不到標的代號",
    ErrorCode.SYMBOL_NOT_TRADABLE: "標的目前不可交易",
    ErrorCode.INVALID_PRICE: "價格格式不合法",
    ErrorCode.INVALID_TICK_SIZE: "價格不符合升降單位規定",
    ErrorCode.INVALID_AMOUNT: "數量不合法",
    ErrorCode.INVALID_TYPE: "證券類型不合法",
    ErrorCode.DUPLICATE_INTENT: "已存在相同的委託",
    ErrorCode.USER_INTENT_LIMIT_EXCEEDED: "您的有效委託數已達上限",
    ErrorCode.SYMBOL_INTENT_LIMIT_EXCEEDED: "此標的的有效委託數已達上限",
    ErrorCode.IDEMPOTENCY_KEY_REQUIRED: "此操作需要提供 Idempotency-Key",
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: "Idempotency-Key 已用於不同的請求",
    ErrorCode.NOT_FOUND: "找不到此資源",
    ErrorCode.CANCEL_NOT_ALLOWED: "此委託狀態不允許取消",
    ErrorCode.INVALID_CURSOR: "Cursor 已失效或不存在",
    ErrorCode.QUOTE_PROVIDER_UNAVAILABLE: "行情服務暫時無法使用",
    ErrorCode.QUOTE_UNAVAILABLE: "尚未收到該標的的行情報價",
    ErrorCode.CURRENT_PRICE_SYMBOL_NOT_ALLOWED: "此測試查價 API 僅允許指定標的",
    ErrorCode.TWAP_END_TIME_OUTSIDE_SESSION: "TWAP 結束時間需在台股交易時段內",
    ErrorCode.TWAP_END_TIME_ALREADY_PASSED: "TWAP 結束時間已經超過",
    ErrorCode.TWAP_INSUFFICIENT_SLICES: "TWAP 至少需要兩筆分批交易",
    ErrorCode.TWAP_TOO_MANY_SLICES: "TWAP 分批筆數超過上限",
    ErrorCode.TWAP_INVALID_INTERVAL: "TWAP 間隔秒數不合法",
    ErrorCode.TWAP_INVALID_QUANTITY: "TWAP 目標量不合法",
    ErrorCode.TWAP_DUPLICATE_ACTIVE_PLAN: "已存在相同的 TWAP 計畫",
    ErrorCode.UNAUTHENTICATED: "請先登入",
    ErrorCode.FORBIDDEN: "沒有權限執行此操作",
    ErrorCode.ACCOUNT_DISABLED: "帳號已停用，請重新登入或聯絡管理員",
    ErrorCode.SESSION_REVOKED: "工作階段已失效，請重新登入",
    ErrorCode.LOGIN_FAILED: "帳號或密碼錯誤",
    ErrorCode.LOGIN_LOCKED: "登入失敗次數過多，請稍後再試",
    ErrorCode.REFRESH_INVALID: "登入憑證已失效，請重新登入",
    ErrorCode.REFRESH_REUSE_DETECTED: "偵測到憑證異常使用，已登出所有工作階段",
    ErrorCode.CSRF_FAILED: "CSRF 驗證失敗",
    ErrorCode.MFA_REQUIRED: "需要完成兩階段驗證",
    ErrorCode.RATE_LIMITED: "請求過於頻繁，請稍後再試",
    ErrorCode.EMAIL_ALREADY_EXISTS: "此 email 已存在",
    ErrorCode.INVITATION_INVALID: "邀請連結無效",
    ErrorCode.INVITATION_EXPIRED: "邀請連結已過期",
    ErrorCode.INVITATION_CONSUMED: "邀請連結已被使用",
    ErrorCode.WEAK_PASSWORD: "密碼長度至少需 8 個字元",
    ErrorCode.TERMS_NOT_ACCEPTED: "必須接受服務條款",
    ErrorCode.PASSWORD_RESET_INVALID: "重設連結無效或已過期",
    ErrorCode.MFA_INVALID_CODE: "驗證碼錯誤",
    ErrorCode.MFA_ALREADY_ENABLED: "已啟用兩階段驗證",
    ErrorCode.MFA_NOT_SETUP: "尚未設定兩階段驗證",
    ErrorCode.ACCOUNT_NOT_DISABLED: "帳號未處於停用狀態，無法復權",
}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode | str,
        status_code: int,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message or _default_message(code)
        self.details = details or {}


def _code_value(code: ErrorCode | str) -> str:
    return code.value if isinstance(code, ErrorCode) else code


def _default_message(code: ErrorCode | str) -> str:
    if isinstance(code, ErrorCode):
        return DEFAULT_MESSAGES[code]
    return "行情服務發生錯誤"


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}
    request_id: str | None = Field(default=None, serialization_alias="requestId")


class ErrorResponse(BaseModel):
    """OpenAPI schema for the standard error envelope emitted by build_error_response.
    Reference it from a route's `responses=` so non-2xx bodies are documented."""

    error: ErrorBody


def build_error_response(
    request: Request,
    status_code: int,
    code: ErrorCode | str,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    header_name = getattr(request.app.state, "request_id_header", "X-Request-Id")
    payload = {
        "error": {
            "code": _code_value(code),
            "message": message or _default_message(code),
            "details": details or {},
            "requestId": request_id,
        }
    }
    headers = {header_name: request_id} if request_id else None
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    settings = app.extra.get("settings")
    if settings is not None:
        app.state.request_id_header = settings.request_id_header

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        # MfaRequiredError must be checked before its ForbiddenError base.
        if isinstance(exc, UnauthenticatedError):
            return build_error_response(request, status.HTTP_401_UNAUTHORIZED, ErrorCode.UNAUTHENTICATED)
        if isinstance(exc, LoginFailedError):
            return build_error_response(request, status.HTTP_401_UNAUTHORIZED, ErrorCode.LOGIN_FAILED)
        if isinstance(exc, LoginLockedError):
            retry_after = max(1, ceil(exc.retry_after_seconds))
            response = build_error_response(
                request,
                status.HTTP_429_TOO_MANY_REQUESTS,
                ErrorCode.LOGIN_LOCKED,
                details={"retryAfterSeconds": retry_after},
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
        if isinstance(exc, RefreshReuseDetectedError):
            return build_error_response(request, status.HTTP_401_UNAUTHORIZED, ErrorCode.REFRESH_REUSE_DETECTED)
        if isinstance(exc, RefreshInvalidError):
            return build_error_response(request, status.HTTP_401_UNAUTHORIZED, ErrorCode.REFRESH_INVALID)
        if isinstance(exc, CsrfFailedError):
            return build_error_response(request, status.HTTP_403_FORBIDDEN, ErrorCode.CSRF_FAILED)
        if isinstance(exc, MfaInvalidCodeError):
            return build_error_response(request, status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.MFA_INVALID_CODE)
        if isinstance(exc, RateLimitedError):
            retry_after = max(1, ceil(exc.retry_after_seconds))
            response = build_error_response(
                request,
                status.HTTP_429_TOO_MANY_REQUESTS,
                ErrorCode.RATE_LIMITED,
                details={"retryAfterSeconds": retry_after},
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
        if isinstance(exc, MfaRequiredError):
            return build_error_response(request, status.HTTP_403_FORBIDDEN, ErrorCode.MFA_REQUIRED)
        # Remaining ForbiddenError (and any unmapped AuthError) -> 403 FORBIDDEN.
        return build_error_response(request, status.HTTP_403_FORBIDDEN, ErrorCode.FORBIDDEN)

    @app.exception_handler(AccountError)
    async def account_error_handler(request: Request, exc: AccountError) -> JSONResponse:
        if isinstance(exc, UserNotFoundError):
            return build_error_response(request, status.HTTP_404_NOT_FOUND, ErrorCode.NOT_FOUND)
        if isinstance(exc, EmailAlreadyExistsError):
            return build_error_response(request, status.HTTP_409_CONFLICT, ErrorCode.EMAIL_ALREADY_EXISTS)
        if isinstance(exc, InvitationExpiredError):
            return build_error_response(request, status.HTTP_410_GONE, ErrorCode.INVITATION_EXPIRED)
        if isinstance(exc, InvitationConsumedError):
            return build_error_response(request, status.HTTP_409_CONFLICT, ErrorCode.INVITATION_CONSUMED)
        if isinstance(exc, InvitationInvalidError):
            return build_error_response(request, status.HTTP_400_BAD_REQUEST, ErrorCode.INVITATION_INVALID)
        if isinstance(exc, WeakPasswordError):
            return build_error_response(request, status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.WEAK_PASSWORD)
        if isinstance(exc, TermsNotAcceptedError):
            return build_error_response(request, status.HTTP_422_UNPROCESSABLE_CONTENT, ErrorCode.TERMS_NOT_ACCEPTED)
        if isinstance(exc, PasswordResetInvalidError):
            return build_error_response(request, status.HTTP_400_BAD_REQUEST, ErrorCode.PASSWORD_RESET_INVALID)
        if isinstance(exc, MfaAlreadyEnabledError):
            return build_error_response(request, status.HTTP_409_CONFLICT, ErrorCode.MFA_ALREADY_ENABLED)
        if isinstance(exc, MfaNotSetupError):
            return build_error_response(request, status.HTTP_409_CONFLICT, ErrorCode.MFA_NOT_SETUP)
        if isinstance(exc, AccountNotDisabledError):
            return build_error_response(request, status.HTTP_409_CONFLICT, ErrorCode.ACCOUNT_NOT_DISABLED)
        return build_error_response(request, status.HTTP_400_BAD_REQUEST, ErrorCode.VALIDATION_ERROR)

    @app.exception_handler(SymbolError)
    async def symbol_error_handler(request: Request, exc: SymbolError) -> JSONResponse:
        if isinstance(exc, UnknownSymbolError):
            return build_error_response(
                request=request,
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.UNKNOWN_SYMBOL,
                details={"symbol": exc.symbol},
            )
        if isinstance(exc, SymbolNotTradableError):
            return build_error_response(
                request=request,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code=ErrorCode.SYMBOL_NOT_TRADABLE,
                details={"symbol": exc.symbol, "tradable_status": exc.tradable_status},
            )
        return build_error_response(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=ErrorCode.INTERNAL_ERROR,
        )

    # InvalidTickSizeError 必須在 InvalidPriceError 之前註冊（子類別優先）
    @app.exception_handler(InvalidTickSizeError)
    async def invalid_tick_size_handler(request: Request, exc: InvalidTickSizeError) -> JSONResponse:
        details: dict[str, Any] = {
            "value": str(exc.value),
            "nearest_lower": str(exc.nearest_lower),
            "nearest_upper": str(exc.nearest_upper),
        }
        if exc.field is not None:
            details["field"] = exc.field
        return build_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_TICK_SIZE,
            details=details,
        )

    @app.exception_handler(InvalidPriceError)
    async def invalid_price_handler(request: Request, exc: InvalidPriceError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_PRICE,
            details={"value": str(exc.value)},
        )

    @app.exception_handler(InvalidAmountError)
    async def invalid_amount_handler(request: Request, exc: InvalidAmountError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_AMOUNT,
            details={"value": str(exc.value)},
        )

    @app.exception_handler(InvalidTypeError)
    async def invalid_type_handler(request: Request, exc: InvalidTypeError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.INVALID_TYPE,
            details={"value": str(exc.value)},
        )

    @app.exception_handler(DuplicateIntentError)
    async def duplicate_intent_handler(request: Request, exc: DuplicateIntentError) -> JSONResponse:
        logger.warning(
            "Duplicate intent rejected",
            extra={"request_id": get_request_id(request), "symbol": exc.symbol, "strategy": exc.strategy},
        )
        return build_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.DUPLICATE_INTENT,
            details={"symbol": exc.symbol, "strategy": exc.strategy},
        )

    @app.exception_handler(IntentLimitExceededError)
    async def intent_limit_exceeded_handler(request: Request, exc: IntentLimitExceededError) -> JSONResponse:
        if isinstance(exc, SymbolIntentLimitExceededError):
            return build_error_response(
                request=request,
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.SYMBOL_INTENT_LIMIT_EXCEEDED,
                details={"symbol": exc.symbol, "limit": exc.limit, "current": exc.current},
            )
        return build_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.USER_INTENT_LIMIT_EXCEEDED,
            details={"limit": exc.limit, "current": exc.current},
        )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(request: Request, exc: IdempotencyConflictError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
        )

    @app.exception_handler(IntentNotFoundError)
    async def intent_not_found_handler(request: Request, exc: IntentNotFoundError) -> JSONResponse:
        logger.warning(
            "Intent not found",
            extra={"request_id": get_request_id(request), "intent_id": str(exc.intent_id)},
        )
        return build_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
        )

    @app.exception_handler(NotificationNotFoundError)
    async def notification_not_found_handler(request: Request, exc: NotificationNotFoundError) -> JSONResponse:
        logger.warning(
            "Notification not found",
            extra={
                "request_id": get_request_id(request),
                "notification_id": str(exc.notification_id),
            },
        )
        return build_error_response(
            request=request,
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
        )

    @app.exception_handler(InvalidCursorError)
    async def invalid_cursor_handler(request: Request, exc: InvalidCursorError) -> JSONResponse:
        logger.warning(
            "Invalid or expired cursor",
            extra={"request_id": get_request_id(request), "cursor_id": str(exc.cursor_id)},
        )
        return build_error_response(
            request=request,
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.INVALID_CURSOR,
        )

    @app.exception_handler(TwapError)
    async def twap_error_handler(request: Request, exc: TwapError) -> JSONResponse:
        status_code = (
            status.HTTP_409_CONFLICT
            if isinstance(exc, TwapDuplicateActivePlanError)
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        try:
            code: ErrorCode | str = ErrorCode(exc.error_code)
        except ValueError:
            code = exc.error_code
        return build_error_response(
            request=request,
            status_code=status_code,
            code=code,
            details=exc.details(),
        )

    @app.exception_handler(CancelNotAllowedError)
    async def cancel_not_allowed_handler(request: Request, exc: CancelNotAllowedError) -> JSONResponse:
        logger.warning(
            "Cancel not allowed",
            extra={
                "request_id": get_request_id(request),
                "intent_id": str(exc.intent_id),
                "current_status": exc.current_status,
            },
        )
        return build_error_response(
            request=request,
            status_code=status.HTTP_409_CONFLICT,
            code=ErrorCode.CANCEL_NOT_ALLOWED,
            details={"currentStatus": exc.current_status},
        )

    @app.exception_handler(QuoteProviderError)
    async def quote_provider_error_handler(request: Request, exc: QuoteProviderError) -> JSONResponse:
        # Provider-specific subclasses provide stable envelope fields through
        # class attributes, without this API layer importing concrete providers.
        code: ErrorCode | str
        try:
            code = ErrorCode(exc.error_code)
        except ValueError:
            code = exc.error_code
        logger.warning(
            "Quote provider error",
            extra={
                "request_id": get_request_id(request),
                "error_code": exc.error_code,
                "exception_class": type(exc).__name__,
            },
        )
        return build_error_response(
            request=request,
            status_code=exc.http_status,
            code=code,
            message=exc.default_message,
            details=exc.details(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code=ErrorCode.VALIDATION_ERROR,
            details={"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=exc.status_code,
            code=ErrorCode.VALIDATION_ERROR,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception",
            extra={
                "request_id": get_request_id(request),
                "path": request.url.path,
                "method": request.method,
            },
        )
        return build_error_response(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=ErrorCode.INTERNAL_ERROR,
            details={},
        )
