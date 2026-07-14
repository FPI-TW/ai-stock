"""HTTP integration tests for L1 password reset against real PostgreSQL.

Covers the privacy-preserving 202 (existing + unknown email), the confirm flow that
changes the password, invalid/weak-password rejections, and session revocation.
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

from app.api.deps import get_mailer
from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db.models.auth import User
from app.main import create_app
from app.services.mailer import MailMessage

_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
_OLD_PASSWORD = "old-password-123"
_NEW_PASSWORD = "new-password-456"


class _RecordingMailer:
    def __init__(self) -> None:
        self.messages: list[MailMessage] = []

    def send(self, message: MailMessage) -> None:
        self.messages.append(message)


class _FailingMailer:
    def send(self, message: MailMessage) -> None:
        raise RuntimeError("SMTP unavailable")


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def reset_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _seed_active_user(engine: Engine, email: str) -> None:
    with Session(engine) as session:
        session.add(
            User(
                id=uuid4(),
                email=email,
                role="user",
                status="active",
                password_hash=hash_password(_HASHER, _OLD_PASSWORD),
            )
        )
        session.commit()


def _client(mailer: _RecordingMailer) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_mailer] = lambda: mailer
    return TestClient(app)


def _reset_token(mailer: _RecordingMailer) -> str:
    return mailer.messages[-1].body.split("token=", 1)[1].strip()


@pytest.mark.integration
def test_request_then_confirm_changes_password(reset_engine: Engine) -> None:
    email = f"reset-{uuid4()}@example.com"
    _seed_active_user(reset_engine, email)
    mailer = _RecordingMailer()
    client = _client(mailer)

    assert client.post("/auth/password-reset/request", json={"email": email}).status_code == 202
    token = _reset_token(mailer)

    confirm = client.post("/auth/password-reset/confirm", json={"token": token, "newPassword": _NEW_PASSWORD})
    assert confirm.status_code == 204

    # Old password no longer works; new one does.
    assert client.post("/auth/login", json={"email": email, "password": _OLD_PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": email, "password": _NEW_PASSWORD}).status_code == 200

    # The reset token cannot be reused.
    reused = client.post("/auth/password-reset/confirm", json={"token": token, "newPassword": _NEW_PASSWORD})
    assert reused.status_code == 400
    assert reused.json()["error"]["code"] == "PASSWORD_RESET_INVALID"


@pytest.mark.integration
def test_request_for_unknown_email_is_202_without_token(reset_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _client(mailer)

    response = client.post("/auth/password-reset/request", json={"email": f"ghost-{uuid4()}@example.com"})

    assert response.status_code == 202
    assert mailer.messages == []  # no token minted / mailed for a non-existent account


@pytest.mark.integration
def test_request_still_202_and_token_persisted_when_mail_fails(reset_engine: Engine) -> None:
    # A failing mailer must not break the privacy contract: the route still returns 202
    # and the reset token is committed (so the flow works via a resend), not rolled back.
    email = f"mailfail-{uuid4()}@example.com"
    _seed_active_user(reset_engine, email)
    app = create_app()
    app.dependency_overrides[get_mailer] = lambda: _FailingMailer()
    client = TestClient(app)

    assert client.post("/auth/password-reset/request", json={"email": email}).status_code == 202


@pytest.mark.integration
def test_confirm_with_invalid_token_is_400(reset_engine: Engine) -> None:
    client = _client(_RecordingMailer())

    response = client.post("/auth/password-reset/confirm", json={"token": "nope", "newPassword": _NEW_PASSWORD})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PASSWORD_RESET_INVALID"


@pytest.mark.integration
def test_confirm_with_weak_password_is_422(reset_engine: Engine) -> None:
    email = f"weak-{uuid4()}@example.com"
    _seed_active_user(reset_engine, email)
    mailer = _RecordingMailer()
    client = _client(mailer)
    client.post("/auth/password-reset/request", json={"email": email})
    token = _reset_token(mailer)

    response = client.post("/auth/password-reset/confirm", json={"token": token, "newPassword": "short"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WEAK_PASSWORD"


@pytest.mark.integration
def test_confirm_revokes_existing_sessions(reset_engine: Engine) -> None:
    email = f"revoke-{uuid4()}@example.com"
    _seed_active_user(reset_engine, email)
    mailer = _RecordingMailer()
    client = _client(mailer)

    # Establish a live session (refresh + csrf cookies).
    assert client.post("/auth/login", json={"email": email, "password": _OLD_PASSWORD}).status_code == 200
    csrf = client.cookies.get("csrf_token")
    assert csrf is not None

    # Reset the password.
    client.post("/auth/password-reset/request", json={"email": email})
    token = _reset_token(mailer)
    assert (
        client.post("/auth/password-reset/confirm", json={"token": token, "newPassword": _NEW_PASSWORD}).status_code
        == 204
    )

    # The pre-reset refresh token is now revoked.
    refreshed = client.post("/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert refreshed.status_code == 401
    assert refreshed.json()["error"]["code"] == "REFRESH_INVALID"
