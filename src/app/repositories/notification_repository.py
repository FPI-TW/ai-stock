from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.models.core import Notification
from app.domain.notification import NotificationData, NotificationNotFoundError
from app.domain.trade_intent import InvalidCursorError


def _to_domain(row: Notification) -> NotificationData:
    return NotificationData(
        id=row.id,
        owner_user_id=row.owner_user_id,
        # 收件匣跨兩軌：舊軌填 trade_intent_id、新軌填 trade_intent_core_id，
        # `triggered_notification_intent` CHECK 保證恰好一個非空 → 對外收斂成單一 ID。
        # 舊軌退役後 trade_intent_id 欄消失，這裡跟著改讀單欄。
        trade_intent_id=row.trade_intent_id or row.trade_intent_core_id,
        type=row.type,
        rendered_title=row.rendered_title,
        rendered_body=row.rendered_body,
        read_at=row.read_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class NotificationRepository:
    """Owner-scoped reads and read-state mutations on `notifications`.

    Same transactional contract as `IntentRepository`: no method commits —
    the caller (typically a command) owns the transaction boundary.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_by_owner(
        self,
        owner_user_id: UUID,
        unread_only: bool,
        cursor: str | None,
        page_size: int,
    ) -> tuple[list[NotificationData], str | None]:
        """Return one page of notifications ordered by `created_at DESC, id ASC`.

        Keyset pagination: `cursor` is the trailing row's UUID; the anchor
        row is fetched server-side so the client never has to encode
        timestamps. Aligns with the `ix_notifications_owner_created_at`
        index `(owner_user_id, created_at DESC)`.
        """

        stmt = select(Notification).where(Notification.owner_user_id == owner_user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.asc())

        if cursor:
            cursor_id = UUID(cursor)  # format already validated by route layer
            anchor = self._db.execute(
                select(Notification).where(
                    Notification.id == cursor_id,
                    Notification.owner_user_id == owner_user_id,
                )
            ).scalar_one_or_none()
            if anchor is None:
                raise InvalidCursorError(cursor_id)
            stmt = stmt.where(
                or_(
                    Notification.created_at < anchor.created_at,
                    and_(
                        Notification.created_at == anchor.created_at,
                        Notification.id > anchor.id,
                    ),
                )
            )

        rows = list(self._db.execute(stmt.limit(page_size + 1)).scalars().all())
        has_more = len(rows) > page_size
        page = rows[:page_size]

        next_cursor = str(page[-1].id) if has_more and page else None
        return [_to_domain(r) for r in page], next_cursor

    def mark_read(self, notification_id: UUID, owner_user_id: UUID) -> NotificationData:
        """Set `read_at` on first call; subsequent calls are no-ops.

        Idempotency contract: if `read_at` is already populated, this method
        returns the existing state without touching `updated_at`. This means
        re-reading a notification does not bump any timestamp — important for
        future audit / change-feed use cases.

        Owner mismatch raises `NotificationNotFoundError` (not a separate
        forbidden error) so the API never leaks existence across owners.
        """

        row = self._db.execute(select(Notification).where(Notification.id == notification_id)).scalar_one_or_none()
        if row is None or row.owner_user_id != owner_user_id:
            raise NotificationNotFoundError(notification_id)
        if row.read_at is None:
            self._db.execute(
                update(Notification)
                .where(
                    Notification.id == notification_id,
                    Notification.owner_user_id == owner_user_id,
                    Notification.read_at.is_(None),
                )
                .values(read_at=func.now(), updated_at=func.now()),
                execution_options={"synchronize_session": False},
            )
            self._db.refresh(row)
        return _to_domain(row)
