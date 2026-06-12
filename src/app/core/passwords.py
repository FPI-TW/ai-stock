"""argon2id password hashing.

The hasher is parameterised from Settings (env-overridable cost) so production can
tune memory/time cost without code changes. Never log raw passwords or hashes.
"""

from functools import lru_cache

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


@lru_cache(maxsize=8)
def _dummy_hash(time_cost: int, memory_cost: int, parallelism: int) -> str:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism).hash("dummy")


def dummy_password_hash(hasher: PasswordHasher) -> str:
    """A throwaway argon2 hash encoded with `hasher`'s exact cost params, computed once
    per param-set (process-wide cache). Login runs a `verify_password` against this when
    no real hash exists (account missing / disabled / not yet activated) so that every
    auth path pays the same argon2 cost — otherwise the skipped hash makes those paths
    measurably faster and leaks which emails are real accounts (timing-based user
    enumeration). Verify time depends on the params encoded in the hash string, so the
    dummy must match the live hasher's params, not argon2's defaults."""
    return _dummy_hash(hasher.time_cost, hasher.memory_cost, hasher.parallelism)


def is_password_strong_enough(password: str) -> bool:
    return len(password) >= MIN_PASSWORD_LENGTH
