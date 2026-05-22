from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.notification import NotificationData
from app.repositories.notification_repository import NotificationRepository


@dataclass(frozen=True)
class MarkNotificationReadInput:
    notification_id: UUID
    owner_user_id: UUID


class MarkNotificationReadCommand:
    """Mark a notification as read inside a single-aggregate transaction.

    Repository does the flush; this command owns the commit/rollback so the
    route layer stays thin. No cross-aggregate side-effects (no quote
    provider, no trade-intent mutation) — kept as a thin wrapper for
    consistency with `CancelTradeIntentCommand`.
    """

    def __init__(self, repo: NotificationRepository, db: Session) -> None:
        self._repo = repo
        self._db = db

    def execute(self, inp: MarkNotificationReadInput) -> NotificationData:
        try:
            notification = self._repo.mark_read(inp.notification_id, inp.owner_user_id)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return notification
