import pyotp

from app.core.totp import generate_totp_secret, totp_provisioning_uri, verify_totp


def test_generated_secret_verifies_current_code() -> None:
    secret = generate_totp_secret()
    current_code = pyotp.TOTP(secret).now()

    assert verify_totp(secret, current_code) is True


def test_malformed_code_is_rejected() -> None:
    secret = generate_totp_secret()
    # Non-numeric can never equal a TOTP value, so this is deterministically wrong.
    assert verify_totp(secret, "bad-code") is False


def test_provisioning_uri_contains_issuer() -> None:
    secret = generate_totp_secret()
    uri = totp_provisioning_uri(secret, account_name="admin@example.com", issuer="ai-stock")

    assert uri.startswith("otpauth://totp/")
    assert "issuer=ai-stock" in uri
