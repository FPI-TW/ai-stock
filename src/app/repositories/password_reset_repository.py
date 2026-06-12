"""Persistence for password-reset tokens. No method commits — the caller owns the tx."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models.auth import PasswordReset
from app.domain.auth import PasswordResetData


def _to_domain(row: PasswordReset) -> PasswordResetData:
    return PasswordResetData(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
    )


class PasswordResetRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, *, user_id: UUID, token_hash: str, expires_at: datetime, requested_ip: str | None) -> UUID:
        reset_id = uuid4()
        self._db.add(
            PasswordReset(
                id=reset_id,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                requested_ip=requested_ip,
            )
        )
        self._db.flush()
        return reset_id

    def find_by_hash(self, token_hash: str) -> PasswordResetData | None:
        row = self._db.execute(select(PasswordReset).where(PasswordReset.token_hash == token_hash)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def consume(self, reset_id: UUID, *, now: datetime) -> None:
        self._db.execute(
            update(PasswordReset)
            .where(PasswordReset.id == reset_id, PasswordReset.consumed_at.is_(None))
            .values(consumed_at=now),
            execution_options={"synchronize_session": False},
        )
