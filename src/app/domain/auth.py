"""Auth domain: the user projection and the error vocabulary for the L1 flow.

Errors here are transport-agnostic; `app.api.errors` maps each to a canonical
ErrorCode + HTTP status. Login never distinguishes "no such email" from "wrong
password" (both -> LoginFailedError) so existence is not leaked.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


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
