"""Reads + writes on `idempotency_keys` (L2 §16). No method commits — the caller
(IdempotencyManager) owns the transaction so the record can land together with, or
right after, the side effect it memoises."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from app.db.models.core import IdempotencyKey


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    request_hash: str
    response_snapshot: dict[str, Any]
    expires_at: datetime


class IdempotencyRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, user_id: UUID, key: str) -> IdempotencyRecord | None:
        row = self._db.execute(
            select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
        ).scalar_one_or_none()
        if row is None:
            return None
        return IdempotencyRecord(
            request_hash=row.request_hash,
            response_snapshot=row.response_snapshot,
            expires_at=row.expires_at,
        )

    def create(
        self,
        *,
        user_id: UUID,
        key: str,
        endpoint: str,
        request_hash: str,
        response_snapshot: dict[str, Any],
        now: datetime,
        expires_at: datetime,
    ) -> None:
        """Insert a completed record. Flushes so a unique (user_id, key) violation
        surfaces here (the manager treats it as a concurrent winner)."""
        self._db.add(
            IdempotencyKey(
                id=uuid4(),
                user_id=user_id,
                key=key,
                endpoint=endpoint,
                request_hash=request_hash,
                response_snapshot=response_snapshot,
                status="completed",
                created_at=now,
                expires_at=expires_at,
            )
        )
        self._db.flush()

    def delete(self, user_id: UUID, key: str) -> None:
        self._db.execute(delete(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key))

    def delete_expired(self, now: datetime) -> int:
        """Delete every record past its expires_at. Returns the row count. No commit —
        the caller (cleanup scheduler) owns the transaction. Backed by the
        ix_idempotency_keys_expires_at index."""
        stmt = delete(IdempotencyKey).where(IdempotencyKey.expires_at < now)
        result = cast(CursorResult[Any], self._db.execute(stmt))
        return int(result.rowcount or 0)
