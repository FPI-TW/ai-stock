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
from app.core.tokens import generate_url_token, hash_url_token
from app.domain.auth import (
    AccountError,
    AuthError,
    EmailAlreadyExistsError,
    InvitationConsumedError,
    InvitationExpiredError,
    InvitationInvalidError,
    TermsNotAcceptedError,
    WeakPasswordError,
)
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter
from app.services.mailer import Mailer, MailMessage

logger = logging.getLogger(__name__)

INVITATION_TTL = timedelta(hours=24)


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
    terms_version: str
    now: datetime
    user_agent: str | None = None
    ip: str | None = None
    request_id: str | None = None


def _send_invitation_mail(mailer: Mailer, email: str, raw_token: str) -> None:
    # The real Mailer (P3) builds the absolute link; the stub just needs the token.
    mailer.send(
        MailMessage(
            to=email,
            subject="您的帳號邀請",
            body=f"請於 24 小時內點選連結設定密碼並啟用帳號：/accept-invitation?token={raw_token}",
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
    ) -> None:
        self._db = db
        self._users = users
        self._invitations = invitations
        self._audit = audit
        self._mailer = mailer

    def execute(self, inp: CreateUserInput) -> CreatedUser:
        try:
            if self._users.get_by_email(inp.email) is not None:
                raise EmailAlreadyExistsError()

            user_id = self._users.create_invited(email=inp.email, role=inp.role)
            raw_token = generate_url_token()
            self._invitations.create(
                user_id=user_id,
                token_hash=hash_url_token(raw_token),
                expires_at=inp.now + INVITATION_TTL,
                created_by_admin_id=inp.created_by_admin_id,
            )
            _send_invitation_mail(self._mailer, inp.email, raw_token)
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
    """Invitee consumes the invitation: set first password, accept terms, activate,
    and receive a logged-in session."""

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
            if not inp.terms_version.strip():
                raise TermsNotAcceptedError()

            user = self._users.get_by_id(invitation.user_id)
            if user is None or user.status != "invited":
                raise InvitationInvalidError()

            self._users.activate(
                user.id,
                password_hash=hash_password(self._hasher, inp.password),
                terms_version=inp.terms_version,
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
