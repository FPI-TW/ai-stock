from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security_headers import SecurityHeadersMiddleware


def _app(hsts_enabled: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=hsts_enabled)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_static_security_headers_present() -> None:
    response = TestClient(_app(hsts_enabled=False)).get("/ping")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_absent_when_disabled() -> None:
    # LOCAL_MODE serves plain HTTP — sending HSTS there would wrongly pin localhost.
    response = TestClient(_app(hsts_enabled=False)).get("/ping")

    assert "Strict-Transport-Security" not in response.headers


def test_hsts_present_when_enabled() -> None:
    response = TestClient(_app(hsts_enabled=True)).get("/ping")

    assert response.headers["Strict-Transport-Security"].startswith("max-age=")
