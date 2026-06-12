"""HTTP integration tests for the L1 account lifecycle: admin invite -> accept -> login,
plus the admin gate and the invitation/password error cases. Against real PostgreSQL.
"""

from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_active_user, get_current_user, get_mailer
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.auth import User
from app.main import create_app
from app.services.mailer import MailMessage


class _RecordingMailer:
    def __init__(self) -> None:
        self.messages: list[MailMessage] = []

    def send(self, message: MailMessage) -> None:
        self.messages.append(message)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def account_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _seed_admin(engine: Engine) -> UUID:
    admin_id = uuid4()
    with Session(engine) as session:
        session.add(
            User(id=admin_id, email=f"admin-{admin_id}@example.com", role="admin", status="active", mfa_enabled=True)
        )
        session.commit()
    return admin_id


def _extract_token(body: str) -> str:
    return body.split("token=", 1)[1].strip()


def _admin_client(engine: Engine, mailer: _RecordingMailer) -> TestClient:
    admin_id = _seed_admin(engine)
    app = create_app()
    app.dependency_overrides[get_mailer] = lambda: mailer
    principal = RequestUser(user_id=admin_id, role="admin", mfa_verified=True)
    # ActiveUserDep re-checks the session in the DB; mirror the override so the seeded
    # admin (which has no refresh-token row) isn't rejected by the revocation gate.
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    return TestClient(app)


@pytest.mark.integration
def test_invite_accept_login_full_chain(account_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _admin_client(account_engine, mailer)
    email = f"invitee-{uuid4()}@example.com"

    # Admin creates the invited user; an invitation mail is "sent".
    created = client.post("/admin/users", json={"email": email, "role": "user"})
    assert created.status_code == 201
    assert created.json()["status"] == "invited"
    token = _extract_token(mailer.messages[-1].body)

    # Invitee accepts: sets password, accepts terms, and is logged in.
    accepted = client.post(
        "/auth/invitations/accept",
        json={"token": token, "password": "password123", "termsVersion": "2026-01"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["accessToken"]

    # The activated account can now log in normally.
    login = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200

    # The invitation token cannot be reused.
    reused = client.post(
        "/auth/invitations/accept",
        json={"token": token, "password": "password123", "termsVersion": "2026-01"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "INVITATION_CONSUMED"


@pytest.mark.integration
def test_create_user_requires_admin_role(account_engine: Engine) -> None:
    app = create_app()
    principal = RequestUser(user_id=uuid4(), role="user", mfa_verified=True)
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    response = TestClient(app).post("/admin/users", json={"email": f"x-{uuid4()}@y.com", "role": "user"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.integration
def test_create_user_requires_verified_mfa(account_engine: Engine) -> None:
    app = create_app()
    principal = RequestUser(user_id=uuid4(), role="admin", mfa_verified=False)
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    response = TestClient(app).post("/admin/users", json={"email": f"x-{uuid4()}@y.com", "role": "user"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MFA_REQUIRED"


@pytest.mark.integration
def test_duplicate_email_is_rejected(account_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _admin_client(account_engine, mailer)
    email = f"dup-{uuid4()}@example.com"

    assert client.post("/admin/users", json={"email": email, "role": "user"}).status_code == 201
    second = client.post("/admin/users", json={"email": email, "role": "user"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.integration
def test_accept_with_invalid_token_is_rejected(account_engine: Engine) -> None:
    app = create_app()
    response = TestClient(app).post(
        "/auth/invitations/accept",
        json={"token": "not-a-real-token", "password": "password123", "termsVersion": "2026-01"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVITATION_INVALID"


@pytest.mark.integration
def test_accept_with_weak_password_is_rejected(account_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _admin_client(account_engine, mailer)
    email = f"weak-{uuid4()}@example.com"
    client.post("/admin/users", json={"email": email, "role": "user"})
    token = _extract_token(mailer.messages[-1].body)

    response = client.post(
        "/auth/invitations/accept",
        json={"token": token, "password": "short", "termsVersion": "2026-01"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WEAK_PASSWORD"
