"""Symbol feature 專用的 HTTP error envelope。

HTTP status code 與 error code 的對應在此集中管理，
domain exception 定義於 app.domain.symbol_errors，不依賴 FastAPI。
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.symbol_errors import SymbolError, SymbolNotTradableError, UnknownSymbolError

__all__ = ["SymbolError", "UnknownSymbolError", "SymbolNotTradableError", "register_symbol_error_handler"]


def register_symbol_error_handler(app: FastAPI) -> None:
    @app.exception_handler(SymbolError)
    async def handle(request: Request, exc: SymbolError) -> JSONResponse:
        if isinstance(exc, UnknownSymbolError):
            status_code = status.HTTP_404_NOT_FOUND
            code = "UNKNOWN_SYMBOL"
            message = "找不到標的代號"
            details = {"symbol": exc.symbol}
        elif isinstance(exc, SymbolNotTradableError):
            status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
            code = "SYMBOL_NOT_TRADABLE"
            message = "標的目前不可交易"
            details = {"symbol": exc.symbol, "tradable_status": exc.tradable_status}
        else:
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            code = "SYMBOL_ERROR"
            message = "未知的標的錯誤"
            details = {}

        request_id = getattr(request.state, "request_id", "")
        header_name = getattr(request.app.state, "request_id_header", "X-Request-Id")
        payload = {
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "requestId": request_id,
            }
        }
        headers = {header_name: request_id} if request_id else None
        return JSONResponse(status_code=status_code, content=payload, headers=headers)
