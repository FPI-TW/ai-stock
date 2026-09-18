"""add trade_intent_twap_slices (新軌 TWAP 切片子表, T1 PR4)

純新增：建立新軌 TWAP 的一對多切片表，FK→trade_intent_core。與 legacy
`twap_slices` 完全並行、不觸碰任何既有表。

CHECK / FK 名一律傳完整裸名（不含會被 naming convention 再套一層的 token），
理由見 202606180003。

Revision ID: 202609100001
Revises: 202608190001
Create Date: 2026-09-10 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609100001"
down_revision: str | None = "202608190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_intent_twap_slices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_quantity_lots", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("primary_notification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_price_available", sa.Boolean(), nullable=True),
        sa.Column("primary_reference_price", sa.Numeric(9, 4), nullable=True),
        sa.Column("primary_reference_price_type", sa.Text(), nullable=True),
        sa.Column("primary_quote_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_followup_required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("price_followup_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_price_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_followup_notification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price_followup_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "planned_quantity_lots > 0",
            name="ck_trade_intent_twap_slices_planned_quantity_lots",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'notified', 'cancelled')",
            name="ck_trade_intent_twap_slices_status",
        ),
        sa.CheckConstraint(
            "primary_reference_price IS NULL OR primary_reference_price > 0",
            name="ck_trade_intent_twap_slices_primary_reference_price",
        ),
        sa.CheckConstraint(
            "primary_reference_price_type IS NULL OR primary_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="ck_trade_intent_twap_slices_primary_reference_price_type",
        ),
        sa.CheckConstraint(
            "price_followup_attempts >= 0 AND price_followup_attempts <= 3",
            name="ck_trade_intent_twap_slices_price_followup_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intent_core.id"],
            name="fk_trade_intent_twap_slices_intent",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_trade_intent_twap_slices_owner"),
        sa.ForeignKeyConstraint(["symbol"], ["symbols.symbol"], name="fk_trade_intent_twap_slices_symbol"),
        sa.ForeignKeyConstraint(
            ["primary_notification_id"],
            ["notifications.id"],
            name="fk_trade_intent_twap_slices_primary_notification",
        ),
        sa.ForeignKeyConstraint(
            ["price_followup_notification_id"],
            ["notifications.id"],
            name="fk_trade_intent_twap_slices_followup_notification",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trade_intent_twap_slices"),
    )
    op.create_index(
        "uq_trade_intent_twap_slices_intent_sequence",
        "trade_intent_twap_slices",
        ["trade_intent_id", "sequence_no"],
        unique=True,
    )
    op.create_index(
        "ix_trade_intent_twap_slices_due",
        "trade_intent_twap_slices",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_trade_intent_twap_slices_followup_due",
        "trade_intent_twap_slices",
        ["price_followup_required", "next_price_followup_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trade_intent_twap_slices_followup_due", table_name="trade_intent_twap_slices")
    op.drop_index("ix_trade_intent_twap_slices_due", table_name="trade_intent_twap_slices")
    op.drop_index("uq_trade_intent_twap_slices_intent_sequence", table_name="trade_intent_twap_slices")
    op.drop_table("trade_intent_twap_slices")
