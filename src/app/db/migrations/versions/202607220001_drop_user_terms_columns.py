"""drop users.terms_version_accepted / terms_accepted_at

商業模式改變後不再要求接受服務條款，移除這兩個欄位與相關讀寫。

Revision ID: 202607220001
Revises: 202606180003
Create Date: 2026-07-22 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220001"
down_revision: str | None = "202606180003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_version_accepted")


def downgrade() -> None:
    op.add_column("users", sa.Column("terms_version_accepted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
