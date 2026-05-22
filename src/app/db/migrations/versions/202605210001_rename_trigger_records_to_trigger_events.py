"""rename trigger_records to trigger_events + triggered_at server_default

Revision ID: 202605210001
Revises: 202605120001
Create Date: 2026-05-21 00:01:00

Two changes folded into one migration because they share the same motivation
— bringing the V0.5 schema in line with the V1 spec (§16 names the table
`TriggerEvent`; §9 expects DB-side trigger timestamps):

1. Rename `trigger_records` → `trigger_events`.
   - PK / UNIQUE / FK use plain `RENAME CONSTRAINT` — names did not collide
     with the naming-convention's `ck_%(table_name)s_%(constraint_name)s`
     bug (see #2 below).
   - CHECK constraints are dropped and re-added instead. The baseline
     `ck_*` names end up doubled by the naming convention
     (`ck_<table>_ck_<table>_<short>`) and one of them gets PostgreSQL's
     truncation hash applied — that hash is keyed off the source string,
     so a literal `RENAME CONSTRAINT` would need different hashes on the
     source and target sides. Dropping and re-adding is both simpler and
     fixes the doubled-prefix accident at the same time. The replacement
     names use the cleaner `ck_<table>_<short>` form.

2. Set `trigger_events.triggered_at` `DEFAULT now()` so writes inside the
   same DB transaction get the same value as `trade_intents.triggered_at`
   (both come from `func.now()` which is transaction-start in PostgreSQL).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202605210001"
down_revision: str | None = "202605120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SIMPLE_RENAMES = [
    ("pk_trigger_records", "pk_trigger_events"),
    ("uq_trigger_records_trade_intent_id", "uq_trigger_events_trade_intent_id"),
    ("fk_trigger_records_trade_intent_id_trade_intents", "fk_trigger_events_trade_intent_id_trade_intents"),
    ("fk_trigger_records_symbol_symbols", "fk_trigger_events_symbol_symbols"),
]

# (old_doubled_name, new_clean_name, check_expression). old_doubled_name is the
# real PG name produced by the baseline migration after the naming convention
# doubled the prefix; the trigger_reference one was further truncated by
# SQLAlchemy with a 4-char hash suffix.
_CHECK_RECREATES = [
    (
        "ck_trigger_records_ck_trigger_records_target_price_effective",
        "ck_trigger_events_target_price_effective",
        "target_price_effective > 0",
    ),
    (
        "ck_trigger_records_ck_trigger_records_trigger_price",
        "ck_trigger_events_trigger_price",
        "trigger_price > 0",
    ),
    (
        "ck_trigger_records_ck_trigger_records_trigger_reference_7113",
        "ck_trigger_events_trigger_reference_price_type",
        "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
    ),
    (
        "ck_trigger_records_ck_trigger_records_fallback_consistency",
        "ck_trigger_events_fallback_consistency",
        "((trigger_reference_price_type = 'last_fallback' AND fallback_used = true) "
        "OR (trigger_reference_price_type IN ('ask', 'bid') AND fallback_used = false))",
    ),
]


def upgrade() -> None:
    op.rename_table("trigger_records", "trigger_events")
    for old, new in _SIMPLE_RENAMES:
        op.execute(f'ALTER TABLE trigger_events RENAME CONSTRAINT "{old}" TO "{new}"')
    for old_name, new_name, expr in _CHECK_RECREATES:
        op.execute(f'ALTER TABLE trigger_events DROP CONSTRAINT "{old_name}"')
        op.execute(f'ALTER TABLE trigger_events ADD CONSTRAINT "{new_name}" CHECK ({expr})')
    op.execute("ALTER TABLE trigger_events ALTER COLUMN triggered_at SET DEFAULT now()")


def downgrade() -> None:
    op.execute("ALTER TABLE trigger_events ALTER COLUMN triggered_at DROP DEFAULT")
    for old_name, new_name, expr in _CHECK_RECREATES:
        op.execute(f'ALTER TABLE trigger_events DROP CONSTRAINT "{new_name}"')
        op.execute(f'ALTER TABLE trigger_events ADD CONSTRAINT "{old_name}" CHECK ({expr})')
    for old, new in _SIMPLE_RENAMES:
        op.execute(f'ALTER TABLE trigger_events RENAME CONSTRAINT "{new}" TO "{old}"')
    op.rename_table("trigger_events", "trigger_records")
