"""Symbol feature 的 entrypoint。

包住 app.main.create_app() 後額外掛上 symbol/intents router 與 symbol
error handler。生產執行：

    uvicorn app.symbol_main:app --app-dir src --reload
"""

from fastapi import FastAPI

from app.api.symbol_errors import register_symbol_error_handler
from app.main import create_app
from app.routers.intents import router as intents_router
from app.routers.symbols import router as symbols_router


def create_symbol_app() -> FastAPI:
    app = create_app()
    register_symbol_error_handler(app)
    app.include_router(symbols_router, prefix="/symbols", tags=["symbols"])
    app.include_router(intents_router, prefix="/intents", tags=["intents"])
    return app


app = create_symbol_app()
