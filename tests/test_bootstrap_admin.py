from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.passwords import build_password_hasher, verify_password
from app.db.bootstrap_admin import (
    DEFAULT_LOCAL_ADMIN_EMAIL,
    DEFAULT_LOCAL_ADMIN_PASSWORD,
    BootstrapAdminConfig,
    bootstrap_initial_admin,
    resolve_bootstrap_admin_config,
)
from app.db.models.auth import User

LOCAL_USER_ID = "11111111-2222-3333-4444-555555555555"
FAST_ARGON2_ENV = {
    "ARGON2_TIME_COST": "1",
    "ARGON2_MEMORY_COST": "8",
    "ARGON2_PARALLELISM": "1",
}


class _ScalarResult:
    def __init__(self, user: User | None) -> None:
        self._user = user

    def scalar_one_or_none(self) -> User | None:
        return self._user


class _FakeSession:
    def __init__(self, user: User | None = None) -> None:
        self._user = user
        self.added: list[User] = []
        self.commits = 0
        self.last_statement: object | None = None

    def execute(self, statement: object) -> _ScalarResult:
        self.last_statement = statement
        return _ScalarResult(self._user)

    def add(self, user: User) -> None:
        self.added.append(user)

    def commit(self) -> None:
        self.commits += 1


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_mode: bool = True,
    initial_email: str | None = None,
    initial_password: str | None = None,
    allow_non_local: bool = False,
) -> Settings:
    monkeypatch.setenv("LOCAL_MODE", str(local_mode).lower())
    monkeypatch.setenv("LOCAL_USER_ID", LOCAL_USER_ID)
    monkeypatch.setenv("QUOTE_PROVIDER", "in_memory")
    for key, value in FAST_ARGON2_ENV.items():
        monkeypatch.setenv(key, value)

    if initial_email is None:
        monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    else:
        monkeypatch.setenv("INITIAL_ADMIN_EMAIL", initial_email)

    if initial_password is None:
        monkeypatch.delenv("INITIAL_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", initial_password)

    monkeypatch.setenv("INITIAL_ADMIN_ALLOW_NON_LOCAL", str(allow_non_local).lower())
    if not local_mode:
        monkeypatch.setenv("JWT_ACCESS_SECRET", "test-jwt-secret")
        monkeypatch.setenv("MFA_ENCRYPTION_KEY", "test-mfa-secret")

    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_resolve_bootstrap_admin_config_reads_env_values(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        initial_email=" Admin@Tingfong.com ",
        initial_password=" password123 ",
        allow_non_local=True,
    )

    config = resolve_bootstrap_admin_config(settings)

    assert config == BootstrapAdminConfig(
        email="Admin@Tingfong.com",
        password="password123",
        allow_non_local=True,
    )


def test_resolve_bootstrap_admin_config_uses_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)

    config = resolve_bootstrap_admin_config(settings)

    assert config.email == DEFAULT_LOCAL_ADMIN_EMAIL
    assert config.password == DEFAULT_LOCAL_ADMIN_PASSWORD
    assert config.allow_non_local is False


def test_resolve_bootstrap_admin_config_requires_password_outside_local_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, local_mode=False, allow_non_local=True)

    with pytest.raises(RuntimeError, match="INITIAL_ADMIN_PASSWORD is required"):
        resolve_bootstrap_admin_config(settings)


def test_bootstrap_initial_admin_rejects_non_local_mode_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, local_mode=False, initial_password="password123")

    with pytest.raises(RuntimeError, match="disabled outside LOCAL_MODE"):
        bootstrap_initial_admin(
            cast(Session, _FakeSession()),
            settings,
            BootstrapAdminConfig(email="admin@example.com", password="password123", allow_non_local=False),
        )


def test_bootstrap_initial_admin_creates_active_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    fake_db = _FakeSession()
    now = datetime(2026, 6, 17, 10, 30, tzinfo=UTC)

    result = bootstrap_initial_admin(
        cast(Session, fake_db),
        settings,
        BootstrapAdminConfig(email=" Admin@Tingfong.com ", password="password123", allow_non_local=False),
        now=now,
    )

    assert result.action == "created"
    assert result.email == "admin@tingfong.com"
    assert UUID(result.user_id)
    assert fake_db.commits == 1
    assert fake_db.last_statement is not None
    assert len(fake_db.added) == 1

    created = fake_db.added[0]
    assert created.email == "admin@tingfong.com"
    assert created.role == "admin"
    assert created.status == "active"
    assert created.password_hash is not None
    assert verify_password(build_password_hasher(settings), created.password_hash, "password123") is True


