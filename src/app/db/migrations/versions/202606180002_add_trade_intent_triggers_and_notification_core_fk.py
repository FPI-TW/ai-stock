"""add trade_intent_triggers + notifications.trade_intent_core_id (模式丙 PR2)

新軌觸發下游：
- 新建 `trade_intent_triggers`（對應 legacy trigger_events，FK→trade_intent_core），
  委託內部稽核、隨新軌退役。
- `notifications` 共用（收件匣跨功能）：additive 加 nullable `trade_intent_core_id`
  FK→trade_intent_core，並放寬 `triggered_notification_intent` CHECK 成「兩個 intent
  欄至少一個非空」。對舊資料 / 舊行為零影響。

完全不碰 `trade_intents`。

Revision ID: 202606180002
Revises: 202606180001
Create Date: 2026-06-18 00:02:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606180002"
down_revision: str | None = "202606180001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOTIFICATION_TRIGGER_TYPES = (
    "'price_triggered', 'limit_order_triggered', 'trailing_stop_triggered', "
    "'market_order_triggered', 'twap_slice', 'twap_price_followup'"
)


def upgrade() -> None:
    op.create_table(
        "trade_intent_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("quote_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_price_effective", sa.Numeric(9, 4), nullable=False),
        sa.Column("trigger_price", sa.Numeric(9, 4), nullable=False),
        sa.Column("trigger_reference_price_type", sa.Text(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("filled_quantity_lots", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("baseline_at_trigger", sa.Numeric(9, 4), nullable=True),
        sa.Column("dynamic_trigger_price_at_trigger", sa.Numeric(9, 4), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="ck_trade_intent_triggers_trigger_reference_price_type",
        ),
        sa.CheckConstraint(
            "((trigger_reference_price_type = 'last_fallback' AND fallback_used = true) "
            "OR (trigger_reference_price_type IN ('ask', 'bid') AND fallback_used = false))",
            name="ck_trade_intent_triggers_fallback_consistency",
        ),
        sa.CheckConstraint("target_price_effective > 0", name="ck_trade_intent_triggers_target_price_effective"),
        sa.CheckConstraint("trigger_price > 0", name="ck_trade_intent_triggers_trigger_price"),
        sa.CheckConstraint("filled_quantity_lots >= 0", name="ck_trade_intent_triggers_filled_quantity_lots"),
        sa.CheckConstraint(
            "baseline_at_trigger IS NULL OR baseline_at_trigger > 0",
            name="ck_trade_intent_triggers_baseline_at_trigger",
        ),
        sa.CheckConstraint(
            "dynamic_trigger_price_at_trigger IS NULL OR dynamic_trigger_price_at_trigger > 0",
            name="ck_trade_intent_triggers_dynamic_trigger_price_at_trigger",
        ),
        sa.ForeignKeyConstraint(["trade_intent_id"], ["trade_intent_core.id"], name="fk_trade_intent_triggers_intent"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_trade_intent_triggers_owner"),
        sa.ForeignKeyConstraint(["symbol"], ["symbols.symbol"], name="fk_trade_intent_triggers_symbol"),
        sa.PrimaryKeyConstraint("id", name="pk_trade_intent_triggers"),
        sa.UniqueConstraint("trade_intent_id", name="uq_trade_intent_triggers_trade_intent_id"),
    )

    # notifications 共用：additive 加欄 + 放寬 CHECK（不碰舊資料）
    op.add_column(
        "notifications",
        sa.Column("trade_intent_core_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_notifications_trade_intent_core_id_trade_intent_core",
        "notifications",
        "trade_intent_core",
        ["trade_intent_core_id"],
        ["id"],
    )
    # 傳裸名：alembic 會套命名慣例補上 ck_notifications_ 前綴（傳完整名會雙重前綴）
    op.drop_constraint("triggered_notification_intent", "notifications", type_="check")
    op.create_check_constraint(
        "triggered_notification_intent",
        "notifications",
        f"(type NOT IN ({_NOTIFICATION_TRIGGER_TYPES})) "
        "OR (trade_intent_id IS NOT NULL OR trade_intent_core_id IS NOT NULL)",
    )


def downgrade() -> None:
    # 傳裸名：alembic 會套命名慣例補上 ck_notifications_ 前綴（傳完整名會雙重前綴）
    op.drop_constraint("triggered_notification_intent", "notifications", type_="check")
    op.create_check_constraint(
        "triggered_notification_intent",
        "notifications",
        f"(type NOT IN ({_NOTIFICATION_TRIGGER_TYPES})) OR (trade_intent_id IS NOT NULL)",
    )
    op.drop_constraint("fk_notifications_trade_intent_core_id_trade_intent_core", "notifications", type_="foreignkey")
    op.drop_column("notifications", "trade_intent_core_id")
    op.drop_table("trade_intent_triggers")
