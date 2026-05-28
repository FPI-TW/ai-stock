"""add market order strategy

Revision ID: 202605280003
Revises: 202605280002
Create Date: 2026-05-28 00:03:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202605280003"
down_revision: str | None = "202605280002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_check_constraints(table_name: str, names: tuple[str, ...]) -> None:
    quoted_names = ", ".join(f"'{name}'" for name in names)
    op.execute(
        f"""
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE t.relname = '{table_name}'
                  AND c.contype = 'c'
                  AND c.conname IN ({quoted_names})
            LOOP
                EXECUTE format('ALTER TABLE {table_name} DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END
        $$;
        """
    )


def upgrade() -> None:
    _drop_check_constraints(
        "trade_intents",
        (
            "ck_trade_intents_strategy",
            "ck_trade_intents_ck_trade_intents_strategy",
            "ck_trade_intents_target_price_presence",
        ),
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_strategy
        CHECK (strategy IN (
            'buy_price_alert', 'sell_price_alert',
            'limit_buy_order', 'limit_sell_order', 'trailing_stop_alert',
            'market_order', 'twap_order'
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
            (strategy = 'market_order'
             AND target_price_original IS NULL
             AND target_price_effective IS NULL)
            OR
            (strategy = 'twap_order'
             AND target_price_original IS NULL
             AND target_price_effective IS NULL)
            OR
            (strategy NOT IN ('trailing_stop_alert', 'market_order', 'twap_order')
             AND target_price_original > 0
             AND target_price_effective > 0)
        )
        """
    )

    _drop_check_constraints(
        "notifications",
        (
            "ck_notifications_type",
            "ck_notifications_triggered_notification_intent",
        ),
    )
    op.execute(
        """
        ALTER TABLE notifications
        ADD CONSTRAINT ck_notifications_type
        CHECK (type IN (
            'price_triggered', 'limit_order_triggered', 'trailing_stop_triggered',
            'market_order_triggered', 'twap_slice', 'twap_price_followup'
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
                'market_order_triggered', 'twap_slice', 'twap_price_followup'
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
            IF EXISTS (SELECT 1 FROM trade_intents WHERE strategy = 'market_order') THEN
                RAISE EXCEPTION 'Cannot downgrade 202605280003: migrate or delete market_order trade_intents first.';
            END IF;
            IF EXISTS (SELECT 1 FROM notifications WHERE type = 'market_order_triggered') THEN
                RAISE EXCEPTION 'Cannot downgrade 202605280003: migrate or delete market_order notifications first.';
            END IF;
        END
        $$;
        """
    )
    _drop_check_constraints(
        "trade_intents",
        (
            "ck_trade_intents_strategy",
            "ck_trade_intents_ck_trade_intents_strategy",
            "ck_trade_intents_target_price_presence",
        ),
    )
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

    _drop_check_constraints(
        "notifications",
        (
            "ck_notifications_type",
            "ck_notifications_triggered_notification_intent",
        ),
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
