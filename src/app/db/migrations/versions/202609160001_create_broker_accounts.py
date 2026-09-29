"""create broker_accounts (per-user broker login binding)

Revision ID: 202609160001
Revises: 202608190001
Create Date: 2026-09-16 00:00:01.000000

One row per user (unique user_id). Credentials are stored encrypted as one BYTEA
blob rather than per-broker columns; ``last_error`` only ever holds whitelisted
safe text. Downgrade drops the table outright — there is nothing to preserve.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202609160001"
down_revision: str | None = "202608190001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("broker", sa.Text(), nullable=False),
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("broker_account_no", sa.Text(), nullable=False),
        sa.Column("cert_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("broker IN ('fubon')", name=op.f("ck_broker_accounts_broker")),
        sa.CheckConstraint("status IN ('active', 'login_failed')", name=op.f("ck_broker_accounts_status")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_broker_accounts_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broker_accounts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_broker_accounts_user_id")),
    )


def downgrade() -> None:
    op.drop_table("broker_accounts")
