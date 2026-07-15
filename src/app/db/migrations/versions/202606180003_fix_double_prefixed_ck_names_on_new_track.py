"""rename 新軌四表被雙前綴＋截斷的 CHECK 約束名（202606180001 的修正）

202606180001 在 op.create_table 的 sa.CheckConstraint 傳了完整名（ck_<table>_...），
但 env.py 的 target_metadata 讓 alembic ops 繼承 naming convention，而 ck convention
`ck_%(table_name)s_%(constraint_name)s` 含 %(constraint_name)s token——明確給名也會
被再套模板，落到 DB 變雙前綴（ck_<table>_ck_<table>_...）且超過 63 字的被 SQLAlchemy
以 md5 hash 後綴截斷。結果與 model（傳裸名）產出的名字不一致：autogenerate 會噴
diff、想按名 drop 會找不到。

舊名的 hash 後綴是決定性的（同字串同演算法），所有跑過 202606180001 的環境名字
一致，可安全硬編。新名＝model 產出的正確名（實際 create_all 驗證，全部 ≤57 字）。

202606180002（同 PR、未合併）已原地改傳裸名，不在此列。舊軌 legacy 表
（symbols / trade_intents / twap_slices）同病但已凍結待退役，刻意不動。

Revision ID: 202606180003
Revises: 202606180002
Create Date: 2026-06-18 00:03:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202606180003"
down_revision: str | None = "202606180002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, 現況錯名, model 正確名)
_RENAMES: list[tuple[str, str, str]] = [
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_execution_mode",
        "ck_trade_intent_core_execution_mode",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_filled_quantity_lots",
        "ck_trade_intent_core_filled_quantity_lots",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_notification_mode",
        "ck_trade_intent_core_notification_mode",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_quantity_lots",
        "ck_trade_intent_core_quantity_lots",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_status",
        "ck_trade_intent_core_status",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_strategy",
        "ck_trade_intent_core_strategy",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_time_in_force",
        "ck_trade_intent_core_time_in_force",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_transaction_mode",
        "ck_trade_intent_core_transaction_mode",
    ),
    (
        "trade_intent_core",
        "ck_trade_intent_core_ck_trade_intent_core_trigger_refer_ef8a",
        "ck_trade_intent_core_trigger_reference_price_type",
    ),
    (
        "trade_intent_price_params",
        "ck_trade_intent_price_params_ck_trade_intent_price_para_7160",
        "ck_trade_intent_price_params_target_price_effective",
    ),
    (
        "trade_intent_price_params",
        "ck_trade_intent_price_params_ck_trade_intent_price_para_a03e",
        "ck_trade_intent_price_params_target_price_original",
    ),
    (
        "trade_intent_trailing_params",
        "ck_trade_intent_trailing_params_ck_trade_intent_trailin_24f4",
        "ck_trade_intent_trailing_params_trail_mode",
    ),
    (
        "trade_intent_trailing_params",
        "ck_trade_intent_trailing_params_ck_trade_intent_trailin_4084",
        "ck_trade_intent_trailing_params_trail_value_range",
    ),
    (
        "trade_intent_trailing_params",
        "ck_trade_intent_trailing_params_ck_trade_intent_trailin_ac12",
        "ck_trade_intent_trailing_params_dynamic_trigger_price",
    ),
    (
        "trade_intent_trailing_params",
        "ck_trade_intent_trailing_params_ck_trade_intent_trailin_bf6d",
        "ck_trade_intent_trailing_params_baseline_positive",
    ),
    (
        "trade_intent_twap_params",
        "ck_trade_intent_twap_params_ck_trade_intent_twap_params_1012",
        "ck_trade_intent_twap_params_twap_materialized_slice_count",
    ),
    (
        "trade_intent_twap_params",
        "ck_trade_intent_twap_params_ck_trade_intent_twap_params_a742",
        "ck_trade_intent_twap_params_position_side",
    ),
    (
        "trade_intent_twap_params",
        "ck_trade_intent_twap_params_ck_trade_intent_twap_params_ad4e",
        "ck_trade_intent_twap_params_twap_available_slice_count",
    ),
    (
        "trade_intent_twap_params",
        "ck_trade_intent_twap_params_ck_trade_intent_twap_params_e6b6",
        "ck_trade_intent_twap_params_twap_interval_seconds",
    ),
]


def upgrade() -> None:
    for table, old, new in _RENAMES:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{old}" TO "{new}"')


def downgrade() -> None:
    for table, old, new in _RENAMES:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{new}" TO "{old}"')
