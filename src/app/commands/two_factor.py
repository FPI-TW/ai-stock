"""Admin TOTP 2FA: setup (provision a secret) and verify (confirm a code, elevate
the session to mfa_verified).

The setup/verify endpoints are reachable by an authenticated admin WITHOUT a verified
factor (otherwise enrolment would be impossible) — they are gated by admin-role only,
not the full admin gate. Verify marks the session's refresh token and re-mints an
access token carrying mfa_verified=True.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.mfa_crypto import decrypt_secret, encrypt_secret
from app.core.rate_limiter import RateLimiter
from app.core.tokens import encode_access_token
from app.core.totp import generate_totp_secret, totp_provisioning_uri, verify_totp
from app.domain.auth import (
    AccountError,
    AuthError,
    ForbiddenError,
    MfaAlreadyEnabledError,
    MfaInvalidCodeError,
    MfaNotSetupError,
    RateLimitedError,
)
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter

logger = logging.getLogger(__name__)

_TOTP_ISSUER = "ai-stock"

# 2FA verify lockout: 5 wrong codes, then ~15 min to recover one attempt. Mirrors the
# login lockout (spec §13) so a stolen first factor can't brute-force the 6-digit TOTP.
_VERIFY_2FA_CAPACITY = 5.0
_VERIFY_2FA_REFILL_PER_SECOND = 1.0 / 900.0


@dataclass(frozen=True, slots=True)
class SetupTwoFactorInput:
    admin_user_id: UUID
    now: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SetupTwoFactorResult:
    provisioning_uri: str
    secret: str


@dataclass(frozen=True, slots=True)
class VerifyTwoFactorInput:
    admin_user_id: UUID
    session_id: UUID
    code: str
    now: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerifyTwoFactorResult:
    access_token: str
    expires_in: int
    role: str


class SetupTwoFactorCommand:
    def __init__(self, db: Session, settings: Settings, users: UserRepository, audit: AuditEventWriter) -> None:
        self._db = db
        self._settings = settings
        self._users = users
        self._audit = audit

    def execute(self, inp: SetupTwoFactorInput) -> SetupTwoFactorResult:
        try:
            user = self._users.get_by_id(inp.admin_user_id)
            if user is None or user.role != "admin":
                raise ForbiddenError()
            if user.mfa_enabled:
                raise MfaAlreadyEnabledError()

            secret = generate_totp_secret()
            encrypted = encrypt_secret(self._settings.resolved_mfa_encryption_key, secret)
            self._users.set_mfa_secret(user.id, encrypted_secret=encrypted, now=inp.now)
            self._db.commit()
            return SetupTwoFactorResult(
                provisioning_uri=totp_provisioning_uri(secret, account_name=user.email, issuer=_TOTP_ISSUER),
                secret=secret,
            )
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise


class VerifyTwoFactorCommand:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        rate_limiter: RateLimiter,
        audit: AuditEventWriter,
    ) -> None:
        self._db = db
        self._settings = settings
        self._users = users
        self._refresh = refresh_tokens
        self._rate_limiter = rate_limiter
        self._audit = audit

    def execute(self, inp: VerifyTwoFactorInput) -> VerifyTwoFactorResult:
        bucket = f"2fa_verify:{inp.admin_user_id}"
        try:
            user = self._users.get_by_id(inp.admin_user_id)
            if user is None or user.role != "admin":
                raise ForbiddenError()

            # Throttle code attempts per admin before touching the secret, so a stolen
            # first factor can't brute-force the TOTP. Like login, the failure paths
            # commit-then-raise so the consumed token persists (else rollback would
            # refund every wrong guess and the lockout would never accumulate).
            decision = self._rate_limiter.consume(
                bucket,
                capacity=_VERIFY_2FA_CAPACITY,
                refill_per_second=_VERIFY_2FA_REFILL_PER_SECOND,
                now=inp.now,
            )
            if not decision.allowed:
                self._audit.write(
                    event_type="admin_2fa_verify_locked",
                    actor_type="admin",
                    actor_id=user.id,
                    request_id=inp.request_id,
                    now=inp.now,
                )
                self._db.commit()
                raise RateLimitedError(decision.retry_after_seconds)

            encrypted = self._users.get_mfa_secret_encrypted(user.id)
            if encrypted is None:
                raise MfaNotSetupError()
            secret = decrypt_secret(self._settings.resolved_mfa_encryption_key, encrypted)
            if not verify_totp(secret, inp.code):
                self._db.commit()  # persist the consumed attempt so the lockout counts it
                raise MfaInvalidCodeError()

            # Success: refund the lockout counter so a verified admin starts fresh.
            self._rate_limiter.reset(bucket)
            if not user.mfa_enabled:
                # First successful verify completes enrolment.
                self._users.enable_mfa(user.id, now=inp.now)
                self._audit.write(
                    event_type="admin_2fa_enabled",
                    actor_type="admin",
                    actor_id=user.id,
                    request_id=inp.request_id,
                    now=inp.now,
                )

            # Elevate the current session and re-mint an access token that says so.
            self._refresh.mark_mfa_verified(inp.session_id)
            access_ttl = self._settings.access_token_ttl_admin_seconds
            access_token = encode_access_token(
                self._settings.resolved_jwt_access_secret,
                sub=user.id,
                role=user.role,
                session_id=inp.session_id,
                ttl_seconds=access_ttl,
                now=inp.now,
                mfa_verified=True,
            )
            self._audit.write(
                event_type="admin_2fa_verified",
                actor_type="admin",
                actor_id=user.id,
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            return VerifyTwoFactorResult(access_token=access_token, expires_in=access_ttl, role=user.role)
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise
