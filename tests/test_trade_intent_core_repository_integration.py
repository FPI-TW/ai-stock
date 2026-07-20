"""Integration tests for TradeIntentCoreRepository (新軌) against real PostgreSQL.

驗證新核心表 + 衛星表的寫/讀/取消最小迴圈，以及 dedup_key 去重在 DB 層的等價語意。
"""

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Symbol
from app.db.models.trade_intent_core import (
    TradeIntentCore,
    TradeIntentPriceParams,
    TradeIntentTrailingParams,
    TradeIntentTwapParams,
)
from app.domain.price import SecurityType
from app.domain.trade_intent import (
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
)
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from tests.db_helpers import ensure_user


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


def _create_price_alert(
    repo: TradeIntentCoreRepository,
    *,
    owner_user_id: UUID,
    target_price: str = "600.0000",
    quantity_lots: int = 1,
    trading_date: date | None = None,
    status: str = "active",
    symbol: str = "2330",
) -> UUID:
    ensure_user(repo._db, owner_user_id)
    intent_id = repo.create(
        owner_user_id=owner_user_id,
        symbol=symbol,
        strategy="buy_price_alert",
        quantity_lots=quantity_lots,
        target_price_original=Decimal(target_price),
        target_price_effective=Decimal(target_price),
        trigger_reference_price_type="ask",
        trading_date=trading_date or date(2026, 5, 12),
        time_in_force="day",
        execution_mode="notify_only",
        status=status,
    )
    repo.commit()
    return intent_id


