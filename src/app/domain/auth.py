"""Auth domain: the user projection and the error vocabulary for the L1 flow.

Errors here are transport-agnostic; `app.api.errors` maps each to a canonical
ErrorCode + HTTP status. Login never distinguishes "no such email" from "wrong
password" (both -> LoginFailedError) so existence is not leaked.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


def normalize_email(email: str) -> str:
    """Canonical email key: strip surrounding whitespace + lowercase. Used on every
    read/write of users.email AND for the per-email rate-limit bucket keys, so the
    bucket key and the DB lookup can never diverge (the column is citext, i.e.
    case-insensitive, but it does NOT strip whitespace)."""
    return email.strip().lower()


@dataclass(frozen=True, slots=True)
class UserData:
    id: UUID
    email: str
    role: str
    status: str
    mfa_enabled: bool
    password_hash: str | None
    terms_version_accepted: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshTokenData:
    id: UUID
    user_id: UUID
    token_hash: str
    parent_token_id: UUID | None
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    mfa_verified: bool


@dataclass(frozen=True, slots=True)
class InvitationData:
    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class PasswordResetData:
    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None


class AuthError(Exception):
    """Base class for L1 auth failures."""


class UnauthenticatedError(AuthError):
    """No usable credential was presented (missing / malformed / expired access token)."""


class ForbiddenError(AuthError):
    """Authenticated but not allowed (role / ownership / mfa gate)."""


class LoginFailedError(AuthError):
    """Bad email or password, or a non-active account. Deliberately undifferentiated."""


class LoginLockedError(AuthError):
    """Too many failed logins for this email; locked until the bucket refills."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("login temporarily locked")
        self.retry_after_seconds = retry_after_seconds


class RefreshInvalidError(AuthError):
    """Refresh token is unknown, revoked (non-reuse), or expired."""


class RefreshReuseDetectedError(AuthError):
    """An already-rotated refresh token was replayed; the whole chain is revoked."""


class CsrfFailedError(AuthError):
    """Double-submit CSRF token missing or mismatched on a cookie-authenticated call."""


class MfaRequiredError(ForbiddenError):
    """Admin endpoint reached without a verified second factor."""


class RateLimitedError(AuthError):
    """A throttled action (e.g. invitation resend) exceeded its bucket."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = retry_after_seconds


class MfaInvalidCodeError(AuthError):
    """The supplied TOTP code did not validate."""


class AccountError(Exception):
    """Base class for account-lifecycle failures (invitation / activation)."""


class EmailAlreadyExistsError(AccountError):
    """Admin tried to create a user whose email already exists."""


class UserNotFoundError(AccountError):
    """Admin referenced a user id that does not exist."""


class InvitationInvalidError(AccountError):
    """Invitation token is unknown or revoked."""


class InvitationExpiredError(AccountError):
    """Invitation token is past its 24h expiry."""


class InvitationConsumedError(AccountError):
    """Invitation token was already used to activate the account."""


class WeakPasswordError(AccountError):
    """Password fails the MVP length rule (spec §13: >= 8 chars)."""


class TermsNotAcceptedError(AccountError):
    """Activation attempted without accepting the required terms version."""


class PasswordResetInvalidError(AccountError):
    """Reset token is unknown, already used, or expired. Deliberately undifferentiated
    so the confirm step never reveals which."""


class MfaAlreadyEnabledError(AccountError):
    """2FA setup attempted on an account that already has it enabled (use reset)."""


class MfaNotSetupError(AccountError):
    """2FA verify attempted before any secret was provisioned."""


class AccountNotDisabledError(AccountError):
    """Reactivation attempted on a user that is not currently disabled."""
