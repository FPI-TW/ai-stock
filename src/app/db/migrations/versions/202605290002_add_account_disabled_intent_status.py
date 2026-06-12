"""add cancelled_by_account_disabled trade intent status

Lets the L1 account-disable cascade mark a user's open intents distinctly from a
user-initiated cancel.

Revision ID: 202605290002
Revises: 202605290001
Create Date: 2026-05-29 00:02:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202605290002"
down_revision: str | None = "202605290001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP_STATUS_CONSTRAINT = """
DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'trade_intents'
          AND c.contype = 'c'
          AND c.conname IN ('ck_trade_intents_status', 'ck_trade_intents_ck_trade_intents_status')
    LOOP
        EXECUTE format('ALTER TABLE trade_intents DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    op.execute(_DROP_STATUS_CONSTRAINT)
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_status
        CHECK (status IN ('scheduled', 'active', 'triggered', 'expired', 'cancelled', 'cancelled_by_account_disabled'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM trade_intents WHERE status = 'cancelled_by_account_disabled') THEN
                RAISE EXCEPTION 'Downgrade 202605290002 blocked: cancelled_by_account_disabled rows exist.';
            END IF;
        END
        $$;
        """
    )
    op.execute(_DROP_STATUS_CONSTRAINT)
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_status
        CHECK (status IN ('scheduled', 'active', 'triggered', 'expired', 'cancelled'))
        """
    )
