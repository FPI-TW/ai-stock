"""Unit tests for create() 的策略↔參數一致性守衛。

拆衛星表後，舊軌 target_price_presence / trailing_fields_presence 兩條跨欄 CHECK 無單表
對應；改由 create() 在 DB 存取前擋下錯配參數。守衛在碰 self._db 之前就 raise，故不需
真 DB 即可測。"""

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository


def _create(strategy: str, **overrides: object) -> None:
    repo = TradeIntentCoreRepository(cast(Session, None))  # guard raises before touching the session
    kwargs: dict[str, object] = {
        "owner_user_id": uuid4(),
        "symbol": "2330",
        "strategy": strategy,
        "quantity_lots": 1,
        "target_price_original": None,
        "target_price_effective": None,
        "trigger_reference_price_type": "last_price",
        "trading_date": date(2026, 7, 14),
        "time_in_force": "day",
        "execution_mode": "notify_only",
        "status": "active",
    }
    kwargs.update(overrides)
    repo.create(**kwargs)  # type: ignore[arg-type]


def test_price_strategy_with_trailing_params_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not carry trailing params"):
        _create(
            "buy_price_alert",
            target_price_effective=Decimal("600"),
            target_price_original=Decimal("600"),
            trail_mode="percentage",
            trail_value=Decimal("5"),
        )


def test_trailing_without_trail_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires trail_mode and trail_value"):
        _create("trailing_stop_alert", trail_mode="percentage", trail_value=None)


def test_trailing_with_target_price_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not carry a target price"):
        _create(
            "trailing_stop_alert",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            target_price_effective=Decimal("600"),
        )


def test_price_strategy_without_target_price_is_rejected() -> None:
    # 這正是會寫出 dedup_key='' 壞列的情形。
    with pytest.raises(ValueError, match="requires a target price"):
        _create("buy_price_alert", target_price_effective=None)


def test_twap_is_rejected() -> None:
    # create() docstring 自述只建非 TWAP；TWAP 落到這裡會以 dedup_key='' 無衛星入庫。
    with pytest.raises(ValueError, match="create_twap"):
        _create("twap_order")
