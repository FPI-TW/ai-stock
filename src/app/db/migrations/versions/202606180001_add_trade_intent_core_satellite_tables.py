"""add trade_intent_core + per-strategy satellite tables (模式丙: parallel slice)

純新增：建立新的 trade_intent_core 核心表與 3 張 1:1 衛星表，與 legacy
trade_intents **完全並行、互不影響**。本 migration 不觸碰任何既有表。

Revision ID: 202606180001
Revises: 202606120002
Create Date: 2026-06-18 00:01:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606180001"
down_revision: str | None = "202606120002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_intent_core",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("execution_mode", sa.Text(), nullable=False),
        sa.Column("quantity_lots", sa.Integer(), nullable=False),
        sa.Column("trigger_reference_price_type", sa.Text(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("time_in_force", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "transaction_mode",
            sa.Text(),
            server_default=sa.text("'single_notification'"),
            nullable=False,
        ),
        sa.Column("notification_mode", sa.Text(), server_default=sa.text("'single'"), nullable=False),
        sa.Column("filled_quantity_lots", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_fill_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "strategy IN ("
            "'buy_price_alert', 'sell_price_alert', "
            "'limit_buy_order', 'limit_sell_order', 'trailing_stop_alert', "
            "'market_order', 'market_buy_order', 'market_sell_order', 'twap_order'"
            ")",
            name="ck_trade_intent_core_strategy",
        ),
        sa.CheckConstraint("execution_mode = 'notify_only'", name="ck_trade_intent_core_execution_mode"),
        sa.CheckConstraint("time_in_force = 'day'", name="ck_trade_intent_core_time_in_force"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'active', 'triggered', 'expired', 'cancelled', 'cancelled_by_account_disabled')",
            name="ck_trade_intent_core_status",
        ),
        sa.CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="ck_trade_intent_core_trigger_reference_price_type",
        ),
        sa.CheckConstraint("quantity_lots > 0", name="ck_trade_intent_core_quantity_lots"),
        sa.CheckConstraint(
            "transaction_mode IN ('single_notification', 'partial_fill_allowed')",
            name="ck_trade_intent_core_transaction_mode",
        ),
        sa.CheckConstraint("notification_mode = 'single'", name="ck_trade_intent_core_notification_mode"),
        sa.CheckConstraint("filled_quantity_lots >= 0", name="ck_trade_intent_core_filled_quantity_lots"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_trade_intent_core_owner_user_id_users"),
        sa.ForeignKeyConstraint(["symbol"], ["symbols.symbol"], name="fk_trade_intent_core_symbol_symbols"),
        sa.PrimaryKeyConstraint("id", name="pk_trade_intent_core"),
    )
    op.create_index(
        "ix_trade_intent_core_owner_status_trading_date",
        "trade_intent_core",
        ["owner_user_id", "status", "trading_date"],
    )
    op.create_index(
        "ix_trade_intent_core_symbol_status_trading_date",
        "trade_intent_core",
        ["symbol", "status", "trading_date"],
    )
    op.create_index(
        "uq_trade_intent_core_active_duplicate",
        "trade_intent_core",
        ["owner_user_id", "symbol", "strategy", "dedup_key", "quantity_lots", "trading_date"],
        unique=True,
        postgresql_where=sa.text("strategy <> 'twap_order' AND status IN ('scheduled', 'active')"),
    )
    op.create_index(
        "uq_trade_intent_core_active_twap_duplicate",
        "trade_intent_core",
        ["owner_user_id", "symbol", "dedup_key", "trading_date"],
        unique=True,
        postgresql_where=sa.text("strategy = 'twap_order' AND status IN ('scheduled', 'active')"),
    )
    # 有界索引：只涵蓋 live 列，讓 system_list_active_symbols 的 DISTINCT symbol WHERE
    # status='active' 不必隨終態列累積而全掃。
    op.create_index(
        "ix_trade_intent_core_active_symbols",
        "trade_intent_core",
        ["symbol"],
        postgresql_where=sa.text("status IN ('scheduled', 'active')"),
    )

    op.create_table(
        "trade_intent_price_params",
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_price_original", sa.Numeric(9, 4), nullable=False),
        sa.Column("target_price_effective", sa.Numeric(9, 4), nullable=False),
        sa.CheckConstraint("target_price_original > 0", name="ck_trade_intent_price_params_target_price_original"),
        sa.CheckConstraint("target_price_effective > 0", name="ck_trade_intent_price_params_target_price_effective"),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intent_core.id"],
            name="fk_trade_intent_price_params_intent",
        ),
        sa.PrimaryKeyConstraint("trade_intent_id", name="pk_trade_intent_price_params"),
    )

    op.create_table(
        "trade_intent_trailing_params",
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trail_mode", sa.Text(), nullable=False),
        sa.Column("trail_value", sa.Numeric(9, 4), nullable=False),
        sa.Column("baseline", sa.Numeric(9, 4), nullable=True),
        sa.Column("dynamic_trigger_price", sa.Numeric(9, 4), nullable=True),
        sa.Column("baseline_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trail_mode IN ('percentage', 'fixed_amount')",
            name="ck_trade_intent_trailing_params_trail_mode",
        ),
        sa.CheckConstraint(
            "(trail_mode = 'percentage' AND trail_value > 0 AND trail_value <= 10) OR "
            "(trail_mode = 'fixed_amount' AND trail_value > 0)",
            name="ck_trade_intent_trailing_params_trail_value_range",
        ),
        sa.CheckConstraint(
            "baseline IS NULL OR baseline > 0",
            name="ck_trade_intent_trailing_params_baseline_positive",
        ),
        sa.CheckConstraint(
            "dynamic_trigger_price IS NULL OR dynamic_trigger_price > 0",
            name="ck_trade_intent_trailing_params_dynamic_trigger_price",
        ),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intent_core.id"],
            name="fk_trade_intent_trailing_params_intent",
        ),
        sa.PrimaryKeyConstraint("trade_intent_id", name="pk_trade_intent_trailing_params"),
    )

    op.create_table(
        "trade_intent_twap_params",
        sa.Column("trade_intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_side", sa.Text(), nullable=False),
        sa.Column("twap_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("twap_end_time", sa.Time(), nullable=False),
        sa.Column("twap_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("twap_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("twap_available_slice_count", sa.Integer(), nullable=False),
        sa.Column("twap_materialized_slice_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("position_side IN ('long', 'short')", name="ck_trade_intent_twap_params_position_side"),
        sa.CheckConstraint(
            "twap_interval_seconds >= 1 AND twap_interval_seconds <= 3600",
            name="ck_trade_intent_twap_params_twap_interval_seconds",
        ),
        sa.CheckConstraint(
            "twap_available_slice_count >= 2",
            name="ck_trade_intent_twap_params_twap_available_slice_count",
        ),
        sa.CheckConstraint(
            "twap_materialized_slice_count >= 2 AND twap_materialized_slice_count <= 200",
            name="ck_trade_intent_twap_params_twap_materialized_slice_count",
        ),
        sa.ForeignKeyConstraint(
            ["trade_intent_id"],
            ["trade_intent_core.id"],
            name="fk_trade_intent_twap_params_intent",
        ),
        sa.PrimaryKeyConstraint("trade_intent_id", name="pk_trade_intent_twap_params"),
    )


def downgrade() -> None:
    op.drop_table("trade_intent_twap_params")
    op.drop_table("trade_intent_trailing_params")
    op.drop_table("trade_intent_price_params")
    op.drop_index("ix_trade_intent_core_active_symbols", table_name="trade_intent_core")
    op.drop_index("uq_trade_intent_core_active_twap_duplicate", table_name="trade_intent_core")
    op.drop_index("uq_trade_intent_core_active_duplicate", table_name="trade_intent_core")
    op.drop_index("ix_trade_intent_core_symbol_status_trading_date", table_name="trade_intent_core")
    op.drop_index("ix_trade_intent_core_owner_status_trading_date", table_name="trade_intent_core")
    op.drop_table("trade_intent_core")
