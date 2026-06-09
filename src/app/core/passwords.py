"""argon2id password hashing.

The hasher is parameterised from Settings (env-overridable cost) so production can
tune memory/time cost without code changes. Never log raw passwords or hashes.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import Settings

# MVP password rule (spec §13): minimum length only, no composition requirement.
MIN_PASSWORD_LENGTH = 8


def build_password_hasher(settings: Settings) -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
    )


def hash_password(hasher: PasswordHasher, password: str) -> str:
    return hasher.hash(password)


def verify_password(hasher: PasswordHasher, password_hash: str, password: str) -> bool:
    """Return True iff the password matches the hash. Never raises on mismatch."""
    try:
        hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True


def is_password_strong_enough(password: str) -> bool:
    return len(password) >= MIN_PASSWORD_LENGTH
