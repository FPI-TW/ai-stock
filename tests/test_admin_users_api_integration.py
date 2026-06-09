"""HTTP integration tests for L1 admin account management: disable cascade,
resend invitation (incl. throttle), and the user list. Against real PostgreSQL.
"""

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_mailer
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.auth import RefreshToken, User
from app.db.models.core import Symbol, TradeIntent
from app.main import create_app
from app.services.mailer import MailMessage

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


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
def admin_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    with Session(engine) as session:
        session.add(
            Symbol(
                id=uuid4(),
                symbol="2330",
                display_name="台積電",
                market="TWSE",
                instrument_type="stock",
                tradable_status="tradable",
            )
        )
        session.commit()
    try:
        yield engine
    finally:
        # Remove intents first: the disable test leaves cancelled_by_account_disabled
        # rows, which would trip the 202605290002 downgrade guard.
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM trade_intents"))
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


def _admin_client(engine: Engine, mailer: _RecordingMailer | None = None) -> TestClient:
    admin_id = _seed_admin(engine)
    app = create_app()
    if mailer is not None:
        app.dependency_overrides[get_mailer] = lambda: mailer
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=admin_id, role="admin", mfa_verified=True)
    return TestClient(app)


def _seed_active_user(engine: Engine, email: str) -> UUID:
    user_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, email=email, role="user", status="active"))
        session.commit()
    return user_id


def _seed_intent(engine: Engine, owner_id: UUID, *, status: str = "active") -> UUID:
    intent_id = uuid4()
    with Session(engine) as session:
        session.add(
            TradeIntent(
                id=intent_id,
                owner_user_id=owner_id,
                symbol="2330",
                strategy="buy_price_alert",
                execution_mode="notify_only",
                quantity_lots=1,
                target_price_original=Decimal("100.0000"),
                target_price_effective=Decimal("100.0000"),
                trigger_reference_price_type="ask",
                trading_date=date(2026, 1, 1),
                time_in_force="day",
                status=status,
            )
        )
        session.commit()
    return intent_id


def _seed_refresh_token(engine: Engine, user_id: UUID) -> UUID:
    token_id = uuid4()
    with Session(engine) as session:
        session.add(
            RefreshToken(
                id=token_id,
                user_id=user_id,
                token_hash=f"hash-{token_id}",
                expires_at=_NOW + timedelta(days=30),
            )
        )
        session.commit()
    return token_id


@pytest.mark.integration
def test_disable_cascades_to_intents_and_tokens(admin_engine: Engine) -> None:
    client = _admin_client(admin_engine)
    user_id = _seed_active_user(admin_engine, f"disable-{uuid4()}@example.com")
    intent_id = _seed_intent(admin_engine, user_id, status="active")
    token_id = _seed_refresh_token(admin_engine, user_id)

    response = client.post(f"/admin/users/{user_id}/disable")
    assert response.status_code == 204

    with Session(admin_engine) as session:
        user = session.execute(select(User).where(User.id == user_id)).scalar_one()
        intent = session.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one()
        token = session.execute(select(RefreshToken).where(RefreshToken.id == token_id)).scalar_one()
        assert user.status == "disabled"
        assert user.disabled_at is not None
        assert intent.status == "cancelled_by_account_disabled"
        assert token.revoked_at is not None
        assert token.revoked_reason == "account_disabled"


@pytest.mark.integration
def test_disable_unknown_user_is_404(admin_engine: Engine) -> None:
    client = _admin_client(admin_engine)

    response = client.post(f"/admin/users/{uuid4()}/disable")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.integration
def test_resend_invitation_revokes_old_and_mails_new(admin_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _admin_client(admin_engine, mailer)
    email = f"resend-{uuid4()}@example.com"

    created = client.post("/admin/users", json={"email": email, "role": "user"})
    user_id = created.json()["id"]
    first_token = mailer.messages[-1].body.split("token=", 1)[1].strip()

    resent = client.post(f"/admin/users/{user_id}/resend-invitation")
    assert resent.status_code == 204
    second_token = mailer.messages[-1].body.split("token=", 1)[1].strip()
    assert second_token != first_token

    # The original token is now revoked and cannot be accepted.
    accept = client.post(
        "/auth/invitations/accept",
        json={"token": first_token, "password": "password123", "termsVersion": "2026-01"},
    )
    assert accept.status_code == 400
    assert accept.json()["error"]["code"] == "INVITATION_INVALID"


@pytest.mark.integration
def test_resend_for_active_user_is_rejected(admin_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _admin_client(admin_engine, mailer)
    user_id = _seed_active_user(admin_engine, f"active-{uuid4()}@example.com")

    response = client.post(f"/admin/users/{user_id}/resend-invitation")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVITATION_INVALID"


@pytest.mark.integration
def test_resend_is_rate_limited(admin_engine: Engine) -> None:
    mailer = _RecordingMailer()
    client = _admin_client(admin_engine, mailer)
    email = f"throttle-{uuid4()}@example.com"
    user_id = client.post("/admin/users", json={"email": email, "role": "user"}).json()["id"]

    # Bucket capacity is 3 resends/hour; the fourth in quick succession is throttled.
    for _ in range(3):
        assert client.post(f"/admin/users/{user_id}/resend-invitation").status_code == 204
    throttled = client.post(f"/admin/users/{user_id}/resend-invitation")
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "RATE_LIMITED"
    assert "Retry-After" in throttled.headers


@pytest.mark.integration
def test_list_users_returns_seeded_users(admin_engine: Engine) -> None:
    client = _admin_client(admin_engine)
    email = f"listed-{uuid4()}@example.com"
    _seed_active_user(admin_engine, email)

    response = client.get("/admin/users")

    assert response.status_code == 200
    emails = {row["email"] for row in response.json()["data"]}
    assert email in emails
