"""Integration tests for TradeIntentCoreRepository (新軌) against real PostgreSQL.

驗證新核心表 + 衛星表的寫/讀/取消最小迴圈，以及 dedup_key 去重在 DB 層的等價語意。
"""

from collections.abc import Generator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.commands.twap import TwapSliceWorkerCommand
from app.core.config import get_settings
from app.db.models.core import Notification, Symbol
from app.db.models.trade_intent_core import (
    TradeIntentCore,
    TradeIntentPriceParams,
    TradeIntentTrailingParams,
    TradeIntentTwapParams,
    TradeIntentTwapSlice,
)
from app.domain.price import SecurityType
from app.domain.trade_intent import (
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
)
from app.domain.trading_session import TradingDayPhase, TradingSessionService
from app.domain.twap import TwapDuplicateActivePlanError, TwapPlan, TwapSlicePlan
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.quote.base import QuoteSnapshot
from app.services.quote.in_memory import InMemoryQuoteProvider
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
    # 切片先於通知：切片持有 notifications 的 FK
    session.execute(delete(TradeIntentTwapSlice))
    session.execute(delete(Notification))
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


# ----------------------------------------------------------------------
# TWAP（PR4）：核心 + twap 衛星 + 切片子表
# ----------------------------------------------------------------------


def _twap_plan(
    *,
    position_side: str = "long",
    quantity_lots: int = 2,
    trading_date: date = date(2026, 5, 12),
) -> TwapPlan:
    start_at = datetime(2026, 5, 12, 1, 0, tzinfo=UTC)
    return TwapPlan(
        position_side=position_side,
        trading_phase=TradingDayPhase.REGULAR_SESSION,
        trading_date=trading_date,
        requested_start_time=time(9, 0),
        start_at=start_at,
        requested_end_time=time(9, 5),
        end_at=start_at + timedelta(minutes=5),
        interval_seconds=300,
        target_quantity_lots=quantity_lots,
        available_slice_count=2,
        materialized_slice_count=2,
        slices=tuple(
            TwapSlicePlan(
                sequence_no=seq,
                scheduled_at=start_at + timedelta(minutes=5 * (seq - 1)),
                planned_quantity_lots=1,
            )
            for seq in (1, 2)
        ),
    )


def _create_twap(
    repo: TradeIntentCoreRepository,
    *,
    owner_user_id: UUID,
    position_side: str = "long",
    quantity_lots: int = 2,
    trading_date: date = date(2026, 5, 12),
    status: str = "active",
) -> UUID:
    ensure_user(repo._db, owner_user_id)
    intent_id = repo.create_twap(
        owner_user_id=owner_user_id,
        symbol="2330",
        twap_plan=_twap_plan(position_side=position_side, quantity_lots=quantity_lots, trading_date=trading_date),
        execution_mode="notify_only",
        time_in_force="day",
        status=status,
        trigger_reference_price_type="last_fallback",
    )
    repo.commit()
    return intent_id


