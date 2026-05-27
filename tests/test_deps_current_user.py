from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUserDep, get_current_user
from app.core.config import get_settings
from app.core.security import RequestUser
from app.main import create_app


def _attach_whoami_route(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/__test/current-user")
    def whoami(user: CurrentUserDep) -> dict[str, str]:
        return {"userId": str(user.user_id), "role": user.role}

    app.include_router(router)


def test_get_current_user_returns_local_user(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_uuid = UUID("12345678-1234-1234-1234-123456789012")
    monkeypatch.setenv("LOCAL_USER_ID", str(expected_uuid))
    monkeypatch.setenv("LOCAL_MODE", "true")

    app = create_app()
    _attach_whoami_route(app)
    client = TestClient(app)

    response = client.get("/__test/current-user")

    assert response.status_code == 200
    assert response.json() == {"userId": str(expected_uuid), "role": "local"}


def test_get_current_user_can_be_overridden() -> None:
    override_uuid = uuid4()

    app = create_app()
    _attach_whoami_route(app)
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=override_uuid, role="local")
    client = TestClient(app)

    response = client.get("/__test/current-user")

    assert response.status_code == 200
    assert response.json() == {"userId": str(override_uuid), "role": "local"}


def test_x_local_user_id_header_overrides_default_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODE", "true")
    get_settings.cache_clear()
    override = uuid4()

    app = create_app()
    _attach_whoami_route(app)
    client = TestClient(app)

    response = client.get("/__test/current-user", headers={"X-Local-User-Id": str(override)})

    assert response.status_code == 200
    assert response.json()["userId"] == str(override)


def test_x_local_user_id_header_ignored_when_local_mode_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    default_uuid = UUID("99999999-9999-9999-9999-999999999999")
    monkeypatch.setenv("LOCAL_MODE", "false")
    monkeypatch.setenv("LOCAL_USER_ID", str(default_uuid))
    get_settings.cache_clear()
    attacker = uuid4()

    app = create_app()
    _attach_whoami_route(app)
    client = TestClient(app)

    response = client.get("/__test/current-user", headers={"X-Local-User-Id": str(attacker)})

    assert response.status_code == 200
    assert response.json()["userId"] == str(default_uuid), "production mode must ignore the header"


def test_x_local_user_id_header_invalid_uuid_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_MODE", "true")
    get_settings.cache_clear()

    app = create_app()
    _attach_whoami_route(app)
    client = TestClient(app)

    response = client.get("/__test/current-user", headers={"X-Local-User-Id": "not-a-uuid"})
    assert response.status_code == 422
