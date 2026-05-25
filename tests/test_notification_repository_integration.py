"""Integration tests for NotificationRepository against a real PostgreSQL database."""

import time
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Thread
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Notification, Symbol, TradeIntent
from app.domain.notification import NotificationData, NotificationNotFoundError
from app.domain.trade_intent import InvalidCursorError
from app.repositories.intent_repository import IntentRepository
from app.repositories.notification_repository import NotificationRepository


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
        # FK ordering: notifications → trade_intents.
        session.execute(delete(Notification))
        session.execute(delete(TradeIntent))
        session.commit()
        yield session
    finally:
        session.execute(delete(Notification))
        session.execute(delete(TradeIntent))
        session.commit()
        session.close()


@pytest.fixture
def repo(db_session: Session) -> NotificationRepository:
    return NotificationRepository(db_session)


def _seed_intent(db: Session, *, owner_user_id: UUID) -> UUID:
    intent_repo = IntentRepository(db)
    intent_id = intent_repo.create(
        owner_user_id=owner_user_id,
        symbol="2330",
        strategy="buy_price_alert",
        quantity_lots=1,
        target_price_original=Decimal("100.0000"),
        target_price_effective=Decimal("100.0000"),
        trigger_reference_price_type="ask",
        trading_date=date(2026, 5, 12),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
    )
    db.commit()
    return intent_id


def _seed_notification(
    db: Session,
    *,
    owner_user_id: UUID,
    trade_intent_id: UUID,
    rendered_title: str = "2330 到價提醒已觸發",
    rendered_body: str = "買進到價提醒",
) -> UUID:
    notification_id = uuid4()
    db.add(
        Notification(
            id=notification_id,
            owner_user_id=owner_user_id,
            trade_intent_id=trade_intent_id,
            type="price_triggered",
            rendered_title=rendered_title,
            rendered_body=rendered_body,
        )
    )
    db.commit()
    return notification_id


def _seed_n_notifications(db: Session, *, owner_user_id: UUID, trade_intent_id: UUID, count: int) -> list[UUID]:
    """Seed `count` notifications committing one-per-statement so server_default
    timestamps differ. Sleeps 5 ms between commits to guarantee monotonic
    created_at on systems with low clock resolution.
    """

    ids: list[UUID] = []
    for i in range(count):
        ids.append(
            _seed_notification(
                db,
                owner_user_id=owner_user_id,
                trade_intent_id=trade_intent_id,
                rendered_title=f"notif-{i}",
            )
        )
        time.sleep(0.005)
    return ids


# ------------------------------------------------------------------
# list_by_owner
# ------------------------------------------------------------------


@pytest.mark.integration
def test_list_by_owner_returns_only_caller_owner_rows(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()
    other = uuid4()
    intent_a = _seed_intent(db_session, owner_user_id=owner)
    intent_b = _seed_intent(db_session, owner_user_id=other)
    _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_a)
    _seed_notification(db_session, owner_user_id=other, trade_intent_id=intent_b)

    items, next_cursor = repo.list_by_owner(owner_user_id=owner, unread_only=False, cursor=None, page_size=50)

    assert len(items) == 1
    assert items[0].owner_user_id == owner
    assert next_cursor is None


@pytest.mark.integration
def test_list_by_owner_orders_newest_first(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    ids = _seed_n_notifications(db_session, owner_user_id=owner, trade_intent_id=intent_id, count=3)

    items, _ = repo.list_by_owner(owner_user_id=owner, unread_only=False, cursor=None, page_size=50)

    # ids[0] was inserted first → it should appear last (DESC by created_at).
    returned = [n.id for n in items]
    assert returned == list(reversed(ids))


@pytest.mark.integration
def test_list_by_owner_unread_only_filters_read_rows(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    read_one = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)
    repo.mark_read(read_one, owner)
    db_session.commit()
    unread_one = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)

    items, _ = repo.list_by_owner(owner_user_id=owner, unread_only=True, cursor=None, page_size=50)

    returned_ids = [n.id for n in items]
    assert read_one not in returned_ids
    assert unread_one in returned_ids


