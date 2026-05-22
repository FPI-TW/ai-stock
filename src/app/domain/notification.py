from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

VALID_NOTIFICATION_TYPES = frozenset({"price_triggered"})


class NotificationError(Exception):
    pass


@dataclass(frozen=True)
class NotificationData:
    id: UUID
    owner_user_id: UUID
    type: str
    rendered_title: str
    rendered_body: str
    created_at: datetime
    updated_at: datetime
    trade_intent_id: UUID | None = None
    read_at: datetime | None = None
