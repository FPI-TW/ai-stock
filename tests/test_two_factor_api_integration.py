"""HTTP integration tests for admin TOTP 2FA against real PostgreSQL.

Closes the L1 admin loop: an admin logs in (no verified factor) → admin endpoints
403 → /admin/2fa/setup → /admin/2fa/verify with a real TOTP code elevates the session
→ admin endpoints work, and the elevated state survives an access-token refresh.
"""

from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pyotp
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

_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
_PASSWORD = "admin-password-123"


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def mfa_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _seed_admin(engine: Engine, email: str) -> None:
    with Session(engine) as session:
        session.add(
            User(
                id=uuid4(),
                email=email,
                role="admin",
                status="active",
                mfa_enabled=False,
                password_hash=hash_password(_HASHER, _PASSWORD),
            )
        )
        session.commit()


def _logged_in_admin(engine: Engine, email: str) -> tuple[TestClient, str]:
    """Returns a client with login cookies and the (un-elevated) access token."""
    _seed_admin(engine, email)
    client = TestClient(create_app())
    login = client.post("/auth/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 200
    return client, login.json()["accessToken"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
def test_admin_2fa_enrol_verify_and_access(mfa_engine: Engine) -> None:
    client, access = _logged_in_admin(mfa_engine, f"admin-{uuid4()}@example.com")

    # Before verifying 2FA, admin endpoints are gated.
    gated = client.get("/admin/users", headers=_bearer(access))
    assert gated.status_code == 403
    assert gated.json()["error"]["code"] == "MFA_REQUIRED"

    # Enrol: obtain a TOTP secret.
    setup = client.post("/admin/2fa/setup", headers=_bearer(access))
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["provisioningUri"].startswith("otpauth://totp/")

    # Verify with a real code → elevated access token.
    code = pyotp.TOTP(secret).now()
    verify = client.post("/admin/2fa/verify", json={"code": code}, headers=_bearer(access))
    assert verify.status_code == 200
    elevated = verify.json()["accessToken"]
    assert elevated != access

    # The elevated token clears the admin gate.
    assert client.get("/admin/users", headers=_bearer(elevated)).status_code == 200

    # And the verified state survives an access-token refresh.
    csrf = client.cookies.get("csrf_token")
    assert csrf is not None
    refreshed = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert refreshed.status_code == 200
    assert client.get("/admin/users", headers=_bearer(refreshed.json()["accessToken"])).status_code == 200


@pytest.mark.integration
def test_verify_with_wrong_code_is_422(mfa_engine: Engine) -> None:
    client, access = _logged_in_admin(mfa_engine, f"admin-bad-{uuid4()}@example.com")
    client.post("/admin/2fa/setup", headers=_bearer(access))

    response = client.post("/admin/2fa/verify", json={"code": "000000"}, headers=_bearer(access))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MFA_INVALID_CODE"


@pytest.mark.integration
def test_verify_before_setup_is_409(mfa_engine: Engine) -> None:
    client, access = _logged_in_admin(mfa_engine, f"admin-nosetup-{uuid4()}@example.com")

    response = client.post("/admin/2fa/verify", json={"code": "000000"}, headers=_bearer(access))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MFA_NOT_SETUP"


@pytest.mark.integration
def test_setup_when_already_enabled_is_409(mfa_engine: Engine) -> None:
    client, access = _logged_in_admin(mfa_engine, f"admin-dup-{uuid4()}@example.com")
    secret = client.post("/admin/2fa/setup", headers=_bearer(access)).json()["secret"]
    client.post("/admin/2fa/verify", json={"code": pyotp.TOTP(secret).now()}, headers=_bearer(access))

    # A second setup on an already-enrolled admin is rejected (reset is a separate flow).
    response = client.post("/admin/2fa/setup", headers=_bearer(access))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MFA_ALREADY_ENABLED"


@pytest.mark.integration
def test_non_admin_cannot_reach_2fa_setup(mfa_engine: Engine) -> None:
    email = f"user-{uuid4()}@example.com"
    with Session(mfa_engine) as session:
        session.add(
            User(
                id=uuid4(),
                email=email,
                role="user",
                status="active",
                password_hash=hash_password(_HASHER, _PASSWORD),
            )
        )
        session.commit()
    client = TestClient(create_app())
    access = client.post("/auth/login", json={"email": email, "password": _PASSWORD}).json()["accessToken"]

    response = client.post("/admin/2fa/setup", headers=_bearer(access))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
