"""Password-reset commands.

Request is privacy-preserving: it ALWAYS reports success to the caller (the route
returns 202 regardless) so it never reveals whether an email exists. A token is only
minted for an active account, and email + IP rate limits cap abuse. Confirm validates
the token, applies the password rule, and revokes every existing session.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from app.core.passwords import hash_password, is_password_strong_enough
from app.core.rate_limiter import RateLimiter
from app.core.tokens import generate_url_token, hash_url_token
from app.domain.auth import AccountError, AuthError, PasswordResetInvalidError, WeakPasswordError, normalize_email
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter
from app.services.mailer import Mailer, MailMessage

logger = logging.getLogger(__name__)

RESET_TTL = timedelta(minutes=30)
# Per spec §13: email 3/h, IP 10/h.
_EMAIL_CAPACITY = 3.0
_EMAIL_REFILL_PER_SECOND = 3.0 / 3600.0
_IP_CAPACITY = 10.0
_IP_REFILL_PER_SECOND = 10.0 / 3600.0


@dataclass(frozen=True, slots=True)
class PasswordResetRequestInput:
    email: str
    now: datetime
    ip: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PasswordResetConfirmInput:
    raw_token: str
    new_password: str
    now: datetime
    request_id: str | None = None


class PasswordResetRequestCommand:
    def __init__(
        self,
        db: Session,
        users: UserRepository,
        password_resets: PasswordResetRepository,
        rate_limiter: RateLimiter,
        audit: AuditEventWriter,
        mailer: Mailer,
    ) -> None:
        self._db = db
        self._users = users
        self._password_resets = password_resets
        self._rate_limiter = rate_limiter
        self._audit = audit
        self._mailer = mailer

    def execute(self, inp: PasswordResetRequestInput) -> None:
        """Always succeeds from the caller's view. Side effects (token + mail) happen
        only for an active account within rate limits."""
        try:
            email = normalize_email(inp.email)
            email_ok = self._rate_limiter.consume(
                f"pwreset:email:{email}",
                capacity=_EMAIL_CAPACITY,
                refill_per_second=_EMAIL_REFILL_PER_SECOND,
                now=inp.now,
            ).allowed
            ip_ok = True
            if inp.ip is not None:
                ip_ok = self._rate_limiter.consume(
                    f"pwreset:ip:{inp.ip}",
                    capacity=_IP_CAPACITY,
                    refill_per_second=_IP_REFILL_PER_SECOND,
                    now=inp.now,
                ).allowed

            if email_ok and ip_ok:
                user = self._users.get_by_email(inp.email)
                if user is not None and user.status == "active":
                    raw_token = generate_url_token()
                    self._password_resets.create(
                        user_id=user.id,
                        token_hash=hash_url_token(raw_token),
                        expires_at=inp.now + RESET_TTL,
                        requested_ip=inp.ip,
                    )
                    self._mailer.send(
                        MailMessage(
                            to=inp.email,
                            subject="重設密碼",
                            body=f"請於 30 分鐘內點選連結重設密碼：/reset-password?token={raw_token}",
                        )
                    )
                    self._audit.write(
                        event_type="password_reset_requested",
                        actor_type="user",
                        actor_id=user.id,
                        request_id=inp.request_id,
                        now=inp.now,
                    )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise


class PasswordResetConfirmCommand:
    def __init__(
        self,
        db: Session,
        users: UserRepository,
        password_resets: PasswordResetRepository,
        refresh_tokens: RefreshTokenRepository,
        audit: AuditEventWriter,
        hasher: PasswordHasher,
    ) -> None:
        self._db = db
        self._users = users
        self._password_resets = password_resets
        self._refresh = refresh_tokens
        self._audit = audit
        self._hasher = hasher

    def execute(self, inp: PasswordResetConfirmInput) -> None:
        try:
            reset = self._password_resets.find_by_hash(hash_url_token(inp.raw_token))
            if reset is None or reset.consumed_at is not None or reset.expires_at <= inp.now:
                raise PasswordResetInvalidError()
            if not is_password_strong_enough(inp.new_password):
                raise WeakPasswordError()
            user = self._users.get_by_id(reset.user_id)
            if user is None or user.status != "active":
                raise PasswordResetInvalidError()

            self._users.set_password(
                user.id,
                password_hash=hash_password(self._hasher, inp.new_password),
                now=inp.now,
            )
            self._password_resets.consume(reset.id, now=inp.now)
            revoked = self._refresh.revoke_all_active_for_user(user.id, reason="password_reset", now=inp.now)
            self._audit.write(
                event_type="password_reset_completed",
                actor_type="user",
                actor_id=user.id,
                metadata={"revoked_token_count": revoked},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise
