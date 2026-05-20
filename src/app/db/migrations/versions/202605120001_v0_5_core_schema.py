"""create v0.5 core schema

Revision ID: 202605120001
Revises:
Create Date: 2026-05-12 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202605120001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("instrument_type", sa.Text(), nullable=False),
        sa.Column("tradable_status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("instrument_type IN ('stock', 'etf')", name="ck_symbols_instrument_type"),
        sa.CheckConstraint("market IN ('TWSE', 'TPEx')", name="ck_symbols_market"),
        sa.CheckConstraint(
            "tradable_status IN ('tradable', 'halted', 'unsupported')",
            name="ck_symbols_tradable_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_symbols"),
        sa.UniqueConstraint("symbol", name="uq_symbols_symbol"),
    )

    op.create_table(
        "trade_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("execution_mode", sa.Text(), nullable=False),
        sa.Column("quantity_lots", sa.Integer(), nullable=False),
        sa.Column("target_price_original", sa.Numeric(9, 4), nullable=False),
        sa.Column("target_price_effective", sa.Numeric(9, 4), nullable=False),
        sa.Column("trigger_reference_price_type", sa.Text(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("time_in_force", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("execution_mode = 'notify_only'", name="ck_trade_intents_execution_mode"),
        sa.CheckConstraint("quantity_lots > 0", name="ck_trade_intents_quantity_lots"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'active', 'triggered', 'cancelled')",
            name="ck_trade_intents_status",
        ),
        sa.CheckConstraint(
            "strategy IN ('buy_price_alert', 'sell_price_alert')",
            name="ck_trade_intents_strategy",
        ),
        sa.CheckConstraint("target_price_effective > 0", name="ck_trade_intents_target_price_effective"),
        sa.CheckConstraint("target_price_original > 0", name="ck_trade_intents_target_price_original"),
        sa.CheckConstraint("time_in_force = 'day'", name="ck_trade_intents_time_in_force"),
        sa.CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="ck_trade_intents_trigger_reference_price_type",
        ),
        sa.ForeignKeyConstraint(["symbol"], ["symbols.symbol"], name="fk_trade_intents_symbol_symbols"),
        sa.PrimaryKeyConstraint("id", name="pk_trade_intents"),
    )
    op.create_index(
        "ix_trade_intents_owner_status_trading_date",
        "trade_intents",
        ["owner_user_id", "status", "trading_date"],
        unique=False,
    )
    op.create_index(
        "ix_trade_intents_symbol_status_trading_date",
        "trade_intents",
        ["symbol", "status", "trading_date"],
        unique=False,
    )
    op.create_index(
        "uq_trade_intents_active_duplicate",
        "trade_intents",
        ["owner_user_id", "symbol", "strategy", "target_price_effective", "quantity_lots", "trading_date"],
        unique=True,
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )

    op.create_table(
        "trigger_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("quote_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_price_effective", sa.Numeric(9, 4), nullable=False),
        sa.Column("trigger_price", sa.Numeric(9, 4), nullable=False),
        sa.Column("trigger_reference_price_type", sa.Text(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("target_price_effective > 0", name="ck_trigger_events_target_price_effective"),
        sa.CheckConstraint("trigger_price > 0", name="ck_trigger_events_trigger_price"),
        sa.CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="ck_trigger_events_trigger_reference_price_type",
        ),
        sa.CheckConstraint(
            "((trigger_reference_price_type = 'last_fallback' AND fallback_used = true) "
            "OR (trigger_reference_price_type IN ('ask', 'bid') AND fallback_used = false))",
            name="ck_trigger_events_fallback_consistency",
        ),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intents.id"],
            name="fk_trigger_events_trade_intent_id_trade_intents",
        ),
        sa.ForeignKeyConstraint(["symbol"], ["symbols.symbol"], name="fk_trigger_events_symbol_symbols"),
        sa.PrimaryKeyConstraint("id", name="pk_trigger_events"),
        sa.UniqueConstraint("trade_intent_id", name="uq_trigger_events_trade_intent_id"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("rendered_title", sa.Text(), nullable=False),
        sa.Column("rendered_body", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("type IN ('price_triggered')", name="ck_notifications_type"),
        sa.CheckConstraint(
            "(type <> 'price_triggered') OR (trade_intent_id IS NOT NULL)",
            name="ck_notifications_price_triggered_intent",
        ),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intents.id"],
            name="fk_notifications_trade_intent_id_trade_intents",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index(
        "ix_notifications_owner_created_at",
        "notifications",
        ["owner_user_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index("ix_notifications_owner_read_at", "notifications", ["owner_user_id", "read_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notifications_owner_read_at", table_name="notifications")
    op.drop_index("ix_notifications_owner_created_at", table_name="notifications")
    op.drop_table("notifications")
    op.drop_table("trigger_events")
    op.drop_index(
        "uq_trade_intents_active_duplicate",
        table_name="trade_intents",
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )
    op.drop_index("ix_trade_intents_symbol_status_trading_date", table_name="trade_intents")
    op.drop_index("ix_trade_intents_owner_status_trading_date", table_name="trade_intents")
    op.drop_table("trade_intents")
    op.drop_table("symbols")
