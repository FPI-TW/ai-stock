from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import CurrentUserDep, get_current_user
from app.core.config import get_settings
from app.core.security import RequestUser
from app.core.tokens import encode_access_token
from app.main import create_app


def _attach_whoami_route(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/__test/current-user")
    def whoami(user: CurrentUserDep) -> dict[str, str]:
        return {"userId": str(user.user_id), "role": user.role}

    app.include_router(router)


def _client() -> TestClient:
    app = create_app()
    _attach_whoami_route(app)
    return TestClient(app)


def test_valid_bearer_token_resolves_user() -> None:
    user_id = UUID("12345678-1234-1234-1234-123456789012")
    secret = get_settings().resolved_jwt_access_secret
    token = encode_access_token(
        secret, sub=user_id, role="user", session_id=uuid4(), ttl_seconds=900, now=datetime.now(UTC)
    )

    response = _client().get("/__test/current-user", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"userId": str(user_id), "role": "user"}


def test_missing_authorization_header_is_unauthenticated() -> None:
    response = _client().get("/__test/current-user")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_malformed_token_is_unauthenticated() -> None:
    response = _client().get("/__test/current-user", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_get_current_user_can_be_overridden() -> None:
    override_uuid = uuid4()

    app = create_app()
    _attach_whoami_route(app)
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=override_uuid, role="user")
    client = TestClient(app)

    response = client.get("/__test/current-user")

    assert response.status_code == 200
    assert response.json() == {"userId": str(override_uuid), "role": "user"}
