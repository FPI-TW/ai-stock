"""Fixtures for end-to-end UI smoke tests (Playwright).

These tests drive a real chromium browser against a real uvicorn subprocess
that talks to local PostgreSQL — same prerequisite as the integration tests.

Run with:
    make test-ui                            # all UI tests
    uv run pytest -m ui                     # equivalent
    uv run pytest -m ui tests/ui/test_smoke.py::test_<name>  # single test

One-time setup (download chromium ~150 MB):
    uv run playwright install chromium
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from collections.abc import Generator

import httpx
import pytest
from playwright.sync_api import Page

# Deterministic UUIDs for the three test users — written into localStorage
# by ``pre_seed_users`` so backend X-Local-User-Id headers and frontend
# user identity match across the run.
STABLE_USER_UUIDS: dict[str, str] = {
    "alice": "11111111-1111-1111-1111-111111111111",
    "bob": "22222222-2222-2222-2222-222222222222",
    "charlie": "33333333-3333-3333-3333-333333333333",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def live_server_url() -> Generator[str]:
    """Spawn uvicorn in a subprocess for the test session.

    Uses the same DATABASE_URL convention as `make test-integration`. The
    process inherits the parent environment so a normal `make test-ui` shell
    invocation works out of the box.
    """
    port = _free_port()
    env = os.environ.copy()
    env.setdefault("LOCAL_MODE", "true")
    env.setdefault("QUOTE_PROVIDER", "in_memory")
    env.setdefault("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")

    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "src",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited before ready (returncode={proc.returncode})")
        try:
            r = httpx.get(f"{base_url}/health", timeout=1.0)
            if r.status_code == 200:
                last_err = None
                break
        except (httpx.RequestError, ConnectionError) as exc:
            last_err = exc
        time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError(f"uvicorn did not become healthy in 30s: {last_err!r}")

    try:
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def api(live_server_url: str) -> httpx.Client:
    """HTTP client pointed at the live server for direct API setup/teardown.

    UI tests use this to seed deterministic state (cancel old intents,
    freeze clock, preload an intent) so the Playwright drive only exercises
    the part of the flow that's actually being asserted.
    """
    return httpx.Client(base_url=live_server_url, timeout=5.0)


def alice_headers() -> dict[str, str]:
    return {"X-Local-User-Id": STABLE_USER_UUIDS["alice"]}


def freeze_clock_in_session(api: httpx.Client) -> None:
    api.post("/dev/set-clock", json={"fakeNow": "2026-05-26T09:30:00+08:00"}).raise_for_status()


def reset_clock(api: httpx.Client) -> None:
    api.post("/dev/set-clock", json={}).raise_for_status()


def cancel_all_active_intents(api: httpx.Client, headers: dict[str, str]) -> None:
    resp = api.get("/trade-intents?pageSize=100", headers=headers)
    resp.raise_for_status()
    for intent in resp.json().get("data", []):
        if intent["status"] in ("active", "scheduled"):
            api.post(f"/trade-intents/{intent['id']}/cancel", headers=headers).raise_for_status()


@pytest.fixture
def pre_seed_users(page: Page) -> None:
    """Write stable user UUIDs + dev mode into localStorage before navigation.

    Must run before page.goto() so the frontend reads them on boot. Playwright
    add_init_script applies on every navigation in the page's lifetime.
    """
    page.add_init_script(
        f"""
        localStorage.setItem('ai-stock-test-users', {json.dumps(json.dumps(STABLE_USER_UUIDS))});
        localStorage.setItem('ai-stock-test-mode', 'dev');
        localStorage.setItem('ai-stock-test-user-label', 'default');
        """
    )
