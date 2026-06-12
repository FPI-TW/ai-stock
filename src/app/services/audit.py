"""AuditEventWriter — the real audit sink (the L1 de-stub decision put the
audit_events table in L1, so this writes real rows from the first ticket).

No commit: writes flush into the caller's transaction so an audit row and the
state change it records land together (or roll back together). metadata must not
contain plaintext passwords, tokens, or full PII.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models.auth import AuditEvent


class AuditEventWriter:
    def __init__(self, db: Session) -> None:
        self._db = db

    def write(
        self,
        *,
        event_type: str,
        actor_type: str,
        actor_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        self._db.add(
            AuditEvent(
                id=uuid4(),
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                occurred_at=now or datetime.now(UTC),
                event_metadata=metadata or {},
                request_id=request_id,
            )
        )
        self._db.flush()
