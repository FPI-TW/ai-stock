"""Account-lifecycle commands: admin creates an invited user, and the invitee
accepts the invitation (sets first password) to activate + log in.

Each command owns its transaction so the user row + invitation row + audit land
atomically. The invitation token is shown only via the Mailer; only its hash is
stored.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from app.commands.auth import IssuedSession, issue_session
from app.core.config import Settings
from app.core.passwords import hash_password, is_password_strong_enough
from app.core.rate_limiter import RateLimiter
from app.core.tokens import generate_url_token, hash_url_token
from app.domain.auth import (
    AccountError,
    AccountNotDisabledError,
    AuthError,
    EmailAlreadyExistsError,
    InvitationConsumedError,
    InvitationExpiredError,
    InvitationInvalidError,
    RateLimitedError,
    UserNotFoundError,
    WeakPasswordError,
    normalize_email,
)
from app.repositories.intent_repository import IntentRepository
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter
from app.services.mailer import Mailer, MailMessage

logger = logging.getLogger(__name__)

INVITATION_TTL = timedelta(hours=24)
ACCOUNT_DISABLED_INTENT_STATUS = "cancelled_by_account_disabled"
# Invitation resend throttle: 3 per hour per invited user.
_RESEND_CAPACITY = 3.0
_RESEND_REFILL_PER_SECOND = 3.0 / 3600.0


@dataclass(frozen=True, slots=True)
class CreateUserInput:
    email: str
    role: str
    created_by_admin_id: UUID
    now: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedUser:
    user_id: UUID


@dataclass(frozen=True, slots=True)
class AcceptInvitationInput:
    raw_token: str
    password: str
    now: datetime
    user_agent: str | None = None
    ip: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class DisableUserInput:
    target_user_id: UUID
    actor_admin_id: UUID
    now: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResendInvitationInput:
    target_user_id: UUID
    actor_admin_id: UUID
    now: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReactivateUserInput:
    target_user_id: UUID
    actor_admin_id: UUID
    now: datetime
    request_id: str | None = None


def _send_invitation_mail(mailer: Mailer, email: str, raw_token: str, base_url: str) -> None:
    # 刻意不吞例外：寄信失敗會讓呼叫端交易 rollback（不留孤兒帳號/邀請），admin 收到
    # 500 後重試即可。與 password_reset 相反——那條為隱私契約必須吞掉失敗仍回 202，
    # 這條是 admin-facing、非隱私路徑，寄不出去就該讓 admin 知道並重試。
    mailer.send(
        MailMessage(
            to=email,
            subject="您的帳號邀請",
            body=f"請於 24 小時內點選連結設定密碼並啟用帳號：{base_url}/accept-invitation?token={raw_token}",
        )
    )


class CreateUserCommand:
    """Admin creates an invited user and sends them an invitation."""

    def __init__(
        self,
        db: Session,
        users: UserRepository,
        invitations: InvitationRepository,
        audit: AuditEventWriter,
        mailer: Mailer,
        base_url: str,
    ) -> None:
        self._db = db
        self._users = users
        self._invitations = invitations
        self._audit = audit
        self._mailer = mailer
        self._base_url = base_url

    def execute(self, inp: CreateUserInput) -> CreatedUser:
        try:
            # 正規化一次、全程共用：DB 存的與信寄到的必須是同一個字串，
            # 否則 " User@Example.com " 會存成 user@example.com 但信寄給原始輸入。
            email = normalize_email(inp.email)
            if self._users.get_by_email(email) is not None:
                raise EmailAlreadyExistsError()

            user_id = self._users.create_invited(email=email, role=inp.role)
            raw_token = generate_url_token()
            self._invitations.create(
                user_id=user_id,
                token_hash=hash_url_token(raw_token),
                expires_at=inp.now + INVITATION_TTL,
                created_by_admin_id=inp.created_by_admin_id,
            )
            _send_invitation_mail(self._mailer, email, raw_token, self._base_url)
            self._audit.write(
                event_type="account_invited",
                actor_type="admin",
                actor_id=inp.created_by_admin_id,
                metadata={"invited_user_id": str(user_id), "role": inp.role},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            return CreatedUser(user_id=user_id)
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise


class AcceptInvitationCommand:
    """Invitee consumes the invitation: set first password, activate, and receive a
    logged-in session."""

    def __init__(
        self,
        db: Session,
        settings: Settings,
        users: UserRepository,
        invitations: InvitationRepository,
        refresh_tokens: RefreshTokenRepository,
        audit: AuditEventWriter,
        hasher: PasswordHasher,
    ) -> None:
        self._db = db
        self._settings = settings
        self._users = users
        self._invitations = invitations
        self._refresh = refresh_tokens
        self._audit = audit
        self._hasher = hasher

    def execute(self, inp: AcceptInvitationInput) -> IssuedSession:
        try:
            invitation = self._invitations.find_by_hash(hash_url_token(inp.raw_token))
            if invitation is None or invitation.revoked_at is not None:
                raise InvitationInvalidError()
            if invitation.consumed_at is not None:
                raise InvitationConsumedError()
            if invitation.expires_at <= inp.now:
                raise InvitationExpiredError()

            if not is_password_strong_enough(inp.password):
                raise WeakPasswordError()

            user = self._users.get_by_id(invitation.user_id)
            if user is None or user.status != "invited":
                raise InvitationInvalidError()

            self._users.activate(
                user.id,
                password_hash=hash_password(self._hasher, inp.password),
                now=inp.now,
            )
            self._invitations.consume(invitation.id, now=inp.now)
            issued = issue_session(
                self._refresh,
                self._settings,
                user=user,
                now=inp.now,
                parent_token_id=None,
                user_agent=inp.user_agent,
                ip=inp.ip,
            )
            self._audit.write(
                event_type="account_activated",
                actor_type=user.role,
                actor_id=user.id,
                metadata={"session_id": str(issued.session_id)},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            return issued
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise


class DisableUserCommand:
    """Admin disables a user and cascades: cancel their open intents, revoke every
    refresh token, flip status to disabled. (Notification-delivery stop is P2.)"""

    def __init__(
        self,
        db: Session,
        users: UserRepository,
        intents: IntentRepository,
        refresh_tokens: RefreshTokenRepository,
        audit: AuditEventWriter,
    ) -> None:
        self._db = db
        self._users = users
        self._intents = intents
        self._refresh = refresh_tokens
        self._audit = audit

    def execute(self, inp: DisableUserInput) -> None:
        try:
            user = self._users.get_by_id(inp.target_user_id)
            if user is None:
                raise UserNotFoundError()

            self._users.disable(user.id, now=inp.now)
            cancelled = self._intents.cancel_active_for_owner(
                user.id, status=ACCOUNT_DISABLED_INTENT_STATUS, now=inp.now
            )
            revoked = self._refresh.revoke_all_active_for_user(user.id, reason="account_disabled", now=inp.now)
            self._audit.write(
                event_type="account_disabled",
                actor_type="admin",
                actor_id=inp.actor_admin_id,
                metadata={
                    "target_user_id": str(user.id),
                    "cancelled_intent_count": cancelled,
                    "revoked_token_count": revoked,
                },
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise


class ReactivateUserCommand:
    """Admin re-enables a disabled user: flip status back to active. Old intents are
    NOT restored (spec §13) and revoked sessions stay revoked — the user logs in afresh.
    Only a currently-disabled account can be reactivated."""

    def __init__(
        self,
        db: Session,
        users: UserRepository,
        audit: AuditEventWriter,
    ) -> None:
        self._db = db
        self._users = users
        self._audit = audit

    def execute(self, inp: ReactivateUserInput) -> None:
        try:
            user = self._users.get_by_id(inp.target_user_id)
            if user is None:
                raise UserNotFoundError()
            if user.status != "disabled":
                raise AccountNotDisabledError()

            self._users.reactivate(user.id, now=inp.now)
            self._audit.write(
                event_type="account_reactivated",
                actor_type="admin",
                actor_id=inp.actor_admin_id,
                metadata={"target_user_id": str(user.id)},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise


class ResendInvitationCommand:
    """Admin re-sends an invitation: revoke the live one, mint + mail a fresh token.
    Throttled per invited user."""

    def __init__(
        self,
        db: Session,
        users: UserRepository,
        invitations: InvitationRepository,
        rate_limiter: RateLimiter,
        audit: AuditEventWriter,
        mailer: Mailer,
        base_url: str,
    ) -> None:
        self._db = db
        self._users = users
        self._invitations = invitations
        self._rate_limiter = rate_limiter
        self._audit = audit
        self._mailer = mailer
        self._base_url = base_url

    def execute(self, inp: ResendInvitationInput) -> None:
        try:
            user = self._users.get_by_id(inp.target_user_id)
            if user is None:
                raise UserNotFoundError()
            if user.status != "invited":
                # Only a still-pending invitation can be resent.
                raise InvitationInvalidError()

            decision = self._rate_limiter.consume(
                f"invitation_resend:{user.id}",
                capacity=_RESEND_CAPACITY,
                refill_per_second=_RESEND_REFILL_PER_SECOND,
                now=inp.now,
            )
            if not decision.allowed:
                self._db.commit()  # persist the consumed bucket
                raise RateLimitedError(decision.retry_after_seconds)

            self._invitations.revoke_active_for_user(user.id, now=inp.now)
            raw_token = generate_url_token()
            self._invitations.create(
                user_id=user.id,
                token_hash=hash_url_token(raw_token),
                expires_at=inp.now + INVITATION_TTL,
                created_by_admin_id=inp.actor_admin_id,
            )
            _send_invitation_mail(self._mailer, user.email, raw_token, self._base_url)
            self._audit.write(
                event_type="invitation_resent",
                actor_type="admin",
                actor_id=inp.actor_admin_id,
                metadata={"target_user_id": str(user.id)},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
        except (AuthError, AccountError):
            raise
        except Exception:
            self._db.rollback()
            raise
