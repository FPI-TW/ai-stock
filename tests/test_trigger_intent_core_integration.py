"""Integration tests for persist_core_trigger (新軌觸發 persist) against real PostgreSQL."""

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from app.commands.trigger_intent_core import CoreIntentNotActiveError, TriggerCoreInput, persist_core_trigger
from app.core.config import get_settings
from app.db.models.core import Notification, Symbol
from app.db.models.trade_intent_core import (
    TradeIntentCore,
    TradeIntentPriceParams,
    TradeIntentTrailingParams,
    TradeIntentTrigger,
    TradeIntentTwapParams,
)
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from tests.db_helpers import ensure_user

QUOTE_TIME = datetime(2026, 5, 12, 1, 30, tzinfo=UTC)


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
def db_session(int_engine: Engine) -> Generator[Session]:
    session = Session(int_engine)
    try:
        _wipe(session)
        yield session
    finally:
        _wipe(session)
        session.close()


@pytest.fixture
def repo(db_session: Session) -> TradeIntentCoreRepository:
    return TradeIntentCoreRepository(db_session)


def _input(intent_id: UUID, *, price: str = "600.0000", ref: str = "ask") -> TriggerCoreInput:
    return TriggerCoreInput(
        intent_id=intent_id,
        trigger_price=Decimal(price),
        trigger_reference_price_type=ref,
        fallback_used=False,
        quote_snapshot={"last_price": price},
        quote_time=QUOTE_TIME,
    )


def _create_active_price_alert(repo: TradeIntentCoreRepository, owner: UUID, *, price: str = "600.0000") -> UUID:
    ensure_user(repo._db, owner)
    intent_id = repo.create(
        owner_user_id=owner,
        symbol="2330",
        strategy="buy_price_alert",
        quantity_lots=1,
        target_price_original=Decimal(price),
        target_price_effective=Decimal(price),
        trigger_reference_price_type="ask",
        trading_date=date(2026, 5, 12),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
    )
    repo.commit()
    return intent_id


@pytest.mark.integration
def test_persist_price_alert_trigger(repo: TradeIntentCoreRepository, db_session: Session) -> None:
    owner = uuid4()
    intent_id = _create_active_price_alert(repo, owner, price="600.0000")
    intent = repo.find_by_id(intent_id, owner)

    persist_core_trigger(db_session, intent, _input(intent_id, price="601.0000"))
    db_session.commit()

    # 狀態轉 triggered
    assert repo.find_by_id(intent_id, owner).status == "triggered"
    # 觸發稽核寫進新表
    trigger = db_session.execute(
        select(TradeIntentTrigger).where(TradeIntentTrigger.trade_intent_id == intent_id)
    ).scalar_one()
    assert trigger.trigger_price == Decimal("601.0000")
    assert trigger.target_price_effective == Decimal("600.0000")
    # 通知寫進共用表，指向新軌（trade_intent_core_id），舊欄為空
    note = db_session.execute(select(Notification).where(Notification.trade_intent_core_id == intent_id)).scalar_one()
    assert note.type == "price_triggered"
    assert note.trade_intent_id is None
    assert note.owner_user_id == owner


@pytest.mark.integration
def test_persist_trigger_stale_guard(repo: TradeIntentCoreRepository, db_session: Session) -> None:
    owner = uuid4()
    intent_id = _create_active_price_alert(repo, owner)
    intent = repo.find_by_id(intent_id, owner)
    # 取消 → 已非 active
    repo.cancel(intent_id, owner)
    repo.commit()

    with pytest.raises(CoreIntentNotActiveError):
        persist_core_trigger(db_session, intent, _input(intent_id))


@pytest.mark.integration
def test_persist_trailing_trigger_records_baseline(repo: TradeIntentCoreRepository, db_session: Session) -> None:
    owner = uuid4()
    ensure_user(repo._db, owner)
    intent_id = repo.create(
        owner_user_id=owner,
        symbol="2330",
        strategy="trailing_stop_alert",
        quantity_lots=1,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="bid",
        trading_date=date(2026, 5, 12),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
        trail_mode="percentage",
        trail_value=Decimal("5"),
    )
    repo.commit()
    repo.system_update_trailing_baseline(
        intent_id,
        baseline=Decimal("610.0000"),
        dynamic_trigger_price=Decimal("579.5000"),
        baseline_updated_at=QUOTE_TIME,
    )
    repo.commit()
    intent = repo.find_by_id(intent_id, owner)

    persist_core_trigger(db_session, intent, _input(intent_id, price="579.0000", ref="bid"))
    db_session.commit()

    trigger = db_session.execute(
        select(TradeIntentTrigger).where(TradeIntentTrigger.trade_intent_id == intent_id)
    ).scalar_one()
    assert trigger.baseline_at_trigger == Decimal("610.0000")
    assert trigger.dynamic_trigger_price_at_trigger == Decimal("579.5000")
    note = db_session.execute(select(Notification).where(Notification.trade_intent_core_id == intent_id)).scalar_one()
    assert note.type == "trailing_stop_triggered"
