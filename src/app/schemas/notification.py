from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.notification import NotificationData


class NotificationResponseData(BaseModel):
    """List-response shape for a single notification.

    Excludes `updated_at` deliberately: V0.5 has no surface for it and the
    work order does not list it. Add when V1 surfaces edit / template
    versioning timestamps.
    """

    id: UUID
    type: str
    trade_intent_id: UUID | None = Field(default=None, serialization_alias="tradeIntentId")
    rendered_title: str = Field(serialization_alias="renderedTitle")
    rendered_body: str = Field(serialization_alias="renderedBody")
    read_at: datetime | None = Field(default=None, serialization_alias="readAt")
    created_at: datetime = Field(serialization_alias="createdAt")


class NotificationListResponse(BaseModel):
    data: list[NotificationResponseData]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
    page_size: int = Field(serialization_alias="pageSize")


class NotificationReadResponseData(BaseModel):
    """Minimal mark-read response per BE-V0.5-10 spec (id + readAt only)."""

    id: UUID
    read_at: datetime = Field(serialization_alias="readAt")


class NotificationReadResponse(BaseModel):
    data: NotificationReadResponseData


def map_to_response_data(notification: NotificationData) -> NotificationResponseData:
    return NotificationResponseData(
        id=notification.id,
        type=notification.type,
        trade_intent_id=notification.trade_intent_id,
        rendered_title=notification.rendered_title,
        rendered_body=notification.rendered_body,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


def map_to_read_response_data(notification: NotificationData) -> NotificationReadResponseData:
    # read_at is guaranteed non-null after `mark_read` returns (the repo
    # either sets it on first call or returns the already-populated value).
    if notification.read_at is None:
        raise RuntimeError(
            f"mark_read returned notification {notification.id} with read_at=None; this indicates a repository bug."
        )
    return NotificationReadResponseData(id=notification.id, read_at=notification.read_at)
