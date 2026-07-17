"""Unit tests for notification row → domain mapping（跨兩軌 intent ID 收斂）。

分頁 / 擁有者範圍等 DB 行為由 `test_notification_repository_integration.py` 覆蓋；
這裡只釘死純映射：新軌通知（只有 trade_intent_core_id）不可回傳 None。
"""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.db.models.core import Notification
from app.repositories.notification_repository import _to_domain

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 5, 12, 10, 0, tzinfo=TAIPEI)


def _row(*, trade_intent_id: object = None, trade_intent_core_id: object = None) -> Notification:
    return Notification(
        id=uuid4(),
        owner_user_id=uuid4(),
        trade_intent_id=trade_intent_id,
        trade_intent_core_id=trade_intent_core_id,
        type="price_triggered",
        rendered_title="標題",
        rendered_body="內文",
        read_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_to_domain_maps_legacy_track_intent_id() -> None:
    intent_id = uuid4()
    assert _to_domain(_row(trade_intent_id=intent_id)).trade_intent_id == intent_id


def test_to_domain_maps_core_track_intent_id() -> None:
    """新軌通知只填 trade_intent_core_id → 對外仍要給得出 intent ID（否則客戶端無法關聯委託）。"""
    core_id = uuid4()
    assert _to_domain(_row(trade_intent_core_id=core_id)).trade_intent_id == core_id
