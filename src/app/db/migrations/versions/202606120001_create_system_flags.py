"""create system_flags (L2 kill switch)

Revision ID: 202606120001
Revises: 202605290004
Create Date: 2026-06-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606120001"
down_revision: str | None = "202605290004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_flags",
        sa.Column("flag_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_system_flags_updated_by_users")),
        sa.PrimaryKeyConstraint("flag_key", name=op.f("pk_system_flags")),
    )


def downgrade() -> None:
    op.drop_table("system_flags")
