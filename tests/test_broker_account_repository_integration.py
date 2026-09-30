"""`BrokerAccountRepository` against real PostgreSQL: the PR3 members (decrypt,
list, login-status writers) that the lifespan login loop and the reconnect loop
depend on. Encryption / upsert / delete are covered by the bind API tests."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.broker_account import BrokerAccount
from app.domain.broker_account import (
    LOGIN_FAILURE_MESSAGES,
    BrokerCredentialKeyError,
    FubonCredentials,
)
from app.repositories.broker_account_repository import BrokerAccountRepository
from tests.db_helpers import ensure_user

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
CREDS = FubonCredentials(personal_id="A123456789", password="login-pw", cert_pfx=b"\x30\x82pfx", cert_password="cpw")


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def db(engine: Engine) -> Generator[Session]:
    session = Session(engine)
    try:
        session.execute(delete(BrokerAccount))
        session.commit()
        yield session
    finally:
        session.rollback()
        session.close()


def _bind(db: Session, repo: BrokerAccountRepository) -> "uuid4":  # type: ignore[valid-type]
    user_id = ensure_user(db, uuid4())
    repo.upsert(
        user_id,
        broker="fubon",
        credentials=CREDS,
        broker_account_no="9876543",
        cert_expires_at=NOW + timedelta(days=365),
        now=NOW,
    )
    db.commit()
    return user_id


@pytest.mark.integration
def test_get_credentials_round_trips_the_login_material(db: Session) -> None:
    repo = BrokerAccountRepository(db, encryption_key=KEY)
    user_id = _bind(db, repo)

    assert repo.get_credentials(user_id) == CREDS
    assert repo.get_credentials(uuid4()) is None


@pytest.mark.integration
def test_get_credentials_after_key_rotation_is_undecryptable_not_a_crash(db: Session) -> None:
    """Rotating MFA_ENCRYPTION_KEY leaves every binding unreadable; the startup loop
    must be able to tell that apart from "not bound" and mark the row instead of dying."""
    user_id = _bind(db, BrokerAccountRepository(db, encryption_key=KEY))

    with pytest.raises(BrokerCredentialKeyError) as info:
        BrokerAccountRepository(db, encryption_key=OTHER_KEY).get_credentials(user_id)
    assert info.value.reason == "undecryptable"

    with pytest.raises(BrokerCredentialKeyError) as missing:
        BrokerAccountRepository(db, encryption_key=None).get_credentials(user_id)
    assert missing.value.reason == "missing"


@pytest.mark.integration
def test_list_all_returns_every_binding_without_secrets(db: Session) -> None:
    repo = BrokerAccountRepository(db, encryption_key=KEY)
    first, second = _bind(db, repo), _bind(db, repo)

    rows = repo.list_all()

    assert {r.user_id for r in rows} == {first, second}
    assert all(not hasattr(r, "credentials_encrypted") for r in rows)
    # Readable without the key: the lifespan lists first, decrypts per user.
    assert {r.user_id for r in BrokerAccountRepository(db, encryption_key=None).list_all()} == {first, second}


@pytest.mark.integration
def test_mark_login_failed_then_ok_flips_status_and_clears_the_safe_message(db: Session) -> None:
    repo = BrokerAccountRepository(db, encryption_key=KEY)
    user_id = _bind(db, repo)

    repo.mark_login_failed(user_id, "login_rejected", now=NOW + timedelta(minutes=1))
    db.commit()
    failed = repo.get_by_user_id(user_id)
    assert failed is not None
    assert failed.status == "login_failed"
    assert failed.last_error == LOGIN_FAILURE_MESSAGES["login_rejected"]
    assert failed.last_login_at == NOW  # unchanged: the last *successful* login
    assert failed.updated_at == NOW + timedelta(minutes=1)

    repo.mark_login_ok(user_id, now=NOW + timedelta(minutes=2))
    db.commit()
    ok = repo.get_by_user_id(user_id)
    assert ok is not None
    assert ok.status == "active"
    assert ok.last_error is None
    assert ok.last_login_at == NOW + timedelta(minutes=2)

    # Unknown user: no row, no error (the loop only marks rows it just listed).
    repo.mark_login_failed(uuid4(), "unknown", now=NOW)
    repo.mark_login_ok(uuid4(), now=NOW)
