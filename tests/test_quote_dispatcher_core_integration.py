"""End-to-end integration test for robot #2 (TradeIntentCoreDispatcher) against real PostgreSQL.

建 active 單 → dispatch 一筆到價報價 → 驗證新軌觸發下游（status / trigger row / notification）。
"""

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Notification, Symbol
from app.db.models.trade_intent_core import (
    TradeIntentCore,
    TradeIntentPriceParams,
    TradeIntentTrailingParams,
    TradeIntentTrigger,
    TradeIntentTwapParams,
)
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.quote.base import QuoteSnapshot
from app.services.quote_dispatcher_core import TradeIntentCoreDispatcher
from tests.db_helpers import ensure_user

TAIPEI = ZoneInfo("Asia/Taipei")
# 週二盤中（市場開盤），quote_time 與 now 對齊 → 通過交易時段 + 新鮮度檢查
NOW = datetime(2026, 5, 12, 10, 0, tzinfo=TAIPEI)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def int_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    with Session(engine) as session:
        session.add(
            Symbol(
                id=uuid4(),
                symbol="2330",
                display_name="台積電",
                market="TWSE",
                instrument_type="stock",
                tradable_status="tradable",
            )
        )
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def _wipe(session: Session) -> None:
    session.execute(delete(Notification))
    session.execute(delete(TradeIntentTrigger))
    session.execute(delete(TradeIntentPriceParams))
    session.execute(delete(TradeIntentTrailingParams))
    session.execute(delete(TradeIntentTwapParams))
    session.execute(delete(TradeIntentCore))
    session.commit()


@pytest.fixture
def int_engine_clean(int_engine: Engine) -> Generator[Engine]:
    with Session(int_engine) as s:
        _wipe(s)
    try:
        yield int_engine
    finally:
        with Session(int_engine) as s:
            _wipe(s)


def _dispatcher(engine: Engine) -> TradeIntentCoreDispatcher:
    session_service = TradingSessionService(clock=lambda: NOW)
    return TradeIntentCoreDispatcher(
        session_factory=lambda: Session(engine),
        evaluator=QuoteEvaluator(session_service),
        session_service=session_service,
    )


def _create_active_buy_alert(engine: Engine, owner: UUID, *, target: str = "600.0000") -> UUID:
    with Session(engine) as s:
        ensure_user(s, owner)
        repo = TradeIntentCoreRepository(s)
        intent_id = repo.create(
            owner_user_id=owner,
            symbol="2330",
            strategy="buy_price_alert",
            quantity_lots=1,
            target_price_original=Decimal(target),
            target_price_effective=Decimal(target),
            trigger_reference_price_type="ask",
            trading_date=date(2026, 5, 12),
            time_in_force="day",
            execution_mode="notify_only",
            status="active",
        )
        repo.commit()
    return intent_id


def _snapshot(ask: str) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="2330",
        bid_price=None,
        ask_price=Decimal(ask),
        last_price=None,
        quote_time=NOW,
        received_at=NOW,
    )


@pytest.mark.integration
def test_robot2_triggers_buy_alert_when_ask_meets_target(int_engine_clean: Engine) -> None:
    engine = int_engine_clean
    owner = uuid4()
    intent_id = _create_active_buy_alert(engine, owner, target="600.0000")

    # ask 590 <= 600 → 應觸發
    _dispatcher(engine).dispatch(_snapshot(ask="590.0000"))

    with Session(engine) as s:
        repo = TradeIntentCoreRepository(s)
        assert repo.find_by_id(intent_id, owner).status == "triggered"
        trigger = s.execute(
            select(TradeIntentTrigger).where(TradeIntentTrigger.trade_intent_id == intent_id)
        ).scalar_one()
        assert trigger.trigger_price == Decimal("590.0000")
        assert trigger.trigger_reference_price_type == "ask"
        note = s.execute(select(Notification).where(Notification.trade_intent_core_id == intent_id)).scalar_one()
        assert note.type == "price_triggered"


@pytest.mark.integration
def test_robot2_does_not_trigger_when_ask_above_target(int_engine_clean: Engine) -> None:
    engine = int_engine_clean
    owner = uuid4()
    intent_id = _create_active_buy_alert(engine, owner, target="600.0000")

    # ask 610 > 600 → 不觸發
    _dispatcher(engine).dispatch(_snapshot(ask="610.0000"))

    with Session(engine) as s:
        repo = TradeIntentCoreRepository(s)
        assert repo.find_by_id(intent_id, owner).status == "active"
        assert (
            s.execute(select(TradeIntentTrigger).where(TradeIntentTrigger.trade_intent_id == intent_id)).first() is None
        )
