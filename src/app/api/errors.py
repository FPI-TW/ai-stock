import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.notification import NotificationNotFoundError
from app.domain.price import InvalidAmountError, InvalidPriceError, InvalidTickSizeError, InvalidTypeError
from app.domain.symbol_errors import SymbolError, SymbolNotTradableError, UnknownSymbolError
from app.domain.trade_intent import (
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
)
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
    NOT_FOUND = "NOT_FOUND"
    CANCEL_NOT_ALLOWED = "CANCEL_NOT_ALLOWED"
    INVALID_CURSOR = "INVALID_CURSOR"
    QUOTE_PROVIDER_UNAVAILABLE = "QUOTE_PROVIDER_UNAVAILABLE"
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    CURRENT_PRICE_SYMBOL_NOT_ALLOWED = "CURRENT_PRICE_SYMBOL_NOT_ALLOWED"


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
    ErrorCode.NOT_FOUND: "找不到此資源",
    ErrorCode.CANCEL_NOT_ALLOWED: "此委託狀態不允許取消",
    ErrorCode.INVALID_CURSOR: "Cursor 已失效或不存在",
    ErrorCode.QUOTE_PROVIDER_UNAVAILABLE: "行情服務暫時無法使用",
    ErrorCode.QUOTE_UNAVAILABLE: "尚未收到該標的的行情報價",
    ErrorCode.CURRENT_PRICE_SYMBOL_NOT_ALLOWED: "此測試查價 API 僅允許指定標的",
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
