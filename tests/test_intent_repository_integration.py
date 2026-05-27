"""Integration tests for IntentRepository against a real PostgreSQL database."""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Symbol, TradeIntent
from app.domain.trade_intent import (
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
    TradeIntentData,
)
from app.repositories.intent_repository import IntentRepository


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


@pytest.fixture
def db_session(int_engine: Engine) -> Generator[Session]:
    session = Session(int_engine)
    try:
        # Each test starts with a clean intent table. `_create` now commits to give
        # `func.now()` distinct timestamps per row, which means data persists across
        # tests unless we wipe it here. Pre-existing per-owner tests are unaffected.
        session.execute(delete(TradeIntent))
        session.commit()
        yield session
    finally:
        # Rollback any failed transaction left over from a test (e.g. trailing
        # duplicate hits the partial unique index via IntegrityError; repo
        # re-raises as DuplicateIntentError but per its contract doesn't roll
        # back — the production caller (CreateTradeIntentCommand) does).
        session.rollback()
        session.execute(delete(TradeIntent))
        session.commit()
        session.close()


@pytest.fixture
def repo(db_session: Session) -> IntentRepository:
    return IntentRepository(db_session)


def _create(
    repo: IntentRepository,
    *,
    owner_user_id: UUID,
    symbol: str = "2330",
    strategy: str = "buy_price_alert",
    quantity_lots: int = 1,
    target_price: str = "100.0000",
    trading_date: date | None = None,
    status: str = "active",
) -> TradeIntentData:
    intent_id = repo.create(
        owner_user_id=owner_user_id,
        symbol=symbol,
        strategy=strategy,
        quantity_lots=quantity_lots,
        target_price_original=Decimal(target_price),
        target_price_effective=Decimal(target_price),
        trigger_reference_price_type="ask" if "buy" in strategy else "bid",
        trading_date=trading_date or date(2026, 5, 12),
        time_in_force="day",
        execution_mode="notify_only",
        status=status,
    )
    # Mimic the production command's commit boundary so server-side `func.now()`
    # values resolve per-statement instead of all sharing one transaction timestamp.
    # Tests that exercise ordering by `created_at` / `updated_at` rely on this.
    repo._db.commit()  # noqa: SLF001
    # Materialise after commit, matching `CreateTradeIntentCommand.execute`.
    return repo.find_by_id(intent_id, owner_user_id)


@pytest.mark.integration
def test_find_by_id_wrong_owner_raises_not_found(repo: IntentRepository) -> None:
    owner = uuid4()
    other = uuid4()
    intent = _create(repo, owner_user_id=owner)

    with pytest.raises(IntentNotFoundError):
        repo.find_by_id(intent.id, other)


@pytest.mark.integration
def test_cancel_wrong_owner_raises_not_found(repo: IntentRepository) -> None:
    owner = uuid4()
    other = uuid4()
    intent = _create(repo, owner_user_id=owner)

    with pytest.raises(IntentNotFoundError):
        repo.cancel(intent.id, other)


@pytest.mark.integration
def test_create_duplicate_active_raises_duplicate_error(repo: IntentRepository) -> None:
    owner = uuid4()
    _create(repo, owner_user_id=owner)

    with pytest.raises(DuplicateIntentError):
        _create(repo, owner_user_id=owner)


def _create_trailing(
    repo: IntentRepository,
    *,
    owner_user_id: UUID,
    position_side: str,
    trail_mode: str = "percentage",
    trail_value: str = "5.0",
    symbol: str = "2330",
    quantity_lots: int = 1,
    trading_date: date | None = None,
    status: str = "active",
) -> TradeIntentData:
    intent_id = repo.create(
        owner_user_id=owner_user_id,
        symbol=symbol,
        strategy="trailing_stop_alert",
        quantity_lots=quantity_lots,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="bid" if position_side == "long" else "ask",
        trading_date=trading_date or date(2026, 5, 12),
        time_in_force="day",
        execution_mode="notify_only",
        status=status,
        position_side=position_side,
        trail_mode=trail_mode,
        trail_value=Decimal(trail_value),
    )
    repo._db.commit()  # noqa: SLF001
    return repo.find_by_id(intent_id, owner_user_id)


