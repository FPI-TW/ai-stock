"""TOTP (RFC 6238) primitives for admin two-factor auth.

Thin wrapper over pyotp. The secret is generated here and must be stored encrypted
(users.mfa_secret_encrypted); this module never persists anything. A small
verification window tolerates clock skew between server and authenticator app.
"""

import pyotp

_DEFAULT_VALID_WINDOW = 1


def generate_totp_secret() -> str:
    """A fresh base32 TOTP secret to provision into an authenticator app."""
    return pyotp.random_base32()


def verify_totp(secret: str, code: str, *, valid_window: int = _DEFAULT_VALID_WINDOW) -> bool:
    """Return True iff `code` is valid for `secret` now (±valid_window steps)."""
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)


def totp_provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """otpauth:// URI for rendering the enrolment QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)
