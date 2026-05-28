"""add TWAP order strategy

Revision ID: 202605280001
Revises: 202605260001
Create Date: 2026-05-28 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202605280001"
down_revision: str | None = "202605260001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_trade_intents_active_duplicate",
        table_name="trade_intents",
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )
    op.execute("ALTER TABLE trade_intents DROP CONSTRAINT ck_trade_intents_strategy")
    op.execute("ALTER TABLE trade_intents DROP CONSTRAINT ck_trade_intents_target_price_presence")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_type")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_triggered_notification_intent")

    op.add_column("trade_intents", sa.Column("position_side", sa.Text(), nullable=True))
    op.add_column("trade_intents", sa.Column("twap_interval_seconds", sa.Integer(), nullable=True))
    op.add_column("trade_intents", sa.Column("twap_end_time", sa.Time(), nullable=True))
    op.add_column("trade_intents", sa.Column("twap_start_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trade_intents", sa.Column("twap_end_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trade_intents", sa.Column("twap_available_slice_count", sa.Integer(), nullable=True))
    op.add_column("trade_intents", sa.Column("twap_materialized_slice_count", sa.Integer(), nullable=True))

    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_strategy
        CHECK (strategy IN (
            'buy_price_alert', 'sell_price_alert',
            'limit_buy_order', 'limit_sell_order', 'trailing_stop_alert',
            'twap_order'
        ))
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_target_price_presence
        CHECK (
            (strategy = 'trailing_stop_alert'
             AND target_price_original IS NULL
             AND target_price_effective IS NULL)
            OR
            (strategy = 'twap_order'
             AND target_price_original IS NULL
             AND target_price_effective IS NULL)
            OR
            (strategy NOT IN ('trailing_stop_alert', 'twap_order')
             AND target_price_original > 0
             AND target_price_effective > 0)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_twap_fields_presence
        CHECK (
            (strategy = 'twap_order'
             AND position_side IN ('long', 'short')
             AND twap_interval_seconds IS NOT NULL
             AND twap_end_time IS NOT NULL
             AND twap_start_at IS NOT NULL
             AND twap_end_at IS NOT NULL
             AND twap_available_slice_count IS NOT NULL
             AND twap_materialized_slice_count IS NOT NULL)
            OR
            (strategy <> 'twap_order'
             AND position_side IS NULL
             AND twap_interval_seconds IS NULL
             AND twap_end_time IS NULL
             AND twap_start_at IS NULL
             AND twap_end_at IS NULL
             AND twap_available_slice_count IS NULL
             AND twap_materialized_slice_count IS NULL)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_twap_interval_seconds
        CHECK (twap_interval_seconds IS NULL OR (twap_interval_seconds >= 1 AND twap_interval_seconds <= 3600))
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_twap_available_slice_count
        CHECK (twap_available_slice_count IS NULL OR twap_available_slice_count >= 2)
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_twap_materialized_slice_count
        CHECK (
            twap_materialized_slice_count IS NULL
            OR (twap_materialized_slice_count >= 2 AND twap_materialized_slice_count <= 200)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_trade_intents_active_duplicate
        ON trade_intents (
            owner_user_id,
            symbol,
            strategy,
            target_price_effective,
            trail_mode,
            trail_value,
            quantity_lots,
            trading_date
        )
        NULLS NOT DISTINCT
        WHERE status IN ('scheduled', 'active')
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_trade_intents_active_twap_duplicate
        ON trade_intents (owner_user_id, symbol, position_side, trading_date)
        WHERE strategy = 'twap_order' AND status IN ('scheduled', 'active')
        """
    )

    op.create_table(
        "twap_slices",
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
        sa.Column("price_followup_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("price_followup_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_price_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_followup_notification_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price_followup_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("planned_quantity_lots > 0", name="ck_twap_slices_planned_quantity_lots"),
        sa.CheckConstraint("status IN ('pending', 'notified', 'cancelled')", name="ck_twap_slices_status"),
        sa.CheckConstraint(
            "primary_reference_price IS NULL OR primary_reference_price > 0",
            name="ck_twap_slices_primary_reference_price",
        ),
        sa.CheckConstraint(
            "primary_reference_price_type IS NULL OR primary_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="ck_twap_slices_primary_reference_price_type",
        ),
        sa.CheckConstraint(
            "price_followup_attempts >= 0 AND price_followup_attempts <= 3",
            name="ck_twap_slices_price_followup_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intents.id"],
            name="fk_twap_slices_trade_intent_id_trade_intents",
        ),
        sa.ForeignKeyConstraint(["symbol"], ["symbols.symbol"], name="fk_twap_slices_symbol_symbols"),
        sa.ForeignKeyConstraint(
            ["primary_notification_id"],
            ["notifications.id"],
            name="fk_twap_slices_primary_notification_id_notifications",
        ),
        sa.ForeignKeyConstraint(
            ["price_followup_notification_id"],
            ["notifications.id"],
            name="fk_twap_slices_price_followup_notification_id_notifications",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_twap_slices"),
    )
    op.create_index("uq_twap_slices_intent_sequence", "twap_slices", ["trade_intent_id", "sequence_no"], unique=True)
    op.create_index("ix_twap_slices_due", "twap_slices", ["status", "scheduled_at"], unique=False)
    op.create_index(
        "ix_twap_slices_price_followup_due",
        "twap_slices",
        ["price_followup_required", "next_price_followup_at"],
        unique=False,
    )

    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_type
        CHECK (type IN (
            'price_triggered', 'limit_order_triggered', 'trailing_stop_triggered',
            'twap_slice', 'twap_price_followup'
        ))
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_triggered_notification_intent
        CHECK (
            type NOT IN (
                'price_triggered', 'limit_order_triggered', 'trailing_stop_triggered',
                'twap_slice', 'twap_price_followup'
            )
            OR trade_intent_id IS NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM trade_intents WHERE strategy = 'twap_order') THEN
                RAISE EXCEPTION 'Cannot downgrade 202605280001: migrate or delete TWAP trade_intents first.';
            END IF;
            IF EXISTS (SELECT 1 FROM notifications WHERE type IN ('twap_slice', 'twap_price_followup')) THEN
                RAISE EXCEPTION 'Cannot downgrade 202605280001: migrate or delete TWAP notifications first.';
            END IF;
        END
        $$;
        """
    )

    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_triggered_notification_intent")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_type")
    op.drop_index("ix_twap_slices_price_followup_due", table_name="twap_slices")
    op.drop_index("ix_twap_slices_due", table_name="twap_slices")
    op.drop_index("uq_twap_slices_intent_sequence", table_name="twap_slices")
    op.drop_table("twap_slices")

    op.drop_index(
        "uq_trade_intents_active_twap_duplicate",
        table_name="trade_intents",
        postgresql_where=sa.text("strategy = 'twap_order' AND status IN ('scheduled', 'active')"),
    )
    op.drop_index(
        "uq_trade_intents_active_duplicate",
        table_name="trade_intents",
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )
    for name in (
        "ck_trade_intents_twap_materialized_slice_count",
        "ck_trade_intents_twap_available_slice_count",
        "ck_trade_intents_twap_interval_seconds",
        "ck_trade_intents_twap_fields_presence",
        "ck_trade_intents_target_price_presence",
        "ck_trade_intents_strategy",
    ):
        op.execute(f"ALTER TABLE trade_intents DROP CONSTRAINT {name}")

    op.drop_column("trade_intents", "twap_materialized_slice_count")
    op.drop_column("trade_intents", "twap_available_slice_count")
    op.drop_column("trade_intents", "twap_end_at")
    op.drop_column("trade_intents", "twap_start_at")
    op.drop_column("trade_intents", "twap_end_time")
    op.drop_column("trade_intents", "twap_interval_seconds")
    op.drop_column("trade_intents", "position_side")

    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_strategy
        CHECK (strategy IN (
            'buy_price_alert', 'sell_price_alert',
            'limit_buy_order', 'limit_sell_order', 'trailing_stop_alert'
        ))
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_target_price_presence
        CHECK (
            (strategy = 'trailing_stop_alert' AND target_price_original IS NULL
             AND target_price_effective IS NULL)
            OR
            (strategy <> 'trailing_stop_alert' AND target_price_original > 0
             AND target_price_effective > 0)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_trade_intents_active_duplicate
        ON trade_intents (
            owner_user_id,
            symbol,
            strategy,
            target_price_effective,
            trail_mode,
            trail_value,
            quantity_lots,
            trading_date
        )
        NULLS NOT DISTINCT
        WHERE status IN ('scheduled', 'active')
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_type
        CHECK (type IN ('price_triggered', 'limit_order_triggered', 'trailing_stop_triggered'))
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_triggered_notification_intent
        CHECK (
            type NOT IN ('price_triggered', 'limit_order_triggered', 'trailing_stop_triggered')
            OR trade_intent_id IS NOT NULL
        )
        """
    )
