from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

VALID_NOTIFICATION_TYPES = frozenset(
    {
        "price_triggered",
        "limit_order_triggered",
        "trailing_stop_triggered",
        "market_order_triggered",
        "twap_slice",
        "twap_price_followup",
    }
)


class NotificationError(Exception):
    pass


class NotificationNotFoundError(NotificationError):
    """Raised when a notification cannot be found for the requested owner.

    The same error is raised both when the row does not exist and when it
    belongs to a different owner — callers must not leak existence to
    cross-owner requests.
    """

    def __init__(self, notification_id: UUID) -> None:
        super().__init__(f"notification {notification_id} not found")
        self.notification_id = notification_id


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
