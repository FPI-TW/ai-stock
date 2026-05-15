from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUserDep, get_current_user
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
