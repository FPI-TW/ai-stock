"""Tests for ``/dev/set-clock`` and ``/dev/server-state``.

Focus on the API surface — the underlying ``TradingSessionService.set_clock``
behaviour is exercised via the response payloads. Multi-worker caveat is
intentionally not tested (LOCAL_MODE only runs 1 uvicorn worker).
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.conftest import ClientFactory


def test_set_clock_freezes_at_fake_now(client_factory: ClientFactory) -> None:
    client = client_factory()
    fake_now = "2026-05-11T10:00:00+08:00"

    response = client.post("/dev/set-clock", json={"fakeNow": fake_now})
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["isFrozen"] is True
    assert body["withinRegularSession"] is True
    # currentTaipei is normalised to Asia/Taipei
    parsed = datetime.fromisoformat(body["currentTaipei"])
    assert parsed.utcoffset() is not None
    assert parsed.hour == 10


def test_set_clock_advance_seconds_on_top_of_fake_now(client_factory: ClientFactory) -> None:
    client = client_factory()
    client.post("/dev/set-clock", json={"fakeNow": "2026-05-11T10:00:00+08:00"})

    response = client.post("/dev/set-clock", json={"advanceSeconds": 60})
    assert response.status_code == 200
    parsed = datetime.fromisoformat(response.json()["data"]["currentTaipei"])
    assert parsed.hour == 10 and parsed.minute == 1


def test_set_clock_empty_body_resets_to_system(client_factory: ClientFactory) -> None:
    client = client_factory()
    client.post("/dev/set-clock", json={"fakeNow": "2026-05-11T10:00:00+08:00"})

    response = client.post("/dev/set-clock", json={})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["isFrozen"] is False
    # currentTaipei is now system clock; just check it parses + is recent.
    parsed = datetime.fromisoformat(body["currentTaipei"])
    now = datetime.now(tz=parsed.tzinfo)
    assert abs((now - parsed).total_seconds()) < 60


def test_server_state_reports_header_user(client_factory: ClientFactory) -> None:
    client = client_factory()
    override = "11111111-2222-3333-4444-555555555555"

    response = client.get("/dev/server-state", headers={"X-Local-User-Id": override})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["currentUser"]["userId"] == override
    assert body["currentUser"]["source"] == "header"
    assert body["localMode"] is True
    assert body["quoteProvider"] == "InMemoryQuoteProvider"
    assert isinstance(body["workerPid"], int)
    assert body["clock"]["isFrozen"] in (True, False)


def test_server_state_reports_default_user_without_header(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/dev/server-state")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["currentUser"]["source"] == "default"
    # Default LOCAL_USER_ID from conftest.
    UUID(body["currentUser"]["userId"])  # parses


def test_dev_endpoints_404_when_local_mode_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODE", "false")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)

    assert client.post("/dev/set-clock", json={}).status_code == 404
    assert client.post("/dev/push-quote", json={"symbol": "2330", "askPrice": "100"}).status_code == 404
    assert client.get("/dev/server-state").status_code == 404


def test_set_clock_state_persists_across_requests(client_factory: ClientFactory) -> None:
    """Sanity check that the clock state survives between requests (app.state)."""
    client = client_factory()
    client.post("/dev/set-clock", json={"fakeNow": "2026-05-11T10:00:00+08:00"})

    state = client.get("/dev/server-state").json()["data"]
    assert state["clock"]["isFrozen"] is True
    assert datetime.fromisoformat(state["clock"]["currentTaipei"]).hour == 10
    # And UTC equivalent:
    utc_view = datetime.fromisoformat(state["clock"]["currentTaipei"]).astimezone(UTC)
    assert utc_view.hour == 2  # 10 TAIPEI = 02 UTC
