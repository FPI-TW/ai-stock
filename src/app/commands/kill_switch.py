"""Kill-switch toggle command (L2). Admin flips the global trigger-halt flag; the
flag write + audit row land in one transaction, then the in-process cache is
invalidated so the change takes effect within a tick rather than the cache TTL.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories.system_flag_repository import SystemFlagRepository
from app.services.audit import AuditEventWriter
from app.services.kill_switch import GLOBAL_TRIGGER_HALT, KillSwitchProvider


@dataclass(frozen=True, slots=True)
class SetKillSwitchInput:
    enabled: bool
    reason: str
    actor_admin_id: UUID
    now: datetime
    request_id: str | None = None


class SetKillSwitchCommand:
    def __init__(
        self,
        db: Session,
        flags: SystemFlagRepository,
        audit: AuditEventWriter,
        provider: KillSwitchProvider | None,
    ) -> None:
        self._db = db
        self._flags = flags
        self._audit = audit
        self._provider = provider

    def execute(self, inp: SetKillSwitchInput) -> None:
        try:
            self._flags.upsert(
                flag_key=GLOBAL_TRIGGER_HALT,
                enabled=inp.enabled,
                updated_by=inp.actor_admin_id,
                reason=inp.reason,
                now=inp.now,
            )
            self._audit.write(
                event_type="kill_switch_enabled" if inp.enabled else "kill_switch_disabled",
                actor_type="admin",
                actor_id=inp.actor_admin_id,
                metadata={"reason": inp.reason},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        # Invalidate only after the flip is durable — the dispatcher / create path
        # must never read "off" from a freshly cleared cache while the DB still says
        # "on". The provider is None only in DB-less test wiring.
        if self._provider is not None:
            self._provider.invalidate()
