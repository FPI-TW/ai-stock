"""Postgres-backed token-bucket rate limiter (Redis-free per V1 decision).

`consume` locks the bucket row (`SELECT ... FOR UPDATE`) so concurrent requests
serialise on it, refills lazily from elapsed wall-clock, and decrements by `cost`.
It does NOT commit — it runs inside the caller's transaction, so the lock is held
until the caller commits/rolls back. `now` is injectable for deterministic tests.

V1-01 wires this to auth buckets only (login / password reset / invitation resend);
L2 (V1-16) inherits the same primitive for every mutating endpoint.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: float
    retry_after_seconds: float


def _refill(tokens: float, elapsed_seconds: float, capacity: float, refill_per_second: float) -> float:
    """Lazily replenish the bucket, clamped to capacity. Pure — unit tested directly."""
    return min(capacity, tokens + max(0.0, elapsed_seconds) * refill_per_second)


class RateLimiter:
    def __init__(self, db: Session) -> None:
        self._db = db

    def consume(
        self,
        bucket_key: str,
        *,
        capacity: float,
        refill_per_second: float,
        cost: float = 1.0,
        now: datetime | None = None,
    ) -> RateLimitDecision:
        if now is None:
            now = datetime.now(UTC)

        # Ensure the row exists (full bucket on first touch), then lock it. The
        # ON CONFLICT keeps an existing bucket untouched so we never reset it.
        self._db.execute(
            text(
                "INSERT INTO rate_limit_buckets (bucket_key, tokens, updated_at) "
                "VALUES (:k, :cap, :now) ON CONFLICT (bucket_key) DO NOTHING"
            ).bindparams(k=bucket_key, cap=capacity, now=now)
        )
        row = self._db.execute(
            text("SELECT tokens, updated_at FROM rate_limit_buckets WHERE bucket_key = :k FOR UPDATE").bindparams(
                k=bucket_key
            )
        ).one()

        elapsed = (now - row.updated_at).total_seconds()
        tokens = _refill(float(row.tokens), elapsed, capacity, refill_per_second)

        allowed = tokens >= cost
        if allowed:
            tokens -= cost

        self._db.execute(
            text("UPDATE rate_limit_buckets SET tokens = :t, updated_at = :now WHERE bucket_key = :k").bindparams(
                t=tokens, now=now, k=bucket_key
            )
        )

        if allowed:
            retry_after = 0.0
        elif refill_per_second > 0:
            retry_after = (cost - tokens) / refill_per_second
        else:
            retry_after = float("inf")
        return RateLimitDecision(allowed=allowed, remaining=tokens, retry_after_seconds=retry_after)
