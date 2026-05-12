import logging
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"


DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INTERNAL_ERROR: "發生未預期錯誤",
    ErrorCode.VALIDATION_ERROR: "請求資料不合法",
    ErrorCode.DATABASE_UNAVAILABLE: "資料庫暫時無法使用",
}


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        status_code: int,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message or DEFAULT_MESSAGES[code]
        self.details = details or {}


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def build_error_response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    header_name = getattr(request.app.state, "request_id_header", "X-Request-Id")
    payload = {
        "error": {
            "code": code.value,
            "message": message or DEFAULT_MESSAGES[code],
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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return build_error_response(
            request=request,
            status_code=422,
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
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            details={},
        )
