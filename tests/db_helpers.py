"""Shared integration-test helpers.

`ensure_user` idempotently inserts a users row so owner-scoped inserts satisfy the
owner_user_id -> users(id) FK. It runs on the caller's Session/Connection (same
transaction), so the FK check sees the row even before commit.
"""

from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

# 兩軌的委託與其下游，清庫時要一起掃。T1 cutover 後同一支測試常同時碰到兩邊
# （endpoint 寫新軌、TWAP 仍在舊軌），逐檔手寫這串會漏表也超行寬。
INTENT_TABLES = "notifications, trigger_events, trade_intents, trade_intent_triggers, trade_intent_core"


def ensure_user(executor: Connection | Session, user_id: UUID, *, role: str = "user", status: str = "active") -> UUID:
    executor.execute(
        text(
            "INSERT INTO users (id, email, role, status) "
            "VALUES (CAST(:id AS uuid), :email, :role, :status) ON CONFLICT (id) DO NOTHING"
        ).bindparams(id=str(user_id), email=f"seed-{user_id}@example.test", role=role, status=status)
    )
    return user_id
