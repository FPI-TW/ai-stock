from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, MarkNotificationReadCommandDep, NotificationRepoDep
from app.api.routes._pagination import validate_cursor
from app.commands.notification import MarkNotificationReadInput
from app.schemas.notification import (
    NotificationListResponse,
    NotificationReadResponse,
    map_to_read_response_data,
    map_to_response_data,
)

router = APIRouter()


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    user: CurrentUserDep,
    notification_repo: NotificationRepoDep,
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    cursor: str | None = None,
    page_size: int = Query(default=50, ge=1, le=100, alias="pageSize"),
) -> NotificationListResponse:
    validate_cursor(cursor)

    items, next_cursor = notification_repo.list_by_owner(
        owner_user_id=user.user_id,
        unread_only=unread_only,
        cursor=cursor,
        page_size=page_size,
    )
    return NotificationListResponse(
        data=[map_to_response_data(n) for n in items],
        next_cursor=next_cursor,
        page_size=page_size,
    )


@router.post("/{notification_id}/read", response_model=NotificationReadResponse)
def mark_notification_read(
    notification_id: UUID,
    user: CurrentUserDep,
    command: MarkNotificationReadCommandDep,
) -> NotificationReadResponse:
    notification = command.execute(
        MarkNotificationReadInput(notification_id=notification_id, owner_user_id=user.user_id)
    )
    return NotificationReadResponse(data=map_to_read_response_data(notification))