@pytest.mark.integration
def test_create_price_alert_assembles_satellite(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")

    intent = repo.find_by_id(intent_id, owner)
    assert intent.strategy == "buy_price_alert"
    assert intent.target_price_original == Decimal("600.0000")
    assert intent.target_price_effective == Decimal("600.0000")
    assert intent.security_type == SecurityType.STOCK
    # 非該策略的衛星欄維持 None
    assert intent.trail_mode is None
    assert intent.position_side is None


@pytest.mark.integration
def test_create_trailing_assembles_trailing_satellite(repo: TradeIntentCoreRepository) -> None:
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

    intent = repo.find_by_id(intent_id, owner)
    assert intent.trail_mode == "percentage"
    assert intent.trail_value == Decimal("5.0000")
    assert intent.baseline is None
    assert intent.target_price_effective is None


@pytest.mark.integration
def test_duplicate_same_logical_order_blocked(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")

    # 數值相等但 scale 不同 → dedup_key 相同 → 仍被擋（pre-check）
    with pytest.raises(DuplicateIntentError):
        _create_price_alert(repo, owner_user_id=owner, target_price="600")


@pytest.mark.integration
def test_duplicate_blocked_at_db_level_on_concurrent_precheck_miss(repo: TradeIntentCoreRepository) -> None:
    # 直接驗 DB 唯一索引：繞過 pre-check（手動塞兩筆同 dedup_key 的 active 核心列）→ 第二筆 flush 應違反唯一索引
    owner = uuid4()
    ensure_user(repo._db, owner)
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    repo._db.add(
        TradeIntentCore(
            id=uuid4(),
            owner_user_id=owner,
            symbol="2330",
            strategy="buy_price_alert",
            execution_mode="notify_only",
            quantity_lots=1,
            trigger_reference_price_type="ask",
            trading_date=date(2026, 5, 12),
            time_in_force="day",
            status="active",
            dedup_key="600.00",
        )
    )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        repo._db.flush()
    repo._db.rollback()


@pytest.mark.integration
def test_different_price_allowed(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    # 不同價格 → 不同 dedup_key → 放行
    second = _create_price_alert(repo, owner_user_id=owner, target_price="620.0000")
    assert repo.find_by_id(second, owner).target_price_effective == Decimal("620.0000")


@pytest.mark.integration
def test_cancel_sets_status_and_is_idempotent(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_price_alert(repo, owner_user_id=owner)

    cancelled = repo.cancel(intent_id, owner)
    repo.commit()
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None

    again = repo.cancel(intent_id, owner)
    assert again.status == "cancelled"


@pytest.mark.integration
def test_cancel_terminal_raises(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_price_alert(repo, owner_user_id=owner, status="triggered")
    with pytest.raises(CancelNotAllowedError):
        repo.cancel(intent_id, owner)


@pytest.mark.integration
def test_find_by_id_wrong_owner_raises(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_price_alert(repo, owner_user_id=owner)
    with pytest.raises(IntentNotFoundError):
        repo.find_by_id(intent_id, uuid4())


# --- 保留的查詢方法（§15 limits + robot #2 用）-----------------------------------


@pytest.mark.integration
def test_counts_active_or_scheduled(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    other = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    _create_price_alert(repo, owner_user_id=owner, target_price="620.0000")
    _create_price_alert(repo, owner_user_id=other, target_price="600.0000")

    assert repo.count_active_or_scheduled_for_user(owner) == 2
    assert repo.count_active_or_scheduled_for_user_symbol(owner, "2330") == 2
    assert repo.count_active_or_scheduled_for_user(other) == 1


@pytest.mark.integration
def test_system_list_active(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    _create_price_alert(repo, owner_user_id=owner, target_price="620.0000")

    assert repo.system_list_active_symbols() == ["2330"]
    rows = repo.system_list_active_by_symbols(["2330"])
    assert len(rows) == 2
    # 確認衛星參數有被組裝回來（owner-agnostic 路徑）
    assert {r.target_price_effective for r in rows} == {Decimal("600.0000"), Decimal("620.0000")}
    assert repo.system_list_active_by_symbols([]) == []


@pytest.mark.integration
def test_system_update_trailing_baseline(repo: TradeIntentCoreRepository) -> None:
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

    now = datetime(2026, 5, 12, 1, 30, tzinfo=UTC)
    repo.system_update_trailing_baseline(
        intent_id,
        baseline=Decimal("610.0000"),
        dynamic_trigger_price=Decimal("579.5000"),
        baseline_updated_at=now,
    )
    repo.commit()

    intent = repo.find_by_id(intent_id, owner)
    assert intent.baseline == Decimal("610.0000")
    assert intent.dynamic_trigger_price == Decimal("579.5000")
    assert intent.baseline_updated_at == now


@pytest.mark.integration
def test_system_update_trailing_baseline_raises_for_non_trailing_intent(repo: TradeIntentCoreRepository) -> None:
    # 打到沒有 trailing 衛星列的 intent（如 price alert）應 fail loud，而非靜默 no-op。
    owner = uuid4()
    intent_id = _create_price_alert(repo, owner_user_id=owner)
    repo.commit()

    with pytest.raises(ValueError, match="no trailing params row"):
        repo.system_update_trailing_baseline(
            intent_id,
            baseline=Decimal("610.0000"),
            dynamic_trigger_price=Decimal("579.5000"),
            baseline_updated_at=datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
        )


@pytest.mark.integration
def test_active_or_scheduled_symbols(repo: TradeIntentCoreRepository) -> None:
    repo._db.add(
        Symbol(
            id=uuid4(),
            symbol="2454",
            display_name="聯發科",
            market="TWSE",
            instrument_type="stock",
            tradable_status="tradable",
        )
    )
    owner = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    _create_price_alert(repo, owner_user_id=owner, target_price="620.0000")  # 同 symbol，distinct
    _create_price_alert(repo, owner_user_id=owner, target_price="900.0000", symbol="2454")  # 跨 symbol 都要出現
    cancelled = _create_price_alert(repo, owner_user_id=owner, target_price="640.0000")
    repo.cancel(cancelled, owner)
    repo.commit()

    # 只回非終態（active/scheduled）的 distinct symbol；已取消的不算
    assert repo.active_or_scheduled_symbols() == {"2330", "2454"}


# ------------------------------------------------------------------
# T1 PR3 cutover 補上的方法
# ------------------------------------------------------------------


@pytest.mark.integration
def test_count_active_or_scheduled_for_symbol_is_owner_agnostic(repo: TradeIntentCoreRepository) -> None:
    """退訂決策看的是「還有沒有人要這檔」，不是「這個人還要不要」。"""

    owner = uuid4()
    other = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    other_intent = _create_price_alert(repo, owner_user_id=other, target_price="620.0000")

    assert repo.count_active_or_scheduled_for_symbol("2330") == 2

    repo.cancel(other_intent, other)
    repo.commit()
    assert repo.count_active_or_scheduled_for_symbol("2330") == 1


@pytest.mark.integration
def test_list_by_owner_scopes_to_owner_and_paginates(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    other = uuid4()
    for price in ("600.0000", "620.0000", "640.0000"):
        _create_price_alert(repo, owner_user_id=owner, target_price=price)
    _create_price_alert(repo, owner_user_id=other, target_price="600.0000")

    page1, cursor = repo.list_by_owner(owner, statuses=None, trading_date=None, cursor=None, page_size=2)
    assert len(page1) == 2
    assert cursor is not None
    # 衛星參數要跟著組回來，否則清單的 targetPrice 會整排是 null
    assert all(item.target_price_effective is not None for item in page1)

    page2, cursor2 = repo.list_by_owner(owner, statuses=None, trading_date=None, cursor=cursor, page_size=2)
    assert len(page2) == 1
    assert cursor2 is None
    ids = {item.id for item in page1 + page2}
    assert len(ids) == 3  # 三筆不重不漏，且不含 other 的單


@pytest.mark.integration
def test_list_by_owner_filters_by_status_and_trading_date(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    active_id = _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    cancelled_id = _create_price_alert(repo, owner_user_id=owner, target_price="620.0000")
    repo.cancel(cancelled_id, owner)
    repo.commit()
    other_day = _create_price_alert(repo, owner_user_id=owner, target_price="640.0000", trading_date=date(2026, 5, 13))

    active_only, _ = repo.list_by_owner(owner, statuses=["active"], trading_date=None, cursor=None, page_size=50)
    assert {i.id for i in active_only} == {active_id, other_day}

    # 純終態查詢走另一組排序（updated_at desc），確認它也能正常回列
    terminal_only, _ = repo.list_by_owner(owner, statuses=["cancelled"], trading_date=None, cursor=None, page_size=50)
    assert [i.id for i in terminal_only] == [cancelled_id]

    by_day, _ = repo.list_by_owner(owner, statuses=None, trading_date=date(2026, 5, 13), cursor=None, page_size=50)
    assert [i.id for i in by_day] == [other_day]


@pytest.mark.integration
def test_list_by_owner_rejects_foreign_cursor(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    other = uuid4()
    _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    foreign = _create_price_alert(repo, owner_user_id=other, target_price="600.0000")

    with pytest.raises(InvalidCursorError):
        repo.list_by_owner(owner, statuses=None, trading_date=None, cursor=str(foreign), page_size=50)


@pytest.mark.integration
def test_lifecycle_activates_scheduled_and_expires_past_days(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    now = datetime(2026, 5, 12, 2, 0, tzinfo=UTC)
    scheduled_id = _create_price_alert(
        repo, owner_user_id=owner, target_price="600.0000", trading_date=date(2026, 5, 12), status="scheduled"
    )
    stale_id = _create_price_alert(repo, owner_user_id=owner, target_price="620.0000", trading_date=date(2026, 5, 11))

    assert repo.system_activate_scheduled_day_intents(date(2026, 5, 12), now) == 1
    repo.commit()
    assert repo.find_by_id(scheduled_id, owner).status == "active"

    assert repo.system_expire_day_intents_through(date(2026, 5, 11), now) == 1
    repo.commit()
    assert repo.find_by_id(stale_id, owner).status == "expired"
    # 當日單不該被掃到
    assert repo.find_by_id(scheduled_id, owner).status == "active"


@pytest.mark.integration
def test_cancel_active_for_owner_only_touches_that_owner(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    other = uuid4()
    now = datetime(2026, 5, 12, 2, 0, tzinfo=UTC)
    mine = _create_price_alert(repo, owner_user_id=owner, target_price="600.0000")
    already_cancelled = _create_price_alert(repo, owner_user_id=owner, target_price="620.0000")
    repo.cancel(already_cancelled, owner)
    repo.commit()
    theirs = _create_price_alert(repo, owner_user_id=other, target_price="600.0000")

    # 已終態的不再重算，故只有 1 筆
    assert repo.cancel_active_for_owner(owner, status="cancelled_by_account_disabled", now=now) == 1
    repo.commit()

    assert repo.find_by_id(mine, owner).status == "cancelled_by_account_disabled"
    assert repo.find_by_id(already_cancelled, owner).status == "cancelled"
    assert repo.find_by_id(theirs, other).status == "active"
