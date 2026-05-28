"""add expired trade intent status

Revision ID: 202605280002
Revises: 202605280001
Create Date: 2026-05-28 00:02:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202605280002"
down_revision: str | None = "202605280001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
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
                  AND c.conname IN (
                      'ck_trade_intents_status',
                      'ck_trade_intents_ck_trade_intents_status'
                  )
            LOOP
                EXECUTE format('ALTER TABLE trade_intents DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_status
        CHECK (status IN ('scheduled', 'active', 'triggered', 'expired', 'cancelled'))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM trade_intents WHERE status = 'expired') THEN
                RAISE EXCEPTION 'Cannot downgrade 202605280002: migrate or delete expired trade_intents first.';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
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
                  AND c.conname IN (
                      'ck_trade_intents_status',
                      'ck_trade_intents_ck_trade_intents_status'
                  )
            LOOP
                EXECUTE format('ALTER TABLE trade_intents DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE trade_intents
        ADD CONSTRAINT ck_trade_intents_status
        CHECK (status IN ('scheduled', 'active', 'triggered', 'cancelled'))
        """
    )
