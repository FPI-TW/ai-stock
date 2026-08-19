"""create the shared Telegram intent interaction state

Revision ID: 202608190001
Revises: 202607220001
Create Date: 2026-08-19 00:00:01.000000

The interaction is deliberately linked to ``trade_intent_core``.  The legacy
``trade_intents`` table is frozen and must not be referenced by this workflow.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608190001"
down_revision: str | None = "202607220001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_intent_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_chat_id", sa.Text(), nullable=False),
        sa.Column("bot_message_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("clarification_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_trade_intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('clarification', 'draft')",
            name=op.f("ck_telegram_intent_interactions_telegram_intent_interaction_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirming', 'confirmed', 'cancelled', 'expired', 'superseded')",
            name=op.f("ck_telegram_intent_interactions_telegram_intent_interaction_status"),
        ),
        sa.CheckConstraint(
            "capability_id IN ('limit_buy', 'limit_sell', 'market_buy', 'market_sell')",
            name=op.f("ck_telegram_intent_interactions_telegram_intent_interaction_capability"),
        ),
        sa.CheckConstraint(
            "strategy IN ('limit_buy_order', 'limit_sell_order', 'market_buy_order', 'market_sell_order')",
            name=op.f("ck_telegram_intent_interactions_telegram_intent_interaction_strategy"),
        ),
        sa.CheckConstraint(
            "clarification_attempt_count >= 0",
            name=op.f("ck_telegram_intent_interactions_telegram_intent_interaction_attempt_count"),
        ),
        sa.ForeignKeyConstraint(
            ["created_trade_intent_id"],
            ["trade_intent_core.id"],
            name=op.f("fk_telegram_intent_interactions_created_trade_intent_id_trade_intent_core"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_telegram_intent_interactions_owner_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_intent_interactions")),
    )
    op.create_index(
        "uq_telegram_intent_interactions_chat_pending",
        "telegram_intent_interactions",
        ["telegram_chat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'confirming')"),
    )
    op.create_index(
        "ix_telegram_intent_interactions_chat_status",
        "telegram_intent_interactions",
        ["telegram_chat_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_intent_interactions_expires_at",
        "telegram_intent_interactions",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_intent_interactions_expires_at", table_name="telegram_intent_interactions")
    op.drop_index("ix_telegram_intent_interactions_chat_status", table_name="telegram_intent_interactions")
    op.drop_index(
        "uq_telegram_intent_interactions_chat_pending",
        table_name="telegram_intent_interactions",
        postgresql_where=sa.text("status IN ('pending', 'confirming')"),
    )
    op.drop_table("telegram_intent_interactions")
