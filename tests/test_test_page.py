"""Smoke tests for the LOCAL_MODE-only /test page + static assets."""

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.conftest import ClientFactory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS_JS = PROJECT_ROOT / "src" / "app" / "static" / "test" / "endpoints.js"
FLOWS_HTML = PROJECT_ROOT / "src" / "app" / "static" / "test" / "flows.html"


def _implemented_catalog_operations() -> set[tuple[str, str]]:
    source = ENDPOINTS_JS.read_text(encoding="utf-8")
    operations: set[tuple[str, str]] = set()
    for match in re.finditer(r"^  \{\n(?P<body>.*?^  \},)", source, re.MULTILINE | re.DOTALL):
        body = match.group("body")
        method_match = re.search(r'^\s{4}method: "([^"]+)"', body, re.MULTILINE)
        path_match = re.search(r'^\s{4}path: "([^"]+)"', body, re.MULTILINE)
        implemented_match = re.search(r"^\s{4}implemented: (true|false)", body, re.MULTILINE)
        if not method_match or not path_match or not implemented_match:
            raise AssertionError(f"Malformed endpoint catalog entry:\n{body}")
        if implemented_match.group(1) == "true":
            operations.add((method_match.group(1), path_match.group(1)))
    return operations


def _openapi_operations(client: TestClient) -> set[tuple[str, str]]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec: dict[str, Any] = response.json()
    operations: set[tuple[str, str]] = set()
    for path, path_item in spec["paths"].items():
        for method in path_item:
            operations.add((method.upper(), path))
    return operations


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


def test_test_flows_page_served_in_local_mode(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/test/flows")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "AI Stock" in body
    assert "系統流程" in body
    assert 'class="mermaid"' in body


def test_test_assets_served_in_local_mode(client_factory: ClientFactory) -> None:
    client = client_factory()
    assert client.get("/test-assets/app.js").status_code == 200
    assert client.get("/test-assets/styles.css").status_code == 200
    assert client.get("/test-assets/endpoints.js").status_code == 200
    assert client.get("/test-assets/user-view.js").status_code == 200
    assert client.get("/test-assets/service-view.js").status_code == 200
    assert client.get("/test-assets/quote-board.js").status_code == 200


def test_endpoint_catalog_covers_current_openapi(client_factory: ClientFactory) -> None:
    client = client_factory()

    assert _implemented_catalog_operations() == _openapi_operations(client)


def test_endpoint_catalog_has_no_duplicate_implemented_operations() -> None:
    source = ENDPOINTS_JS.read_text(encoding="utf-8")
    operations: list[tuple[str, str]] = []
    for match in re.finditer(r"^  \{\n(?P<body>.*?^  \},)", source, re.MULTILINE | re.DOTALL):
        body = match.group("body")
        if not re.search(r"^\s{4}implemented: true", body, re.MULTILINE):
            continue
        method = re.search(r'^\s{4}method: "([^"]+)"', body, re.MULTILINE)
        path = re.search(r'^\s{4}path: "([^"]+)"', body, re.MULTILINE)
        assert method is not None
        assert path is not None
        operations.append((method.group(1), path.group(1)))

    duplicates = sorted({operation for operation in operations if operations.count(operation) > 1})
    assert duplicates == []


def test_flows_page_pins_mermaid_cdn_with_sri() -> None:
    source = FLOWS_HTML.read_text(encoding="utf-8")

    assert "https://cdn.jsdelivr.net/npm/mermaid@11." in source
    assert "https://cdn.jsdelivr.net/npm/mermaid@11/dist/" not in source
    assert 'integrity="sha384-' in source
    assert 'crossorigin="anonymous"' in source
    assert "onerror=" not in source


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
    assert client.get("/test/flows").status_code == 404
    assert client.get("/test-assets/app.js").status_code == 404
    assert client.get("/test-assets/service-view.js").status_code == 404
    assert client.get("/test-assets/quote-board.js").status_code == 404
    # /dev/* must also stay 404 in production mode (regression guard).
    assert client.post("/dev/evaluate-quotes", json={}).status_code == 404