def test_bootstrap_initial_admin_updates_existing_user(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    disabled_at = datetime(2026, 6, 16, 9, 0, tzinfo=UTC)
    existing = User(
        id=uuid4(),
        email="admin@tingfong.com",
        password_hash=None,
        role="user",
        status="disabled",
        mfa_enabled=True,
        mfa_secret_encrypted=b"old-secret",
        disabled_at=disabled_at,
    )
    fake_db = _FakeSession(existing)
    now = datetime(2026, 6, 17, 10, 30, tzinfo=UTC)

    result = bootstrap_initial_admin(
        cast(Session, fake_db),
        settings,
        BootstrapAdminConfig(email="admin@tingfong.com", password="newpass123", allow_non_local=False),
        now=now,
    )

    assert result.action == "updated"
    assert result.email == "admin@tingfong.com"
    assert result.user_id == str(existing.id)
    assert fake_db.added == []
    assert fake_db.commits == 1

    assert existing.role == "admin"
    assert existing.status == "active"
    assert existing.mfa_enabled is False
    assert existing.mfa_secret_encrypted is None
    assert existing.disabled_at is None
    assert existing.updated_at == now
    assert existing.password_hash is not None
    assert verify_password(build_password_hasher(settings), existing.password_hash, "newpass123") is True


def test_bootstrap_initial_admin_rejects_blank_email(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    fake_db = _FakeSession()

    with pytest.raises(RuntimeError, match="must not be blank"):
        bootstrap_initial_admin(
            cast(Session, fake_db),
            settings,
            BootstrapAdminConfig(email=" ", password="password123", allow_non_local=False),
        )

    assert fake_db.commits == 0


def test_bootstrap_initial_admin_rejects_malformed_email(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guards the gap PR #67 opened: the auth API now validates login/password-reset
    # email via EmailStr, so an admin whose email has no @-sign would be creatable
    # here yet locked out of login. Reject it at the source instead.
    settings = _settings(monkeypatch)
    fake_db = _FakeSession()

    with pytest.raises(RuntimeError, match="not a valid email"):
        bootstrap_initial_admin(
            cast(Session, fake_db),
            settings,
            BootstrapAdminConfig(email="admin", password="password123", allow_non_local=False),
        )

    assert fake_db.commits == 0
    assert fake_db.added == []


def test_bootstrap_initial_admin_rejects_weak_password(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    fake_db = _FakeSession()

    with pytest.raises(RuntimeError, match="at least 8 characters"):
        bootstrap_initial_admin(
            cast(Session, fake_db),
            settings,
            BootstrapAdminConfig(email="admin@example.com", password="short", allow_non_local=False),
        )

    assert fake_db.commits == 0


def test_bootstrap_initial_admin_no_overwrite_refuses_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    # The per-admin CLI passes overwrite=False so re-running for an existing admin
    # can't silently reset their password.
    settings = _settings(monkeypatch)
    existing = User(
        id=uuid4(),
        email="admin@tingfong.com",
        password_hash="keep-me",
        role="admin",
        status="active",
    )
    fake_db = _FakeSession(existing)

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        bootstrap_initial_admin(
            cast(Session, fake_db),
            settings,
            BootstrapAdminConfig(email="admin@tingfong.com", password="newpass123", allow_non_local=False),
            overwrite=False,
        )

    # untouched: no write, original hash preserved.
    assert fake_db.commits == 0
    assert fake_db.added == []
    assert existing.password_hash == "keep-me"


def test_bootstrap_initial_admin_no_overwrite_creates_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    fake_db = _FakeSession()

    result = bootstrap_initial_admin(
        cast(Session, fake_db),
        settings,
        BootstrapAdminConfig(email="dev@example.com", password="password123", allow_non_local=False),
        overwrite=False,
    )

    assert result.action == "created"
    assert result.email == "dev@example.com"
    assert len(fake_db.added) == 1
    assert fake_db.added[0].role == "admin"
    assert fake_db.added[0].status == "active"
    assert fake_db.commits == 1
