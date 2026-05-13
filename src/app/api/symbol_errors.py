"""Symbol feature 專用的 error envelope。

main 的 app.api.errors.ErrorCode 沒有 symbol 相關 code，這裡用獨立的
SymbolApiError 階層 + 自己的 exception handler 處理 envelope，不動到
共用的 ErrorCode StrEnum。
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class SymbolApiError(Exception):
    def __init__(
        self,
        code: str,
        status_code: int,
        message: str,
        details: dict[str, Any],
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message = message
        self.details = details


class UnknownSymbolError(SymbolApiError):
    def __init__(self, symbol: str) -> None:
        super().__init__(
            code="UNKNOWN_SYMBOL",
            status_code=status.HTTP_404_NOT_FOUND,
            message="找不到標的代號",
            details={"symbol": symbol},
        )


class UnsupportedInstrumentError(SymbolApiError):
    def __init__(self, instrument_type: str) -> None:
        super().__init__(
            code="UNSUPPORTED_INSTRUMENT",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="不支援的商品類型",
            details={"instrument_type": instrument_type},
        )


class SymbolNotTradableError(SymbolApiError):
    def __init__(self, symbol: str, tradable_status: str) -> None:
        super().__init__(
            code="SYMBOL_NOT_TRADABLE",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="標的目前不可交易",
            details={"symbol": symbol, "tradable_status": tradable_status},
        )


def register_symbol_error_handler(app: FastAPI) -> None:
    @app.exception_handler(SymbolApiError)
    async def handle(request: Request, exc: SymbolApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "")
        header_name = getattr(request.app.state, "request_id_header", "X-Request-Id")
        payload = {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "requestId": request_id,
            }
        }
        headers = {header_name: request_id} if request_id else None
        return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)
