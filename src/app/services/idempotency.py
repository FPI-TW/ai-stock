"""IdempotencyManager — memoises a mutating endpoint's response per (user, key).

§16 contract:
- same (user, key) + same request → return the stored response, no re-execution.
- same (user, key) + different request → IdempotencyConflictError (409).
- records expire after 24h; an expired key is reusable.

Flow is store-after-execute: run the command (which commits its own side effects),
then persist the record. The unique (user_id, key) constraint is a backstop for the
concurrent-same-key race — the loser replays the winner's snapshot. That race can
still double-run the side effect once (both pass the pre-check before either inserts);
this is an accepted V1 single-node limitation. The common case — a client retrying
the same key sequentially — replays without re-executing.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.idempotency_repository import IdempotencyRepository

RECORD_TTL = timedelta(hours=24)

Snapshot = dict[str, Any]


class IdempotencyConflictError(Exception):
    """The key is live but the request differs from the one it first memoised."""


def hash_payload(payload: object) -> str:
    """Stable SHA-256 of a request payload. Sorted keys + compact separators so
    logically-equal payloads hash equal regardless of key order / whitespace."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyManager:
    def __init__(self, db: Session, repo: IdempotencyRepository) -> None:
        self._db = db
        self._repo = repo

    def run(
        self,
        *,
        user_id: UUID,
        key: str,
        endpoint: str,
        payload: object,
        now: datetime,
        execute: Callable[[], Snapshot],
    ) -> Snapshot:
        request_hash = hash_payload(payload)

        existing = self._repo.get(user_id, key)
        if existing is not None and existing.expires_at > now:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError
            return existing.response_snapshot
        if existing is not None:
            # Expired record — clear it so this key can be reused.
            self._repo.delete(user_id, key)
            self._db.flush()

        snapshot = execute()

        try:
            self._repo.create(
                user_id=user_id,
                key=key,
                endpoint=endpoint,
                request_hash=request_hash,
                response_snapshot=snapshot,
                now=now,
                expires_at=now + RECORD_TTL,
            )
            self._db.commit()
        except IntegrityError:
            # Concurrent request won the (user_id, key) insert. Replay its snapshot if
            # the request matches; otherwise it is a genuine conflict.
            self._db.rollback()
            winner = self._repo.get(user_id, key)
            if winner is not None and winner.expires_at > now and winner.request_hash == request_hash:
                return winner.response_snapshot
            raise IdempotencyConflictError from None
        return snapshot
