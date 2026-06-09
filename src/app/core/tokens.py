"""Access JWT (HS256) and refresh-token primitives.

Access tokens are short-lived, stateless, and carry no PII (profile is fetched via
/auth/me). Refresh tokens are opaque 32-byte randoms; only their SHA-256 hash is
ever persisted, so a DB leak never exposes a usable token. `now` is injected so the
logic is deterministic under test.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

_ALGORITHM = "HS256"


class InvalidAccessTokenError(Exception):
    """Raised when an access token is malformed, tampered with, or expired."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    sub: UUID
    role: str
    session_id: UUID
    mfa_verified: bool
    issued_at: datetime
    expires_at: datetime


def encode_access_token(
    secret: str,
    *,
    sub: UUID,
    role: str,
    session_id: UUID,
    ttl_seconds: int,
    now: datetime,
    mfa_verified: bool = False,
) -> str:
    expires_at = now + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": str(sub),
        "role": role,
        "session_id": str(session_id),
        "mfa_verified": mfa_verified,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=_ALGORITHM)


def decode_access_token(secret: str, token: str, *, now: datetime | None = None) -> AccessTokenClaims:
    """Verify signature and expiry, returning the claims.

    Signature is always checked. Expiry is checked against `now` when provided
    (deterministic — used by tests); otherwise PyJWT validates `exp` against the
    real system clock (production default).
    """
    try:
        # When `now` is supplied we own the expiry check, so disable PyJWT's.
        payload = jwt.decode(token, secret, algorithms=[_ALGORITHM], options={"verify_exp": now is None})
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc
    try:
        claims = AccessTokenClaims(
            sub=UUID(payload["sub"]),
            role=payload["role"],
            session_id=UUID(payload["session_id"]),
            mfa_verified=bool(payload.get("mfa_verified", False)),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValueError) as exc:
        raise InvalidAccessTokenError(f"malformed access token claims: {exc}") from exc
    if now is not None and claims.expires_at <= now:
        raise InvalidAccessTokenError("access token expired")
    return claims


def generate_refresh_token() -> str:
    """A fresh opaque refresh token. Shown to the client ONCE; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hex digest of a refresh token, for storage and lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_url_token() -> str:
    """Opaque token embedded in an invitation / password-reset link. Shown once;
    only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_url_token(token: str) -> str:
    """SHA-256 hex digest of an invitation / password-reset token, for storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    """Random value for the double-submit CSRF cookie. Not stored server-side: the
    cookie copy and the X-CSRF-Token header copy are compared against each other."""
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_value: str | None, header_value: str | None) -> bool:
    """Constant-time double-submit check. Both sides must be present and equal."""
    if not cookie_value or not header_value:
        return False
    return secrets.compare_digest(cookie_value, header_value)
