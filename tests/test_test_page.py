"""Smoke tests for the LOCAL_MODE-only /test page + static assets."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.conftest import ClientFactory


def test_test_page_served_in_local_mode(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/test")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # Sentinel strings — fail if the page was accidentally replaced.
    assert "ai-stock /test" in body
    assert "/test-assets/app.js" in body
    assert "/test-assets/styles.css" in body
    # User-view mode sentinels (added in /test 加 使用者視角 模式 PR)
    assert 'id="user-view"' in body
    assert 'id="toast-container"' in body
    assert 'id="sheet-overlay"' in body
    assert "使用者" in body  # mode toggle label


def test_test_assets_served_in_local_mode(client_factory: ClientFactory) -> None:
    client = client_factory()
    assert client.get("/test-assets/app.js").status_code == 200
    assert client.get("/test-assets/styles.css").status_code == 200
    assert client.get("/test-assets/endpoints.js").status_code == 200
    assert client.get("/test-assets/user-view.js").status_code == 200


def test_test_assets_reject_path_traversal(client_factory: ClientFactory) -> None:
    """StaticFiles must not let `..` escape the mounted directory."""
    client = client_factory()
    # FastAPI/Starlette returns 404 (not 400) for traversal attempts; either is
    # acceptable — what matters is the file is not served.
    response = client.get("/test-assets/../main.py")
    assert response.status_code in (400, 404)


def test_test_page_404_when_local_mode_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODE", "false")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    assert client.get("/test").status_code == 404
    assert client.get("/test-assets/app.js").status_code == 404
    # /dev/* must also stay 404 in production mode (regression guard).
    assert client.post("/dev/evaluate-quotes", json={}).status_code == 404
