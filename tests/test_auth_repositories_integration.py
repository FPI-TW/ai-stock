"""Integration tests for the L1 data layer (user / refresh-token repos + audit writer)."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.auth import AuditEvent, User
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def auth_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _seed_user(engine: Engine, email: str, *, role: str = "user", status: str = "active") -> UUID:
    user_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, email=email, role=role, status=status))
        session.commit()
    return user_id


@pytest.mark.integration
def test_get_by_email_is_case_insensitive(auth_engine: Engine) -> None:
    user_id = _seed_user(auth_engine, "Alice@Example.com")

    with Session(auth_engine) as session:
        repo = UserRepository(session)
        by_email = repo.get_by_email("alice@example.com")  # citext: different case still matches
        assert by_email is not None
        assert by_email.id == user_id
        assert repo.get_by_id(user_id) is not None
        assert repo.get_by_email("nobody@example.com") is None


@pytest.mark.integration
def test_refresh_token_create_find_and_revoke(auth_engine: Engine) -> None:
    user_id = _seed_user(auth_engine, f"rt-{uuid4()}@example.com")
    token_hash = f"hash-{uuid4()}"

    with Session(auth_engine) as session:
        repo = RefreshTokenRepository(session)
        token_id = repo.create(user_id=user_id, token_hash=token_hash, expires_at=_NOW + timedelta(days=30))
        session.commit()

        found = repo.find_by_hash(token_hash)
        assert found is not None
        assert found.id == token_id
        assert found.revoked_at is None

        repo.revoke(token_id, reason="logout", now=_NOW)
        session.commit()

        revoked = repo.find_by_hash(token_hash)
        assert revoked is not None
        assert revoked.revoked_reason == "logout"


@pytest.mark.integration
def test_revoke_all_active_leaves_already_revoked_untouched(auth_engine: Engine) -> None:
    user_id = _seed_user(auth_engine, f"chain-{uuid4()}@example.com")
    hash_a, hash_b = f"a-{uuid4()}", f"b-{uuid4()}"

    with Session(auth_engine) as session:
        repo = RefreshTokenRepository(session)
        token_a = repo.create(user_id=user_id, token_hash=hash_a, expires_at=_NOW + timedelta(days=30))
        repo.create(user_id=user_id, token_hash=hash_b, expires_at=_NOW + timedelta(days=30))
        repo.revoke(token_a, reason="rotated", now=_NOW)  # a is already revoked
        session.commit()

        revoked_count = repo.revoke_all_active_for_user(user_id, reason="reuse_detected", now=_NOW)
        session.commit()

        assert revoked_count == 1  # only b was still active
        a_row = repo.find_by_hash(hash_a)
        b_row = repo.find_by_hash(hash_b)
        assert a_row is not None and a_row.revoked_reason == "rotated"  # original reason preserved
        assert b_row is not None and b_row.revoked_reason == "reuse_detected"


@pytest.mark.integration
def test_audit_writer_persists_event(auth_engine: Engine) -> None:
    actor_id = uuid4()

    with Session(auth_engine) as session:
        AuditEventWriter(session).write(
            event_type="login_success",
            actor_type="user",
            actor_id=actor_id,
            metadata={"ip": "203.0.113.7"},
            request_id="req-123",
            now=_NOW,
        )
        session.commit()

    with Session(auth_engine) as session:
        row = session.execute(select(AuditEvent).where(AuditEvent.actor_id == actor_id)).scalar_one()
        assert row.event_type == "login_success"
        assert row.actor_type == "user"
        assert row.event_metadata == {"ip": "203.0.113.7"}
        assert row.request_id == "req-123"