@pytest.mark.integration
def test_create_duplicate_trailing_raises_duplicate_error(repo: IntentRepository) -> None:
    """BE-V0.5-16: 同 owner / symbol / position_side / trail_mode / trail_value
    撞 partial unique index (NULLS NOT DISTINCT) 應升為 DuplicateIntentError。
    Trailing 路徑跳過 SELECT-based pre-check (NULL = NULL false),這條測試確認
    DB unique index 的兜底有效。"""
    owner = uuid4()
    _create_trailing(repo, owner_user_id=owner, position_side="long")

    with pytest.raises(DuplicateIntentError):
        _create_trailing(repo, owner_user_id=owner, position_side="long")


@pytest.mark.integration
def test_long_and_short_trailing_same_trail_value_coexist(repo: IntentRepository) -> None:
    """同 trail_value 但 position_side 不同的 trailing 不互撞 — partial unique
    index 含 position_side。"""
    owner = uuid4()
    long_intent = _create_trailing(repo, owner_user_id=owner, position_side="long", trail_value="5.0")
    short_intent = _create_trailing(repo, owner_user_id=owner, position_side="short", trail_value="5.0")

    assert long_intent.id != short_intent.id
    assert long_intent.position_side == "long"
    assert short_intent.position_side == "short"


@pytest.mark.integration
def test_trailing_and_buy_alert_same_symbol_coexist(repo: IntentRepository) -> None:
    """trailing row (target_price_* NULL, trail_* 非 NULL) 與 buy alert
    (target_price_* 非 NULL, trail_* NULL) 共用 partial unique index 的不同欄位
    組合,不應互撞。"""
    owner = uuid4()
    buy_intent = _create(repo, owner_user_id=owner, strategy="buy_price_alert")
    trailing_intent = _create_trailing(repo, owner_user_id=owner, position_side="long")

    assert buy_intent.id != trailing_intent.id
    assert buy_intent.target_price_effective is not None
    assert trailing_intent.target_price_effective is None
    assert trailing_intent.trail_value == Decimal("5.0000")  # Numeric(9,4) → DB precision


@pytest.mark.integration
def test_create_after_terminal_allows_new_intent(repo: IntentRepository) -> None:
    owner = uuid4()
    intent = _create(repo, owner_user_id=owner)
    repo.cancel(intent.id, owner)

    new_intent = _create(repo, owner_user_id=owner)
    assert new_intent.id != intent.id
    assert new_intent.status == "active"


@pytest.mark.integration
def test_cancel_already_cancelled_returns_same_state(repo: IntentRepository) -> None:
    owner = uuid4()
    intent = _create(repo, owner_user_id=owner)
    first_cancel = repo.cancel(intent.id, owner)

    second_cancel = repo.cancel(intent.id, owner)

    assert second_cancel.cancelled_at == first_cancel.cancelled_at
    assert second_cancel.status == "cancelled"


@pytest.mark.integration
def test_list_active_keyset_pagination(repo: IntentRepository) -> None:
    owner = uuid4()
    _create(repo, owner_user_id=owner, target_price="100.0000")
    _create(repo, owner_user_id=owner, target_price="200.0000")
    _create(repo, owner_user_id=owner, target_price="300.0000")

    page1, cursor1 = repo.list_by_owner(owner, statuses=["active"], cursor=None, page_size=2)
    assert len(page1) == 2
    assert cursor1 is not None

    page2, cursor2 = repo.list_by_owner(owner, statuses=["active"], cursor=cursor1, page_size=2)
    assert len(page2) == 1
    assert cursor2 is None

    assert len({i.id for i in page1 + page2}) == 3


