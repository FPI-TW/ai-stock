"""End-to-end integration tests for the L1 auth commands against real PostgreSQL.

Covers login success, the failure-undifferentiation + lockout counter, refund on
success, refresh rotation, refresh-reuse chain revocation, logout, and CSRF.
Each helper opens its own Session so command-owned commits behave like real requests.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.commands.auth import (
    IssuedSession,
    LoginCommand,
    LoginInput,
    LogoutCommand,
    LogoutInput,
    RefreshCommand,
    RefreshInput,
)
from app.core.config import Settings
from app.core.passwords import hash_password
from app.core.rate_limiter import RateLimiter
from app.core.tokens import decode_access_token
from app.db.models.auth import User
from app.domain.auth import (
    CsrfFailedError,
    LoginFailedError,
    LoginLockedError,
    RefreshInvalidError,
    RefreshReuseDetectedError,
)
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
# Cheap argon2 params keep the many login attempts in these tests fast.
_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
_PASSWORD = "correct-horse-battery"


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", Settings().database_url or "")  # type: ignore[call-arg]
    return config


@pytest.fixture(scope="module")
def auth_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(Settings().database_url or "")  # type: ignore[call-arg]
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _seed_active_user(engine: Engine, email: str, *, role: str = "user") -> UUID:
    user_id = uuid4()
    with Session(engine) as session:
        session.add(
            User(
                id=user_id,
                email=email,
                role=role,
                status="active",
                password_hash=hash_password(_HASHER, _PASSWORD),
            )
        )
        session.commit()
    return user_id


def _login(
    engine: Engine,
    settings: Settings,
    email: str,
    password: str,
    *,
    now: datetime = _NOW,
    ip: str | None = None,
) -> IssuedSession:
    with Session(engine) as session:
        cmd = LoginCommand(
            session,
            settings,
            UserRepository(session),
            RefreshTokenRepository(session),
            RateLimiter(session),
            AuditEventWriter(session),
            _HASHER,
        )
        return cmd.execute(LoginInput(email=email, password=password, now=now, ip=ip))


def _refresh(engine: Engine, settings: Settings, inp: RefreshInput) -> IssuedSession:
    with Session(engine) as session:
        cmd = RefreshCommand(
            session,
            settings,
            UserRepository(session),
            RefreshTokenRepository(session),
            AuditEventWriter(session),
        )
        return cmd.execute(inp)


def _logout(engine: Engine, inp: LogoutInput) -> None:
    with Session(engine) as session:
        LogoutCommand(session, RefreshTokenRepository(session), AuditEventWriter(session)).execute(inp)


@pytest.mark.integration
def test_login_success_issues_decodable_session(auth_engine: Engine, settings: Settings) -> None:
    email = f"login-ok-{uuid4()}@example.com"
    user_id = _seed_active_user(auth_engine, email)

    issued = _login(auth_engine, settings, email, _PASSWORD)

    # Inject the same fixed `now` into decode so expiry is deterministic.
    claims = decode_access_token(settings.resolved_jwt_access_secret, issued.access_token, now=_NOW)
    assert claims.sub == user_id
    assert claims.role == "user"
    assert claims.session_id == issued.session_id
    assert issued.refresh_token and issued.csrf_token


@pytest.mark.integration
def test_wrong_password_is_undifferentiated_then_locks(auth_engine: Engine, settings: Settings) -> None:
    email = f"lock-{uuid4()}@example.com"
    _seed_active_user(auth_engine, email)

    # Five wrong attempts each fail generically (no "unknown email" leak)...
    for _ in range(5):
        with pytest.raises(LoginFailedError):
            _login(auth_engine, settings, email, "wrong-password")
    # ...the sixth is locked out rather than merely failed.
    with pytest.raises(LoginLockedError):
        _login(auth_engine, settings, email, "wrong-password")

    # An unknown email also raises LoginFailedError (existence not leaked).
    with pytest.raises(LoginFailedError):
        _login(auth_engine, settings, f"ghost-{uuid4()}@example.com", "whatever")


@pytest.mark.integration
def test_successful_login_refunds_lockout_counter(auth_engine: Engine, settings: Settings) -> None:
    email = f"refund-{uuid4()}@example.com"
    _seed_active_user(auth_engine, email)

    for _ in range(4):
        with pytest.raises(LoginFailedError):
            _login(auth_engine, settings, email, "wrong-password")

    # Correct password just before lockout succeeds and refunds the bucket.
    assert _login(auth_engine, settings, email, _PASSWORD).access_token

    # Because the counter was refunded, the next wrong attempt is a plain failure,
    # not an immediate lockout.
    with pytest.raises(LoginFailedError):
        _login(auth_engine, settings, email, "wrong-password")


@pytest.mark.integration
def test_login_normalizes_email_whitespace_and_case(auth_engine: Engine, settings: Settings) -> None:
    # Seeded clean+lowercase; login with surrounding whitespace + mixed case must still
    # resolve the same user — proving get_by_email and the login bucket share one
    # normalization (citext covers case; normalize_email adds the strip).
    email = f"normalize-{uuid4()}@example.com"
    user_id = _seed_active_user(auth_engine, email)

    issued = _login(auth_engine, settings, f"  {email.upper()}  ", _PASSWORD)

    claims = decode_access_token(settings.resolved_jwt_access_secret, issued.access_token, now=_NOW)
    assert claims.sub == user_id


@pytest.mark.integration
def test_login_ip_throttle_caps_cross_email_stuffing(auth_engine: Engine, settings: Settings) -> None:
    # Credential stuffing: many distinct emails from one IP. The per-email bucket never
    # trips (each email is fresh), so the cap must come from the global per-IP bucket.
    ip = "203.0.113.7"
    for _ in range(20):  # LOGIN_IP_BUCKET_CAPACITY
        with pytest.raises(LoginFailedError):
            _login(auth_engine, settings, f"stuff-{uuid4()}@example.com", "x", ip=ip)
    # The 21st attempt from the same IP is throttled regardless of (a fresh) email.
    with pytest.raises(LoginLockedError):
        _login(auth_engine, settings, f"stuff-{uuid4()}@example.com", "x", ip=ip)
    # A different IP is unaffected — the throttle is per-source, not global-global.
    with pytest.raises(LoginFailedError):
        _login(auth_engine, settings, f"stuff-{uuid4()}@example.com", "x", ip="198.51.100.9")


@pytest.mark.integration
def test_login_ip_throttle_is_not_refunded_by_success(auth_engine: Engine, settings: Settings) -> None:
    # Unlike the email bucket, a successful login must NOT reset the IP budget —
    # otherwise an attacker could interleave one known-good login to keep stuffing.
    ip = "203.0.113.8"
    email = f"ip-refund-{uuid4()}@example.com"
    _seed_active_user(auth_engine, email)

    for _ in range(19):  # burn 19 of 20 IP tokens with fresh unknown emails
        with pytest.raises(LoginFailedError):
            _login(auth_engine, settings, f"stuff-{uuid4()}@example.com", "x", ip=ip)
    # A genuine success consumes the 20th token and refunds only the *email* bucket.
    assert _login(auth_engine, settings, email, _PASSWORD, ip=ip).access_token
    # IP bucket is now empty: the next attempt from this IP is locked, not merely failed.
    with pytest.raises(LoginLockedError):
        _login(auth_engine, settings, f"stuff-{uuid4()}@example.com", "x", ip=ip)


@pytest.mark.integration
def test_refresh_rotates_and_old_token_is_reuse_detected(auth_engine: Engine, settings: Settings) -> None:
    email = f"rotate-{uuid4()}@example.com"
    user_id = _seed_active_user(auth_engine, email)
    issued = _login(auth_engine, settings, email, _PASSWORD)

    rotated = _refresh(
        auth_engine,
        settings,
        RefreshInput(
            raw_refresh_token=issued.refresh_token,
            csrf_cookie=issued.csrf_token,
            csrf_header=issued.csrf_token,
            now=_NOW,
        ),
    )
    assert rotated.refresh_token != issued.refresh_token
    assert decode_access_token(settings.resolved_jwt_access_secret, rotated.access_token, now=_NOW).sub == user_id

    # Replaying the original (now-rotated) token trips reuse detection.
    with pytest.raises(RefreshReuseDetectedError):
        _refresh(
            auth_engine,
            settings,
            RefreshInput(
                raw_refresh_token=issued.refresh_token,
                csrf_cookie=issued.csrf_token,
                csrf_header=issued.csrf_token,
                now=_NOW,
            ),
        )

    # Reuse revoked the whole chain, so the freshly rotated token is dead too.
    with pytest.raises(RefreshInvalidError):
        _refresh(
            auth_engine,
            settings,
            RefreshInput(
                raw_refresh_token=rotated.refresh_token,
                csrf_cookie=rotated.csrf_token,
                csrf_header=rotated.csrf_token,
                now=_NOW,
            ),
        )


@pytest.mark.integration
def test_refresh_requires_matching_csrf(auth_engine: Engine, settings: Settings) -> None:
    email = f"csrf-{uuid4()}@example.com"
    _seed_active_user(auth_engine, email)
    issued = _login(auth_engine, settings, email, _PASSWORD)

    with pytest.raises(CsrfFailedError):
        _refresh(
            auth_engine,
            settings,
            RefreshInput(
                raw_refresh_token=issued.refresh_token,
                csrf_cookie=issued.csrf_token,
                csrf_header="a-different-csrf-value",
                now=_NOW,
            ),
        )


@pytest.mark.integration
def test_logout_revokes_then_refresh_is_invalid(auth_engine: Engine, settings: Settings) -> None:
    email = f"logout-{uuid4()}@example.com"
    _seed_active_user(auth_engine, email)
    issued = _login(auth_engine, settings, email, _PASSWORD)

    _logout(
        auth_engine,
        LogoutInput(
            raw_refresh_token=issued.refresh_token,
            csrf_cookie=issued.csrf_token,
            csrf_header=issued.csrf_token,
            now=_NOW,
        ),
    )

    with pytest.raises(RefreshInvalidError):
        _refresh(
            auth_engine,
            settings,
            RefreshInput(
                raw_refresh_token=issued.refresh_token,
                csrf_cookie=issued.csrf_token,
                csrf_header=issued.csrf_token,
                now=_NOW,
            ),
        )


@pytest.mark.integration
def test_logout_without_cookie_is_noop(auth_engine: Engine) -> None:
    # Idempotent: logging out with no refresh cookie must not raise.
    _logout(auth_engine, LogoutInput(raw_refresh_token=None, csrf_cookie=None, csrf_header=None, now=_NOW))
