"""Auth commands: login, refresh-rotation, logout.

Each command owns its transaction. Note the deliberate commit-then-raise on the
failure paths: a failed login must still persist the consumed rate-limit token and
the audit row, otherwise the lockout counter would never accumulate. Genuine
(non-auth) errors roll back.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.passwords import verify_password
from app.core.rate_limiter import RateLimiter
from app.core.tokens import (
    csrf_tokens_match,
    encode_access_token,
    generate_csrf_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.domain.auth import (
    AuthError,
    CsrfFailedError,
    LoginFailedError,
    LoginLockedError,
    RefreshInvalidError,
    RefreshReuseDetectedError,
    UserData,
)
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter

logger = logging.getLogger(__name__)

# Login lockout: 5 attempts, then ~15 min to recover one attempt (spec §13).
LOGIN_BUCKET_CAPACITY = 5.0
LOGIN_BUCKET_REFILL_PER_SECOND = 1.0 / 900.0


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    refresh_token: str  # raw value — set as cookie, shown to the client once
    csrf_token: str  # raw value — set as the non-HttpOnly CSRF cookie
    role: str
    session_id: UUID
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginInput:
    email: str
    password: str
    now: datetime
    user_agent: str | None = None
    ip: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshInput:
    raw_refresh_token: str | None
    csrf_cookie: str | None
    csrf_header: str | None
    now: datetime
    user_agent: str | None = None
    ip: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class LogoutInput:
    raw_refresh_token: str | None
    csrf_cookie: str | None
    csrf_header: str | None
    now: datetime
    request_id: str | None = None


def _access_ttl_seconds(settings: Settings, role: str) -> int:
    if role == "admin":
        return settings.access_token_ttl_admin_seconds
    return settings.access_token_ttl_user_seconds


def _refresh_ttl_seconds(settings: Settings, role: str) -> int:
    if role == "admin":
        return settings.refresh_token_ttl_admin_seconds
    return settings.refresh_token_ttl_user_seconds


def _issue_session(
    refresh_repo: RefreshTokenRepository,
    settings: Settings,
    *,
    user: UserData,
    now: datetime,
    parent_token_id: UUID | None,
    user_agent: str | None,
    ip: str | None,
) -> IssuedSession:
    """Mint a refresh token (stored as hash) + a stateless access JWT + a CSRF token.

    mfa_verified is always False at issuance in V1: admins gain a verified factor
    only through the dedicated 2FA verify flow (Phase 4); refresh never elevates it.
    """
    refresh_ttl = _refresh_ttl_seconds(settings, user.role)
    refresh_expires_at = now + timedelta(seconds=refresh_ttl)
    raw_refresh = generate_refresh_token()
    token_id = refresh_repo.create(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=refresh_expires_at,
        parent_token_id=parent_token_id,
        user_agent=user_agent,
        ip=ip,
    )

    access_ttl = _access_ttl_seconds(settings, user.role)
    access_token = encode_access_token(
        settings.resolved_jwt_access_secret,
        sub=user.id,
        role=user.role,
        session_id=token_id,
        ttl_seconds=access_ttl,
        now=now,
        mfa_verified=False,
    )
    return IssuedSession(
        access_token=access_token,
        refresh_token=raw_refresh,
        csrf_token=generate_csrf_token(),
        role=user.role,
        session_id=token_id,
        access_expires_at=now + timedelta(seconds=access_ttl),
        refresh_expires_at=refresh_expires_at,
    )


class LoginCommand:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        rate_limiter: RateLimiter,
        audit: AuditEventWriter,
        hasher: PasswordHasher,
    ) -> None:
        self._db = db
        self._settings = settings
        self._users = users
        self._refresh = refresh_tokens
        self._rate_limiter = rate_limiter
        self._audit = audit
        self._hasher = hasher

    def execute(self, inp: LoginInput) -> IssuedSession:
        bucket = f"login:{inp.email.strip().lower()}"
        try:
            decision = self._rate_limiter.consume(
                bucket,
                capacity=LOGIN_BUCKET_CAPACITY,
                refill_per_second=LOGIN_BUCKET_REFILL_PER_SECOND,
                now=inp.now,
            )
            if not decision.allowed:
                self._audit.write(
                    event_type="login_failed",
                    actor_type="user",
                    metadata={"reason": "locked"},
                    request_id=inp.request_id,
                    now=inp.now,
                )
                self._db.commit()
                raise LoginLockedError(decision.retry_after_seconds)

            user = self._users.get_by_email(inp.email)
            authenticated = (
                user is not None
                and user.status == "active"
                and user.password_hash is not None
                and verify_password(self._hasher, user.password_hash, inp.password)
            )
            if not authenticated or user is None:
                self._audit.write(
                    event_type="login_failed",
                    actor_type="user",
                    actor_id=user.id if user is not None else None,
                    metadata={"reason": "bad_credentials"},
                    request_id=inp.request_id,
                    now=inp.now,
                )
                self._db.commit()
                raise LoginFailedError()

            # Success: refund the lockout counter, mint a session, audit.
            self._rate_limiter.reset(bucket)
            issued = _issue_session(
                self._refresh,
                self._settings,
                user=user,
                now=inp.now,
                parent_token_id=None,
                user_agent=inp.user_agent,
                ip=inp.ip,
            )
            self._audit.write(
                event_type="login_success",
                actor_type=user.role,
                actor_id=user.id,
                metadata={"session_id": str(issued.session_id)},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            return issued
        except AuthError:
            raise
        except Exception:
            self._db.rollback()
            raise


class RefreshCommand:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        audit: AuditEventWriter,
    ) -> None:
        self._db = db
        self._settings = settings
        self._users = users
        self._refresh = refresh_tokens
        self._audit = audit

    def execute(self, inp: RefreshInput) -> IssuedSession:
        try:
            if not csrf_tokens_match(inp.csrf_cookie, inp.csrf_header):
                raise CsrfFailedError()
            if inp.raw_refresh_token is None:
                raise RefreshInvalidError()

            token = self._refresh.find_by_hash(hash_refresh_token(inp.raw_refresh_token))
            if token is None:
                raise RefreshInvalidError()

            if token.revoked_at is not None:
                if token.revoked_reason == "rotated":
                    # A token that was already rotated is being replayed: treat the
                    # whole chain as compromised and revoke every live token.
                    revoked = self._refresh.revoke_all_active_for_user(
                        token.user_id, reason="reuse_detected", now=inp.now
                    )
                    self._audit.write(
                        event_type="refresh_reuse_detected",
                        actor_type="user",
                        actor_id=token.user_id,
                        metadata={"revoked_token_count": revoked},
                        request_id=inp.request_id,
                        now=inp.now,
                    )
                    self._db.commit()
                    logger.warning(
                        "refresh token reuse detected; revoked chain",
                        extra={"user_id": str(token.user_id), "revoked_token_count": revoked},
                    )
                    raise RefreshReuseDetectedError()
                raise RefreshInvalidError()

            if token.expires_at <= inp.now:
                raise RefreshInvalidError()

            user = self._users.get_by_id(token.user_id)
            if user is None or user.status != "active":
                raise RefreshInvalidError()

            # Rotate: revoke the presented token and mint its successor.
            self._refresh.revoke(token.id, reason="rotated", now=inp.now)
            issued = _issue_session(
                self._refresh,
                self._settings,
                user=user,
                now=inp.now,
                parent_token_id=token.id,
                user_agent=inp.user_agent,
                ip=inp.ip,
            )
            self._audit.write(
                event_type="session_refreshed",
                actor_type=user.role,
                actor_id=user.id,
                metadata={"session_id": str(issued.session_id)},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            return issued
        except AuthError:
            raise
        except Exception:
            self._db.rollback()
            raise


class LogoutCommand:
    def __init__(
        self,
        db: Session,
        refresh_tokens: RefreshTokenRepository,
        audit: AuditEventWriter,
    ) -> None:
        self._db = db
        self._refresh = refresh_tokens
        self._audit = audit

    def execute(self, inp: LogoutInput) -> None:
        """Revoke the current refresh token. Idempotent: no cookie -> no-op success."""
        try:
            if inp.raw_refresh_token is None:
                return
            if not csrf_tokens_match(inp.csrf_cookie, inp.csrf_header):
                raise CsrfFailedError()

            token = self._refresh.find_by_hash(hash_refresh_token(inp.raw_refresh_token))
            if token is not None and token.revoked_at is None:
                self._refresh.revoke(token.id, reason="logout", now=inp.now)
                self._audit.write(
                    event_type="logout",
                    actor_type="user",
                    actor_id=token.user_id,
                    metadata={"session_id": str(token.id)},
                    request_id=inp.request_id,
                    now=inp.now,
                )
            self._db.commit()
        except AuthError:
            raise
        except Exception:
            self._db.rollback()
            raise