@pytest.mark.integration
def test_list_by_owner_cursor_keyset_pagination(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    ids = _seed_n_notifications(db_session, owner_user_id=owner, trade_intent_id=intent_id, count=3)

    page1, cursor1 = repo.list_by_owner(owner_user_id=owner, unread_only=False, cursor=None, page_size=2)
    assert [n.id for n in page1] == [ids[2], ids[1]]
    assert cursor1 == str(ids[1])

    page2, cursor2 = repo.list_by_owner(owner_user_id=owner, unread_only=False, cursor=cursor1, page_size=2)
    assert [n.id for n in page2] == [ids[0]]
    assert cursor2 is None


@pytest.mark.integration
def test_list_by_owner_cursor_anchor_owner_mismatch_raises_invalid(
    repo: NotificationRepository, db_session: Session
) -> None:
    """Anchor row belonging to another owner must not be readable as a cursor."""
    owner = uuid4()
    other = uuid4()
    intent_other = _seed_intent(db_session, owner_user_id=other)
    other_notification = _seed_notification(db_session, owner_user_id=other, trade_intent_id=intent_other)

    with pytest.raises(InvalidCursorError):
        repo.list_by_owner(
            owner_user_id=owner,
            unread_only=False,
            cursor=str(other_notification),
            page_size=10,
        )


@pytest.mark.integration
def test_list_by_owner_unknown_cursor_raises_invalid(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()

    with pytest.raises(InvalidCursorError):
        repo.list_by_owner(owner_user_id=owner, unread_only=False, cursor=str(uuid4()), page_size=10)


# ------------------------------------------------------------------
# mark_read
# ------------------------------------------------------------------


@pytest.mark.integration
def test_mark_read_sets_read_at(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    notification_id = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)

    result = repo.mark_read(notification_id, owner)
    db_session.commit()

    assert isinstance(result, NotificationData)
    assert result.read_at is not None


@pytest.mark.integration
def test_mark_read_twice_does_not_change_read_at(repo: NotificationRepository, db_session: Session) -> None:
    """Idempotency: second mark_read must not overwrite read_at or updated_at."""
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    notification_id = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)

    first = repo.mark_read(notification_id, owner)
    db_session.commit()
    first_read_at = first.read_at
    first_updated_at = first.updated_at
    time.sleep(0.01)
    second = repo.mark_read(notification_id, owner)
    db_session.commit()

    assert second.read_at == first_read_at
    assert second.updated_at == first_updated_at


@pytest.mark.integration
def test_concurrent_mark_read_does_not_overwrite_first_read_at(int_engine: Engine, db_session: Session) -> None:
    """A racing mark_read call must not overwrite the first successful read timestamp."""
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    notification_id = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)

    first_session = Session(int_engine)
    second_result: list[NotificationData] = []
    second_error: list[BaseException] = []

    def run_second_mark_read() -> None:
        second_session = Session(int_engine)
        try:
            second_result.append(NotificationRepository(second_session).mark_read(notification_id, owner))
            second_session.commit()
        except BaseException as exc:
            second_session.rollback()
            second_error.append(exc)
        finally:
            second_session.close()

    try:
        first = NotificationRepository(first_session).mark_read(notification_id, owner)
        time.sleep(0.01)

        second_thread = Thread(target=run_second_mark_read)
        second_thread.start()
        time.sleep(0.01)

        first_session.commit()
        first_read_at = first.read_at
        first_updated_at = first.updated_at

        second_thread.join(timeout=5)
        assert not second_thread.is_alive()
        assert second_error == []
        second = second_result[0]

        assert second.read_at == first_read_at
        assert second.updated_at == first_updated_at
    finally:
        first_session.close()


@pytest.mark.integration
def test_mark_read_owner_mismatch_raises_not_found(repo: NotificationRepository, db_session: Session) -> None:
    owner = uuid4()
    other = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    notification_id = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)

    with pytest.raises(NotificationNotFoundError):
        repo.mark_read(notification_id, other)


@pytest.mark.integration
def test_mark_read_unknown_id_raises_not_found(
    repo: NotificationRepository,
) -> None:
    with pytest.raises(NotificationNotFoundError):
        repo.mark_read(uuid4(), uuid4())


@pytest.mark.integration
def test_mark_read_does_not_mutate_trade_intent(repo: NotificationRepository, db_session: Session) -> None:
    """Per BE-V0.5-10 acceptance: read status must not affect trade_intent."""
    owner = uuid4()
    intent_id = _seed_intent(db_session, owner_user_id=owner)
    notification_id = _seed_notification(db_session, owner_user_id=owner, trade_intent_id=intent_id)
    intent_before = db_session.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one()
    status_before = intent_before.status
    updated_at_before = intent_before.updated_at
    cancelled_at_before = intent_before.cancelled_at

    repo.mark_read(notification_id, owner)
    db_session.commit()
    db_session.expire_all()

    intent_after = db_session.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one()
    assert intent_after.status == status_before
    assert intent_after.updated_at == updated_at_before
    assert intent_after.cancelled_at == cancelled_at_before
