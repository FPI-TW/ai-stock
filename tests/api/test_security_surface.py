from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.api.deps import get_refresh_command
from app.main import create_app


def test_security_headers_on_health() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    # Tests run in LOCAL_MODE (plain HTTP) → no HSTS.
    assert "Strict-Transport-Security" not in response.headers


def test_cors_allows_listed_origin_with_credentials() -> None:
    origin = "http://localhost:3000"  # in the default CORS allow-list
    response = TestClient(create_app()).get("/health", headers={"Origin": origin})

    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_blocks_unlisted_origin() -> None:
    response = TestClient(create_app()).get("/health", headers={"Origin": "https://evil.example.com"})

    # CORSMiddleware simply omits the allow-origin header for a non-listed origin,
    # which is what makes the browser block the response.
    assert "access-control-allow-origin" not in response.headers


def test_refresh_rejects_disallowed_origin() -> None:
    # The Origin gate runs in the route before the command, so a bad cross-site
    # Origin is rejected with 403 CSRF_FAILED without touching the DB.
    app = create_app()
    app.dependency_overrides[get_refresh_command] = lambda: MagicMock()
    try:
        response = TestClient(app).post("/auth/refresh", headers={"Origin": "https://evil.example.com"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
