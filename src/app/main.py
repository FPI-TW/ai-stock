from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.routes.health import router as health_router
from app.api.routes.intents import router as intents_router
from app.api.routes.symbols import router as symbols_router
from app.core.config import get_settings
from app.core.ids import RequestIdMiddleware


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.state.request_id_header = settings.request_id_header
    app.add_middleware(RequestIdMiddleware, header_name=settings.request_id_header)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(symbols_router, prefix="/symbols", tags=["symbols"])
    app.include_router(intents_router, prefix="/intents", tags=["intents"])
    if settings.local_mode:
        # Lazy import keeps dev router out of the dependency graph in non-local builds.
        from fastapi.middleware.cors import CORSMiddleware

        from app.api.routes.dev_quotes import router as dev_quotes_router

        app.include_router(dev_quotes_router, prefix="/dev", tags=["dev"])

        # CORS：dev console 從 standalone repo（任意 localhost port）跨 origin 呼叫。
        # 僅 LOCAL_MODE 開啟；正式環境不掛此 middleware。
        # expose_headers 確保 dev console 能讀 X-Request-Id 來對應後端 log。
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[settings.request_id_header],
        )
    return app


app = create_app()
