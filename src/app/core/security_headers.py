"""Static security response headers (L3 §13 安全表面).

A single middleware that stamps the same hardening headers on every response.
HSTS is opt-in (`hsts_enabled`) because Strict-Transport-Security must only be
sent over HTTPS — in LOCAL_MODE the API is served over plain HTTP and an HSTS
header there would wrongly pin localhost to https.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# 2 years, per common HSTS preload guidance.
_HSTS_VALUE = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, hsts_enabled: bool = False) -> None:
        super().__init__(app)
        self.hsts_enabled = hsts_enabled

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.hsts_enabled:
            response.headers["Strict-Transport-Security"] = _HSTS_VALUE
        return response