@pytest.mark.integration
def test_create_twap_writes_core_params_and_slices(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_twap(repo, owner_user_id=owner)

    intent = repo.find_by_id(intent_id, owner)
    assert intent.strategy == "twap_order"
    assert intent.position_side == "long"
    assert intent.quantity_lots == 2
    assert intent.twap_interval_seconds == 300
    assert intent.twap_materialized_slice_count == 2
    # 非 TWAP 的衛星欄維持 None
    assert intent.target_price_effective is None
    assert intent.trail_mode is None

    slices = repo.list_twap_slices(intent_id, owner)
    assert [s.sequence_no for s in slices] == [1, 2]
    assert all(s.status == "pending" for s in slices)
    assert all(s.planned_quantity_lots == 1 for s in slices)


@pytest.mark.integration
def test_create_twap_same_side_same_day_blocked(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    _create_twap(repo, owner_user_id=owner, position_side="long")

    with pytest.raises(TwapDuplicateActivePlanError):
        _create_twap(repo, owner_user_id=owner, position_side="long")


@pytest.mark.integration
def test_create_twap_long_and_short_same_quantity_same_day_allowed(repo: TradeIntentCoreRepository) -> None:
    # 組長 2026-07-14 定案：做多／做空是方向相反的兩個意圖，同 qty 同日不算重複。
    # 新軌 TWAP 唯一索引只看 position_side（不含 quantity_lots），故兩張並存。
    owner = uuid4()
    long_id = _create_twap(repo, owner_user_id=owner, position_side="long", quantity_lots=2)
    short_id = _create_twap(repo, owner_user_id=owner, position_side="short", quantity_lots=2)

    assert repo.find_by_id(long_id, owner).position_side == "long"
    assert repo.find_by_id(short_id, owner).position_side == "short"


@pytest.mark.integration
def test_create_twap_duplicate_blocked_at_db_level_on_precheck_miss(repo: TradeIntentCoreRepository) -> None:
    # 併發下前置 SELECT 可能雙雙落空 → 唯一索引是最後防線（繞過 pre-check 直接插）。
    owner = uuid4()
    _create_twap(repo, owner_user_id=owner, position_side="long")

    repo._db.add(
        TradeIntentCore(
            id=uuid4(),
            owner_user_id=owner,
            symbol="2330",
            strategy="twap_order",
            execution_mode="notify_only",
            quantity_lots=5,
            trigger_reference_price_type="last_fallback",
            trading_date=date(2026, 5, 12),
            time_in_force="day",
            status="active",
            dedup_key="long",
        )
    )
    with pytest.raises(IntegrityError):
        repo._db.flush()
    repo._db.rollback()


@pytest.mark.integration
def test_cancel_twap_cascades_pending_slices(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_twap(repo, owner_user_id=owner)
    # 第 1 片已通知並在等補價；只有未發出的第 2 片該被取消。
    notified = repo._db.execute(
        select(TradeIntentTwapSlice).where(
            TradeIntentTwapSlice.trade_intent_id == intent_id,
            TradeIntentTwapSlice.sequence_no == 1,
        )
    ).scalar_one()
    notified.status = "notified"
    notified.price_followup_required = True
    notified.next_price_followup_at = datetime(2026, 5, 12, 1, 0, tzinfo=UTC)
    repo.commit()

    repo.cancel(intent_id, owner)
    repo.commit()

    by_seq = {s.sequence_no: s for s in repo.list_twap_slices(intent_id, owner)}
    assert by_seq[1].status == "notified"
    assert by_seq[1].price_followup_required is False
    assert by_seq[1].next_price_followup_at is None
    assert by_seq[2].status == "cancelled"


@pytest.mark.integration
def test_expire_twap_cascades_pending_slices(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    now = datetime(2026, 5, 13, 2, 0, tzinfo=UTC)
    intent_id = _create_twap(repo, owner_user_id=owner, trading_date=date(2026, 5, 12))

    assert repo.system_expire_day_intents_through(date(2026, 5, 12), now) == 1
    repo.commit()

    assert repo.find_by_id(intent_id, owner).status == "expired"
    assert [s.status for s in repo.list_twap_slices(intent_id, owner)] == ["cancelled", "cancelled"]


@pytest.mark.integration
def test_list_twap_slices_wrong_owner_raises(repo: TradeIntentCoreRepository) -> None:
    owner = uuid4()
    intent_id = _create_twap(repo, owner_user_id=owner)

    with pytest.raises(IntentNotFoundError):
        repo.list_twap_slices(intent_id, uuid4())


@pytest.mark.integration
def test_twap_worker_notifies_due_slices_against_real_tables(
    repo: TradeIntentCoreRepository,
    db_session: Session,
) -> None:
    """worker 的 loader 是 切片×核心×TWAP 參數 的三表 JOIN + FOR UPDATE OF——單元測試
    餵假 session 驗不到 SQL 本身，故在真 PG 上跑一輪：兩片都到期 → 兩則通知、委託轉 triggered。"""

    owner = uuid4()
    intent_id = _create_twap(repo, owner_user_id=owner)
    now = datetime(2026, 5, 12, 2, 0, tzinfo=UTC)  # 10:00 台北，盤中
    provider = InMemoryQuoteProvider()
    provider.push_quote(
        QuoteSnapshot(
            symbol="2330",
            bid_price=Decimal("589"),
            ask_price=Decimal("591"),
            last_price=Decimal("590"),
            quote_time=now,
            received_at=now,
        )
    )

    output = TwapSliceWorkerCommand(
        db_session,
        provider,
        TradingSessionService(clock=lambda: now),
    ).process_due_slices()

    assert output.processed_count == 2
    slices = repo.list_twap_slices(intent_id, owner)
    assert [s.status for s in slices] == ["notified", "notified"]
    assert all(s.primary_reference_price == Decimal("591.0000") for s in slices)  # long → ask
    assert repo.find_by_id(intent_id, owner).status == "triggered"

    notifications = db_session.execute(select(Notification).order_by(Notification.created_at)).scalars().all()
    assert len(notifications) == 2
    # 新軌通知掛 trade_intent_core_id（舊欄留空），否則會撞 notifications 的 num_nonnulls CHECK
    assert all(n.trade_intent_core_id == intent_id and n.trade_intent_id is None for n in notifications)
