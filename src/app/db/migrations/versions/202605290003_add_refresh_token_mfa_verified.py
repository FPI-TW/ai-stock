"""add mfa_verified to refresh_tokens

Lets an admin's verified-2FA state survive access-token refresh: 2FA verify marks
the session's refresh token, and rotation carries the flag to the successor.

Revision ID: 202605290003
Revises: 202605290002
Create Date: 2026-05-29 00:03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202605290003"
down_revision: str | None = "202605290002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column("mfa_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "mfa_verified")
