"""Reads + upsert on `system_flags` (L2 kill switch). No method commits — the
caller owns the transaction."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.core import SystemFlag


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    enabled: bool
    reason: str | None
    updated_at: datetime | None
    updated_by: UUID | None


class SystemFlagRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def is_enabled(self, flag_key: str) -> bool:
        """True only when a row exists and is enabled. A missing flag means off."""
        enabled = self._db.execute(
            select(SystemFlag.enabled).where(SystemFlag.flag_key == flag_key)
        ).scalar_one_or_none()
        return bool(enabled)

    def get_state(self, flag_key: str) -> KillSwitchState:
        row = self._db.execute(select(SystemFlag).where(SystemFlag.flag_key == flag_key)).scalar_one_or_none()
        if row is None:
            return KillSwitchState(enabled=False, reason=None, updated_at=None, updated_by=None)
        return KillSwitchState(
            enabled=row.enabled,
            reason=row.reason,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )

    def upsert(self, *, flag_key: str, enabled: bool, updated_by: UUID, reason: str, now: datetime) -> None:
        stmt = insert(SystemFlag).values(
            flag_key=flag_key,
            enabled=enabled,
            updated_by=updated_by,
            updated_at=now,
            reason=reason,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["flag_key"],
            set_={"enabled": enabled, "updated_by": updated_by, "updated_at": now, "reason": reason},
        )
        self._db.execute(stmt)
