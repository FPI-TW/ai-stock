from app.core.config import Settings
from app.core.passwords import (
    MIN_PASSWORD_LENGTH,
    build_password_hasher,
    hash_password,
    is_password_strong_enough,
    verify_password,
)


def test_hash_is_not_plaintext_and_verifies() -> None:
    hasher = build_password_hasher(Settings())  # type: ignore[call-arg]
    hashed = hash_password(hasher, "correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert hashed.startswith("$argon2id$")
    assert verify_password(hasher, hashed, "correct horse battery staple") is True


def test_verify_rejects_wrong_password() -> None:
    hasher = build_password_hasher(Settings())  # type: ignore[call-arg]
    hashed = hash_password(hasher, "right-password")

    assert verify_password(hasher, hashed, "wrong-password") is False


def test_verify_rejects_malformed_hash_without_raising() -> None:
    hasher = build_password_hasher(Settings())  # type: ignore[call-arg]

    assert verify_password(hasher, "not-a-real-hash", "whatever") is False


def test_password_strength_boundary() -> None:
    assert is_password_strong_enough("a" * MIN_PASSWORD_LENGTH) is True
    assert is_password_strong_enough("a" * (MIN_PASSWORD_LENGTH - 1)) is False
