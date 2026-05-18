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
    return app


app = create_app()