@pytest.mark.integration
def test_list_terminal_keyset_pagination(repo: IntentRepository) -> None:
    owner = uuid4()
    i1 = _create(repo, owner_user_id=owner, target_price="400.0000")
    i2 = _create(repo, owner_user_id=owner, target_price="500.0000")
    repo.cancel(i1.id, owner)
    repo.cancel(i2.id, owner)

    page1, cursor = repo.list_by_owner(owner, statuses=["cancelled"], cursor=None, page_size=1)
    assert len(page1) == 1
    assert cursor is not None

    page2, cursor2 = repo.list_by_owner(owner, statuses=["cancelled"], cursor=cursor, page_size=1)
    assert len(page2) == 1
    assert cursor2 is None

    assert page1[0].id != page2[0].id


@pytest.mark.integration
def test_list_mixed_statuses_uses_active_sort_order(repo: IntentRepository) -> None:
    """?status=active&status=cancelled → is_terminal_only=False → sorted by created_at DESC.

    If terminal sort (updated_at DESC) were used instead, the cancelled intent (i1, cancelled
    last) would appear first. The active sort (created_at DESC) puts i2 (created later) first,
    making the two paths observably different.
    """
    owner = uuid4()
    i1 = _create(repo, owner_user_id=owner, target_price="100.0000")
    i2 = _create(repo, owner_user_id=owner, target_price="200.0000")
    repo.cancel(i1.id, owner)  # i1.updated_at is now the most recent

    results, _ = repo.list_by_owner(owner, statuses=["active", "cancelled"], cursor=None, page_size=10)

    assert {r.id for r in results} == {i1.id, i2.id}
    # active sort: created_at DESC → i2 (created later) first
    assert results[0].id == i2.id
    assert results[1].id == i1.id


@pytest.mark.integration
def test_cursor_from_other_owner_raises_invalid_cursor(repo: IntentRepository) -> None:
    owner_a = uuid4()
    owner_b = uuid4()
    intent = _create(repo, owner_user_id=owner_a)

    with pytest.raises(InvalidCursorError):
        repo.list_by_owner(owner_b, statuses=None, cursor=str(intent.id), page_size=10)


# ----------------------------------------------------------------------
# BE-V0.5-13 — repository helpers feeding the quote provider reconcile flow
# ----------------------------------------------------------------------


@pytest.mark.integration
def test_active_or_scheduled_symbols_returns_distinct_symbols(repo: IntentRepository) -> None:
    owner_a = uuid4()
    owner_b = uuid4()
    _create(repo, owner_user_id=owner_a, target_price="100.0000")
    _create(repo, owner_user_id=owner_a, target_price="200.0000")  # same symbol, different price
    _create(repo, owner_user_id=owner_b, target_price="100.0000")  # same symbol, different owner

    symbols = repo.active_or_scheduled_symbols()

    assert symbols == {"2330"}


@pytest.mark.integration
def test_active_or_scheduled_symbols_excludes_terminal_intents(repo: IntentRepository) -> None:
    owner = uuid4()
    intent = _create(repo, owner_user_id=owner)
    repo.cancel(intent.id, owner)

    symbols = repo.active_or_scheduled_symbols()

    assert symbols == set()


@pytest.mark.integration
def test_count_active_or_scheduled_for_symbol_global(repo: IntentRepository) -> None:
    """Quota / unsubscribe reconcile counts must be broker-global, not per-owner."""

    owner_a = uuid4()
    owner_b = uuid4()
    _create(repo, owner_user_id=owner_a, target_price="100.0000")
    _create(repo, owner_user_id=owner_b, target_price="200.0000")

    assert repo.count_active_or_scheduled_for_symbol("2330") == 2
    assert repo.count_active_or_scheduled_for_symbol("0050") == 0


@pytest.mark.integration
def test_count_drops_after_cancel(repo: IntentRepository) -> None:
    owner = uuid4()
    intent = _create(repo, owner_user_id=owner)
    assert repo.count_active_or_scheduled_for_symbol("2330") == 1

    repo.cancel(intent.id, owner)

    assert repo.count_active_or_scheduled_for_symbol("2330") == 0
