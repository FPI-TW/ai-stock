"""limit_buy_order / limit_sell_order strategy + V2 partial fill fields

Revision ID: 202605250001
Revises: 202605210001
Create Date: 2026-05-25 00:01:00

BE-V0.5-15. Schema changes:

1. `trade_intents`:
   - Add `transaction_mode` (default 'single_notification') and
     `notification_mode` (default 'single') as V2 partial-fill metadata.
     V0.5 evaluator does not branch on these — they exist so the schema
     accepts the new limit_order strategies without losing forward
     compatibility for the V2 broker integration.
   - Add `filled_quantity_lots` (default 0) and `last_fill_at` (nullable).
     V0.5 trigger sets both at trigger time (filled = quantity_lots,
     last_fill_at = triggered_at); they stay at defaults for non-triggered
     intents and existing buy/sell_price_alert rows backfill cleanly.
   - Extend `ck_trade_intents_strategy` to include the two limit_order
     strategies. The baseline name was wrapped by the metadata naming
     convention into `ck_trade_intents_ck_trade_intents_strategy`; drop the
     doubled form and re-add the constraint under the clean
     `ck_<table>_<short>` shape (same cleanup pattern as 202605210001).

2. `trigger_events`:
   - Add `filled_quantity_lots` and backfill from the parent intent
     (`quantity_lots`) so the column can flip to NOT NULL atomically.

3. `notifications`:
   - Extend `ck_notifications_type` and the trade_intent_id guard to cover
     `limit_order_triggered`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202605250001"
down_revision: str | None = "202605210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- trade_intents: new columns + CHECK constraints ---------------------
    op.execute("ALTER TABLE trade_intents ADD COLUMN transaction_mode TEXT NOT NULL DEFAULT 'single_notification'")
    op.execute("ALTER TABLE trade_intents ADD COLUMN notification_mode TEXT NOT NULL DEFAULT 'single'")
    op.execute("ALTER TABLE trade_intents ADD COLUMN filled_quantity_lots INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE trade_intents ADD COLUMN last_fill_at TIMESTAMPTZ NULL")
    op.execute(
        'ALTER TABLE trade_intents ADD CONSTRAINT "ck_trade_intents_transaction_mode" '
        "CHECK (transaction_mode IN ('single_notification', 'partial_fill_allowed'))"
    )
    op.execute(
        'ALTER TABLE trade_intents ADD CONSTRAINT "ck_trade_intents_notification_mode" '
        "CHECK (notification_mode IN ('single', 'per_fill'))"
    )
    op.execute(
        'ALTER TABLE trade_intents ADD CONSTRAINT "ck_trade_intents_filled_quantity_lots" '
        "CHECK (filled_quantity_lots >= 0)"
    )

    # Extend strategy CHECK to include the two limit_order strategies. The
    # baseline (202605120001) name was doubled by the naming convention
    # (`ck_<table>_<constraint_name>` wrapped over an already-prefixed name) —
    # mirror the cleanup pattern from 202605210001 and re-add under the clean
    # `ck_<table>_<short>` form.
    op.execute('ALTER TABLE trade_intents DROP CONSTRAINT "ck_trade_intents_ck_trade_intents_strategy"')
    op.execute(
        'ALTER TABLE trade_intents ADD CONSTRAINT "ck_trade_intents_strategy" '
        "CHECK (strategy IN ('buy_price_alert', 'sell_price_alert', "
        "'limit_buy_order', 'limit_sell_order'))"
    )

    # --- trigger_events: filled_quantity_lots with backfill -----------------
    op.execute("ALTER TABLE trigger_events ADD COLUMN filled_quantity_lots INTEGER NULL")
    op.execute(
        "UPDATE trigger_events te "
        "SET filled_quantity_lots = ti.quantity_lots "
        "FROM trade_intents ti "
        "WHERE te.trade_intent_id = ti.id"
    )
    op.execute("ALTER TABLE trigger_events ALTER COLUMN filled_quantity_lots SET NOT NULL")
    op.execute(
        'ALTER TABLE trigger_events ADD CONSTRAINT "ck_trigger_events_filled_quantity_lots" '
        "CHECK (filled_quantity_lots >= 0)"
    )

    # --- notifications: extend type CHECK + intent_id guard -----------------
    # Same doubled-prefix cleanup as the strategy CHECK above.
    op.execute('ALTER TABLE notifications DROP CONSTRAINT "ck_notifications_ck_notifications_type"')
    op.execute(
        'ALTER TABLE notifications ADD CONSTRAINT "ck_notifications_type" '
        "CHECK (type IN ('price_triggered', 'limit_order_triggered'))"
    )
    op.execute('ALTER TABLE notifications DROP CONSTRAINT "ck_notifications_ck_notifications_price_triggered_intent"')
    op.execute(
        'ALTER TABLE notifications ADD CONSTRAINT "ck_notifications_price_triggered_intent" '
        "CHECK ((type NOT IN ('price_triggered', 'limit_order_triggered')) "
        "OR (trade_intent_id IS NOT NULL))"
    )


def downgrade() -> None:
    # Reverse order: notifications -> trigger_events -> trade_intents.
    # Restore the doubled-prefix names from 202605120001 so a downgrade
    # chain through this revision leaves the schema bit-for-bit equivalent
    # to the prior state.
    #
    # Data semantics: the old schema cannot represent `limit_order_triggered`
    # notifications or `limit_*_order` intents. Purge those rows before
    # re-adding the strict CHECK constraints — re-validating with data still
    # present would otherwise abort the downgrade. Cascading via FK from
    # trade_intents takes care of the matching trigger_events rows.
    op.execute("DELETE FROM notifications WHERE type = 'limit_order_triggered'")
    op.execute(
        "DELETE FROM trigger_events WHERE trade_intent_id IN "
        "(SELECT id FROM trade_intents WHERE strategy IN ('limit_buy_order', 'limit_sell_order'))"
    )
    op.execute(
        "DELETE FROM notifications WHERE trade_intent_id IN "
        "(SELECT id FROM trade_intents WHERE strategy IN ('limit_buy_order', 'limit_sell_order'))"
    )
    op.execute("DELETE FROM trade_intents WHERE strategy IN ('limit_buy_order', 'limit_sell_order')")

    op.execute('ALTER TABLE notifications DROP CONSTRAINT "ck_notifications_price_triggered_intent"')
    op.execute(
        "ALTER TABLE notifications ADD CONSTRAINT "
        '"ck_notifications_ck_notifications_price_triggered_intent" '
        "CHECK ((type <> 'price_triggered') OR (trade_intent_id IS NOT NULL))"
    )
    op.execute('ALTER TABLE notifications DROP CONSTRAINT "ck_notifications_type"')
    op.execute(
        'ALTER TABLE notifications ADD CONSTRAINT "ck_notifications_ck_notifications_type" '
        "CHECK (type IN ('price_triggered'))"
    )

    op.execute('ALTER TABLE trigger_events DROP CONSTRAINT "ck_trigger_events_filled_quantity_lots"')
    op.execute("ALTER TABLE trigger_events DROP COLUMN filled_quantity_lots")

    op.execute('ALTER TABLE trade_intents DROP CONSTRAINT "ck_trade_intents_strategy"')
    op.execute(
        'ALTER TABLE trade_intents ADD CONSTRAINT "ck_trade_intents_ck_trade_intents_strategy" '
        "CHECK (strategy IN ('buy_price_alert', 'sell_price_alert'))"
    )
    op.execute('ALTER TABLE trade_intents DROP CONSTRAINT "ck_trade_intents_filled_quantity_lots"')
    op.execute('ALTER TABLE trade_intents DROP CONSTRAINT "ck_trade_intents_notification_mode"')
    op.execute('ALTER TABLE trade_intents DROP CONSTRAINT "ck_trade_intents_transaction_mode"')
    op.execute("ALTER TABLE trade_intents DROP COLUMN last_fill_at")
    op.execute("ALTER TABLE trade_intents DROP COLUMN filled_quantity_lots")
    op.execute("ALTER TABLE trade_intents DROP COLUMN notification_mode")
    op.execute("ALTER TABLE trade_intents DROP COLUMN transaction_mode")
