"""add limit order and trailing stop alert strategies

Revision ID: 202605260001
Revises: 202605210001
Create Date: 2026-05-26 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605260001"
down_revision: str | None = "202605210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_trade_intents_active_duplicate",
        table_name="trade_intents",
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )
    op.execute("ALTER TABLE trade_intents DROP CONSTRAINT ck_trade_intents_ck_trade_intents_strategy")
    op.execute("ALTER TABLE trade_intents DROP CONSTRAINT ck_trade_intents_ck_trade_intents_target_price_effective")
    op.execute("ALTER TABLE trade_intents DROP CONSTRAINT ck_trade_intents_ck_trade_intents_target_price_original")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_ck_notifications_type")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_ck_notifications_price_triggered_intent")

    op.add_column(
        "trade_intents",
        sa.Column(
            "transaction_mode",
            sa.Text(),
            server_default=sa.text("'single_notification'"),
            nullable=False,
        ),
    )
    op.add_column(
        "trade_intents",
        sa.Column("notification_mode", sa.Text(), server_default=sa.text("'single'"), nullable=False),
    )
    op.add_column(
        "trade_intents",
        sa.Column("filled_quantity_lots", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("trade_intents", sa.Column("last_fill_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trade_intents", sa.Column("trail_mode", sa.Text(), nullable=True))
    op.add_column("trade_intents", sa.Column("trail_value", sa.Numeric(9, 4), nullable=True))
    op.add_column("trade_intents", sa.Column("baseline", sa.Numeric(9, 4), nullable=True))
    op.add_column("trade_intents", sa.Column("dynamic_trigger_price", sa.Numeric(9, 4), nullable=True))
    op.add_column("trade_intents", sa.Column("baseline_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("trade_intents", "target_price_original", existing_type=sa.Numeric(9, 4), nullable=True)
    op.alter_column("trade_intents", "target_price_effective", existing_type=sa.Numeric(9, 4), nullable=True)

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
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_transaction_mode
        CHECK (transaction_mode IN ('single_notification', 'partial_fill_allowed'))
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_notification_mode
        CHECK (notification_mode = 'single')
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_filled_quantity_lots
        CHECK (filled_quantity_lots >= 0)
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_trailing_fields_presence
        CHECK (
            (strategy = 'trailing_stop_alert'
             AND trail_mode IN ('percentage', 'fixed_amount')
             AND trail_value IS NOT NULL)
            OR
            (strategy <> 'trailing_stop_alert'
             AND trail_mode IS NULL
             AND trail_value IS NULL
             AND baseline IS NULL
             AND dynamic_trigger_price IS NULL
             AND baseline_updated_at IS NULL)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_trail_value_range
        CHECK (
            trail_value IS NULL
            OR (trail_mode = 'percentage' AND trail_value > 0 AND trail_value <= 10)
            OR (trail_mode = 'fixed_amount' AND trail_value > 0)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_baseline_positive
        CHECK (baseline IS NULL OR baseline > 0)
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_dynamic_trigger_price
        CHECK (dynamic_trigger_price IS NULL OR dynamic_trigger_price > 0)
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

    op.add_column(
        "trigger_events",
        sa.Column("filled_quantity_lots", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("trigger_events", sa.Column("baseline_at_trigger", sa.Numeric(9, 4), nullable=True))
    op.add_column(
        "trigger_events",
        sa.Column("dynamic_trigger_price_at_trigger", sa.Numeric(9, 4), nullable=True),
    )
    op.execute(
        """
        UPDATE trigger_events AS te
        SET filled_quantity_lots = ti.quantity_lots
        FROM trade_intents AS ti
        WHERE ti.id = te.trade_intent_id
        """
    )
    op.execute(
        """
        ALTER TABLE trigger_events
        ADD CONSTRAINT ck_trigger_events_filled_quantity_lots
        CHECK (filled_quantity_lots >= 0)
        """
    )
    op.execute(
        """
        ALTER TABLE trigger_events
        ADD CONSTRAINT ck_trigger_events_baseline_at_trigger
        CHECK (baseline_at_trigger IS NULL OR baseline_at_trigger > 0)
        """
    )
    op.execute(
        """
        ALTER TABLE trigger_events
        ADD CONSTRAINT ck_trigger_events_dynamic_trigger_price_at_trigger
        CHECK (dynamic_trigger_price_at_trigger IS NULL OR dynamic_trigger_price_at_trigger > 0)
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


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_triggered_notification_intent")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT ck_notifications_type")
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_ck_notifications_type
        CHECK (type IN ('price_triggered'))
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_ck_notifications_price_triggered_intent
        CHECK ((type <> 'price_triggered') OR (trade_intent_id IS NOT NULL))
        """
    )

    op.execute("ALTER TABLE trigger_events DROP CONSTRAINT ck_trigger_events_dynamic_trigger_price_at_trigger")
    op.execute("ALTER TABLE trigger_events DROP CONSTRAINT ck_trigger_events_baseline_at_trigger")
    op.execute("ALTER TABLE trigger_events DROP CONSTRAINT ck_trigger_events_filled_quantity_lots")
    op.drop_column("trigger_events", "dynamic_trigger_price_at_trigger")
    op.drop_column("trigger_events", "baseline_at_trigger")
    op.drop_column("trigger_events", "filled_quantity_lots")

    op.drop_index(
        "uq_trade_intents_active_duplicate",
        table_name="trade_intents",
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )
    for name in (
        "ck_trade_intents_dynamic_trigger_price",
        "ck_trade_intents_baseline_positive",
        "ck_trade_intents_trail_value_range",
        "ck_trade_intents_trailing_fields_presence",
        "ck_trade_intents_filled_quantity_lots",
        "ck_trade_intents_notification_mode",
        "ck_trade_intents_transaction_mode",
        "ck_trade_intents_target_price_presence",
        "ck_trade_intents_strategy",
    ):
        op.execute(f"ALTER TABLE trade_intents DROP CONSTRAINT {name}")

    op.execute("DELETE FROM trade_intents WHERE strategy NOT IN ('buy_price_alert', 'sell_price_alert')")
    op.alter_column("trade_intents", "target_price_effective", existing_type=sa.Numeric(9, 4), nullable=False)
    op.alter_column("trade_intents", "target_price_original", existing_type=sa.Numeric(9, 4), nullable=False)
    op.drop_column("trade_intents", "baseline_updated_at")
    op.drop_column("trade_intents", "dynamic_trigger_price")
    op.drop_column("trade_intents", "baseline")
    op.drop_column("trade_intents", "trail_value")
    op.drop_column("trade_intents", "trail_mode")
    op.drop_column("trade_intents", "last_fill_at")
    op.drop_column("trade_intents", "filled_quantity_lots")
    op.drop_column("trade_intents", "notification_mode")
    op.drop_column("trade_intents", "transaction_mode")

    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_ck_trade_intents_strategy
        CHECK (strategy IN ('buy_price_alert', 'sell_price_alert'))
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_ck_trade_intents_target_price_effective
        CHECK (target_price_effective > 0)
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_ck_trade_intents_target_price_original
        CHECK (target_price_original > 0)
        """
    )
    op.create_index(
        "uq_trade_intents_active_duplicate",
        "trade_intents",
        ["owner_user_id", "symbol", "strategy", "target_price_effective", "quantity_lots", "trading_date"],
        unique=True,
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )
