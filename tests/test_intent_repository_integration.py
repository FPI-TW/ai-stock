"""Integration tests for IntentRepository against a real PostgreSQL database."""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Symbol
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
        yield session
    finally:
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
    return repo.create(
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
def test_cursor_from_other_owner_raises_invalid_cursor(repo: IntentRepository) -> None:
    owner_a = uuid4()
    owner_b = uuid4()
    intent = _create(repo, owner_user_id=owner_a)

    with pytest.raises(InvalidCursorError):
        repo.list_by_owner(owner_b, statuses=None, cursor=str(intent.id), page_size=10)
