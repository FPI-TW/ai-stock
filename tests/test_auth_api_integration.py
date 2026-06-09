"""HTTP-level integration tests for the /auth routes against real PostgreSQL.

Exercises the full browser-shaped flow: login -> Set-Cookie, Bearer-authenticated
/auth/me, cookie+CSRF refresh, and the 401 on protected endpoints without a token.
"""

from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db.models.auth import User
from app.main import create_app

# Cheap argon2; the stored hash embeds its own params, so the app's default-cost
# verifier still validates it.
_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
_PASSWORD = "correct-horse-battery"


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def auth_api_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _seed_active_user(engine: Engine, email: str, *, role: str = "user") -> None:
    with Session(engine) as session:
        session.add(
            User(
                id=uuid4(),
                email=email,
                role=role,
                status="active",
                password_hash=hash_password(_HASHER, _PASSWORD),
            )
        )
        session.commit()


@pytest.mark.integration
def test_login_me_and_refresh_flow(auth_api_engine: Engine) -> None:
    email = f"api-{uuid4()}@example.com"
    _seed_active_user(auth_api_engine, email)
    client = TestClient(create_app())

    # Login -> 200 with access token in body, refresh + csrf as cookies.
    login = client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 200
    access_token = login.json()["accessToken"]
    assert login.json()["tokenType"] == "Bearer"
    assert "refresh_token" in client.cookies
    assert "csrf_token" in client.cookies

    # /auth/me returns the profile for the Bearer token.
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert me.json()["role"] == "user"
    assert me.json()["mfaEnabled"] is False

    # Refresh with the cookie + matching CSRF header rotates and returns a new token.
    csrf = client.cookies.get("csrf_token")
    assert csrf is not None
    refreshed = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert refreshed.status_code == 200
    assert refreshed.json()["accessToken"] != access_token


@pytest.mark.integration
def test_protected_endpoint_without_token_is_401(auth_api_engine: Engine) -> None:
    client = TestClient(create_app())

    response = client.get("/trade-intents")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.integration
def test_login_wrong_password_is_401_login_failed(auth_api_engine: Engine) -> None:
    email = f"api-bad-{uuid4()}@example.com"
    _seed_active_user(auth_api_engine, email)
    client = TestClient(create_app())

    response = client.post("/auth/login", json={"email": email, "password": "nope"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LOGIN_FAILED"


@pytest.mark.integration
def test_refresh_without_csrf_header_is_403(auth_api_engine: Engine) -> None:
    email = f"api-csrf-{uuid4()}@example.com"
    _seed_active_user(auth_api_engine, email)
    client = TestClient(create_app())
    client.post("/auth/login", json={"email": email, "password": _PASSWORD})

    # Refresh cookie is present but no X-CSRF-Token header -> double-submit fails.
    response = client.post("/auth/refresh")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_FAILED"
