from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.tokens import (
    InvalidAccessTokenError,
    decode_access_token,
    encode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

# >= 32 bytes per spec §13 / RFC 7518 (HS256 minimum key length).
_SECRET = "unit-test-secret-0123456789-abcdefghijklmnop"


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_access_token_roundtrip_preserves_claims() -> None:
    sub = uuid4()
    session_id = uuid4()

    token = encode_access_token(
        _SECRET,
        sub=sub,
        role="admin",
        session_id=session_id,
        ttl_seconds=900,
        now=_T0,
        mfa_verified=True,
    )
    # Injected `now` makes expiry deterministic regardless of wall-clock.
    claims = decode_access_token(_SECRET, token, now=_T0)

    assert claims.sub == sub
    assert claims.role == "admin"
    assert claims.session_id == session_id
    assert claims.mfa_verified is True
    assert claims.expires_at > claims.issued_at


def test_access_token_defaults_mfa_verified_false() -> None:
    token = encode_access_token(_SECRET, sub=uuid4(), role="user", session_id=uuid4(), ttl_seconds=900, now=_T0)
    assert decode_access_token(_SECRET, token, now=_T0).mfa_verified is False


def test_token_valid_just_before_expiry_and_rejected_after() -> None:
    token = encode_access_token(_SECRET, sub=uuid4(), role="user", session_id=uuid4(), ttl_seconds=60, now=_T0)

    # One second before expiry: still valid.
    assert decode_access_token(_SECRET, token, now=_T0 + timedelta(seconds=59)) is not None
    # One second after expiry: rejected, deterministically.
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(_SECRET, token, now=_T0 + timedelta(seconds=61))


def test_wrong_secret_is_rejected() -> None:
    token = encode_access_token(
        _SECRET, sub=uuid4(), role="user", session_id=uuid4(), ttl_seconds=900, now=datetime.now(UTC)
    )
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token("a-different-secret-0123456789-abcdefghij", token)


def test_tampered_access_token_is_rejected() -> None:
    token = encode_access_token(
        _SECRET, sub=uuid4(), role="user", session_id=uuid4(), ttl_seconds=900, now=datetime.now(UTC)
    )
    tampered = token[:-3] + ("aaa" if token[-3:] != "aaa" else "bbb")
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(_SECRET, tampered)


def test_refresh_token_is_unique_and_hash_is_stable() -> None:
    first = generate_refresh_token()
    second = generate_refresh_token()

    assert first != second
    assert hash_refresh_token(first) == hash_refresh_token(first)
    assert hash_refresh_token(first) != hash_refresh_token(second)
    # The stored hash must never equal the raw token.
    assert hash_refresh_token(first) != first
