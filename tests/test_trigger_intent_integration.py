"""Integration tests for TriggerIntentCommand — BE-V0.5-09."""

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, select, update
from sqlalchemy.orm import Session

from app.commands.trigger_intent import (
    IntentNotActiveError,
    TriggerIntentCommand,
    TriggerIntentInput,
    persist_trigger,
)
from app.core.config import get_settings
from app.db.models.core import Notification, Symbol, TradeIntent, TriggerEvent
from app.domain.trade_intent import IntentNotFoundError
from app.domain.trigger_event import DuplicateTriggerError
from app.repositories.intent_repository import IntentRepository

TAIPEI = ZoneInfo("Asia/Taipei")
QUOTE_TIME = datetime(2026, 5, 11, 10, 0, 5, tzinfo=TAIPEI)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def trigger_engine() -> Generator[Engine]:
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
def db_session(trigger_engine: Engine) -> Generator[Session]:
    session = Session(trigger_engine)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repo(db_session: Session) -> IntentRepository:
    return IntentRepository(db_session)


@pytest.fixture
def trigger_cmd(db_session: Session) -> TriggerIntentCommand:
    return TriggerIntentCommand(db_session)


def _create_active_intent(
    repo: IntentRepository,
    db: Session,
    *,
    owner_user_id: UUID,
    target_price: str = "100.0000",
    strategy: str = "buy_price_alert",
) -> UUID:
    # PR #12 made IntentRepository.create flush-only and return UUID; commit
    # explicitly so the trigger command's SELECT FOR UPDATE finds a row.
    intent_id = repo.create(
        owner_user_id=owner_user_id,
        symbol="2330",
        strategy=strategy,
        quantity_lots=1,
        target_price_original=Decimal(target_price),
        target_price_effective=Decimal(target_price),
        trigger_reference_price_type="ask" if "buy" in strategy else "bid",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
    )
    db.commit()
    return intent_id


def _input(
    intent_id: UUID,
    *,
    trigger_price: str = "99.0000",
    trigger_reference_price_type: str = "ask",
    fallback_used: bool = False,
    quote_snapshot: dict[str, Any] | None = None,
) -> TriggerIntentInput:
    return TriggerIntentInput(
        intent_id=intent_id,
        trigger_price=Decimal(trigger_price),
        trigger_reference_price_type=trigger_reference_price_type,
        fallback_used=fallback_used,
        quote_snapshot=quote_snapshot
        or {
            "symbol": "2330",
            "ask_price": "99.0000",
            "quote_time": QUOTE_TIME.isoformat(),
        },
        quote_time=QUOTE_TIME,
    )


@pytest.mark.integration
def test_trigger_active_intent_writes_three_rows_atomically(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)

    result = trigger_cmd.execute(_input(intent_id))

    # trigger_events row populated correctly
    assert result.trigger_event.trade_intent_id == intent_id
    assert result.trigger_event.owner_user_id == owner
    assert result.trigger_event.symbol == "2330"
    assert result.trigger_event.trigger_price == Decimal("99.0000")
    assert result.trigger_event.trigger_reference_price_type == "ask"
    assert result.trigger_event.fallback_used is False
    assert result.trigger_event.quote_snapshot["symbol"] == "2330"

    # intent transitioned to triggered with timestamp
    updated = repo.find_by_id(intent_id, owner)
    assert updated.status == "triggered"
    assert updated.triggered_at is not None

    # notification row populated and links back to the intent
    assert result.notification.type == "price_triggered"
    assert result.notification.trade_intent_id == intent_id
    assert result.notification.owner_user_id == owner
    assert result.notification.rendered_title == "2330 到價提醒已觸發"
    assert "100.00" in result.notification.rendered_body
    assert "99.00" in result.notification.rendered_body
    assert "僅通知、未下單、不保證成交" in result.notification.rendered_body


@pytest.mark.integration
def test_trigger_persists_fallback_metadata(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)

    result = trigger_cmd.execute(
        _input(
            intent_id,
            trigger_price="99.0000",
            trigger_reference_price_type="last_fallback",
            fallback_used=True,
            quote_snapshot={"symbol": "2330", "last_price": "99.0000"},
        )
    )

    assert result.trigger_event.trigger_reference_price_type == "last_fallback"
    assert result.trigger_event.fallback_used is True


@pytest.mark.integration
def test_intent_not_found_raises(trigger_cmd: TriggerIntentCommand) -> None:
    with pytest.raises(IntentNotFoundError):
        trigger_cmd.execute(_input(uuid4()))


@pytest.mark.integration
def test_already_triggered_intent_status_guard_raises(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)
    trigger_cmd.execute(_input(intent_id))

    with pytest.raises(IntentNotActiveError) as exc_info:
        trigger_cmd.execute(_input(intent_id))
    assert exc_info.value.current_status == "triggered"


@pytest.mark.integration
def test_cancelled_intent_status_guard_raises(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)
    repo.cancel(intent_id, owner)

    with pytest.raises(IntentNotActiveError) as exc_info:
        trigger_cmd.execute(_input(intent_id))
    assert exc_info.value.current_status == "cancelled"


@pytest.mark.integration
def test_duplicate_trigger_via_unique_constraint_raises(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    """Force the UNIQUE backstop by leaving the intent active while a trigger row already exists.

    This is an inconsistent state that should never occur via the command path
    (status guard catches double-trigger first); we manufacture it to confirm
    the IntegrityError → DuplicateTriggerError translation.
    """
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)

    db_session.add(
        TriggerEvent(
            id=uuid4(),
            trade_intent_id=intent_id,
            owner_user_id=owner,
            symbol="2330",
            quote_snapshot={"symbol": "2330"},
            target_price_effective=Decimal("100.0000"),
            trigger_price=Decimal("99.0000"),
            trigger_reference_price_type="ask",
            fallback_used=False,
            filled_quantity_lots=1,
            triggered_at=QUOTE_TIME,
        )
    )
    db_session.commit()

    with pytest.raises(DuplicateTriggerError):
        trigger_cmd.execute(_input(intent_id))


@pytest.mark.integration
def test_rollback_leaves_no_partial_state_on_duplicate(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)

    db_session.add(
        TriggerEvent(
            id=uuid4(),
            trade_intent_id=intent_id,
            owner_user_id=owner,
            symbol="2330",
            quote_snapshot={"symbol": "2330"},
            target_price_effective=Decimal("100.0000"),
            trigger_price=Decimal("99.0000"),
            trigger_reference_price_type="ask",
            fallback_used=False,
            filled_quantity_lots=1,
            triggered_at=QUOTE_TIME,
        )
    )
    db_session.commit()

    with pytest.raises(DuplicateTriggerError):
        trigger_cmd.execute(_input(intent_id))

    # Intent must still be active and no extra notification was written.
    intent_after = repo.find_by_id(intent_id, owner)
    assert intent_after.status == "active"
    assert intent_after.triggered_at is None

    notification_count = db_session.execute(select(Notification).where(Notification.trade_intent_id == intent_id)).all()
    assert notification_count == []


@pytest.mark.integration
def test_persist_trigger_rowcount_guard_raises_when_status_changed_underneath(
    trigger_engine: Engine,
) -> None:
    """Defense-in-depth: simulate a future caller that skips SELECT FOR UPDATE.

    Two sessions on the same engine — the main session loads the intent
    ORM row while it is still active; a racing session flips status to
    `cancelled` and commits. When the main session then drives
    `persist_trigger`, its `UPDATE ... WHERE status='active'` will miss
    (the row is no longer active in DB) — `rowcount` 0 must raise
    `IntentNotActiveError` and prevent the staged trigger_event /
    notification from committing.

    Direct unit-style call into `persist_trigger` is intentional —
    `TriggerIntentCommand.execute()` would short-circuit at the Python
    status guard after re-reading the row, so we cannot exercise the
    rowcount branch through the public surface.
    """

    owner = uuid4()
    intent_id: UUID

    with Session(trigger_engine) as setup_session:
        repo = IntentRepository(setup_session)
        intent_id = repo.create(
            owner_user_id=owner,
            symbol="2330",
            strategy="buy_price_alert",
            quantity_lots=1,
            target_price_original=Decimal("100.0000"),
            target_price_effective=Decimal("100.0000"),
            trigger_reference_price_type="ask",
            trading_date=date(2026, 5, 11),
            time_in_force="day",
            execution_mode="notify_only",
            status="active",
        )
        setup_session.commit()

    main_session = Session(trigger_engine)
    race_session = Session(trigger_engine)
    try:
        intent_row = main_session.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one()
        assert intent_row.status == "active"  # main session's view, pre-race

        # Racing session flips DB-side status. READ COMMITTED — main
        # session's next statement will observe the new value.
        race_session.execute(update(TradeIntent).where(TradeIntent.id == intent_id).values(status="cancelled"))
        race_session.commit()

        with pytest.raises(IntentNotActiveError):
            persist_trigger(main_session, intent_row, _input(intent_id))

        main_session.rollback()
    finally:
        main_session.close()
        race_session.close()

    # Verify no orphan trigger_event / notification leaked through.
    with Session(trigger_engine) as verify:
        trigger_rows = verify.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).all()
        notif_rows = verify.execute(select(Notification).where(Notification.trade_intent_id == intent_id)).all()
        intent_after = verify.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one()

    assert trigger_rows == []
    assert notif_rows == []
    assert intent_after.status == "cancelled"
    assert intent_after.triggered_at is None


# ----------------------------------------------------------------------------
# BE-V0.5-15 limit_buy_order / limit_sell_order coverage
# ----------------------------------------------------------------------------


@pytest.mark.integration
def test_trigger_limit_buy_order_writes_full_fill_and_limit_notification(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = repo.create(
        owner_user_id=owner,
        symbol="2330",
        strategy="limit_buy_order",
        quantity_lots=3,
        target_price_original=Decimal("600.0000"),
        target_price_effective=Decimal("600.0000"),
        trigger_reference_price_type="ask",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
        transaction_mode="partial_fill_allowed",
        notification_mode="single",
    )
    db_session.commit()

    result = trigger_cmd.execute(
        _input(
            intent_id,
            trigger_price="599.0000",
            trigger_reference_price_type="ask",
            quote_snapshot={"symbol": "2330", "ask_price": "599.0000"},
        )
    )

    # trigger_event carries the V0.5 full-fill snapshot.
    assert result.trigger_event.filled_quantity_lots == 3

    # intent transitioned to triggered and recorded the V2 partial-fill metadata.
    updated = repo.find_by_id(intent_id, owner)
    assert updated.status == "triggered"
    assert updated.triggered_at is not None
    assert updated.transaction_mode == "partial_fill_allowed"
    assert updated.notification_mode == "single"
    assert updated.filled_quantity_lots == 3
    assert updated.last_fill_at is not None

    # notification routes to limit_order_triggered with the required wording.
    assert result.notification.type == "limit_order_triggered"
    assert result.notification.rendered_title == "2330 限價買單已觸發"
    body = result.notification.rendered_body
    assert "限價買單" in body
    assert "成交 3 張 / 委託 3 張" in body
    assert "僅通知、未下單、不保證成交" in body


@pytest.mark.integration
def test_trigger_limit_sell_order_writes_full_fill_and_limit_notification(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    owner = uuid4()
    intent_id = repo.create(
        owner_user_id=owner,
        symbol="2330",
        strategy="limit_sell_order",
        quantity_lots=2,
        target_price_original=Decimal("650.0000"),
        target_price_effective=Decimal("650.0000"),
        trigger_reference_price_type="bid",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
    )
    db_session.commit()

    result = trigger_cmd.execute(
        _input(
            intent_id,
            trigger_price="651.0000",
            trigger_reference_price_type="bid",
            quote_snapshot={"symbol": "2330", "bid_price": "651.0000"},
        )
    )

    assert result.trigger_event.filled_quantity_lots == 2
    assert result.notification.type == "limit_order_triggered"
    assert result.notification.rendered_title == "2330 限價賣單已觸發"
    assert "成交 2 張 / 委託 2 張" in result.notification.rendered_body


@pytest.mark.integration
def test_trigger_buy_price_alert_still_writes_full_fill_and_price_notification(
    db_session: Session,
    trigger_cmd: TriggerIntentCommand,
    repo: IntentRepository,
) -> None:
    """Regression: buy_price_alert keeps notification type 'price_triggered'
    but the new filled_quantity_lots / last_fill_at fields are populated."""
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner)

    result = trigger_cmd.execute(_input(intent_id))

    updated = repo.find_by_id(intent_id, owner)
    assert updated.transaction_mode == "single_notification"
    assert updated.notification_mode == "single"
    assert updated.filled_quantity_lots == 1
    assert updated.last_fill_at is not None
    assert result.notification.type == "price_triggered"
    assert result.trigger_event.filled_quantity_lots == 1


@pytest.mark.integration
def test_buy_price_alert_and_limit_buy_order_share_target_without_conflict(
    db_session: Session,
    repo: IntentRepository,
) -> None:
    """Duplicate index already includes `strategy`; the two strategies must
    not block each other when otherwise identical."""
    owner = uuid4()

    repo.create(
        owner_user_id=owner,
        symbol="2330",
        strategy="buy_price_alert",
        quantity_lots=1,
        target_price_original=Decimal("600.0000"),
        target_price_effective=Decimal("600.0000"),
        trigger_reference_price_type="ask",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
    )
    repo.create(
        owner_user_id=owner,
        symbol="2330",
        strategy="limit_buy_order",
        quantity_lots=1,
        target_price_original=Decimal("600.0000"),
        target_price_effective=Decimal("600.0000"),
        trigger_reference_price_type="ask",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
    )
    db_session.commit()

    rows = (
        db_session.execute(select(TradeIntent).where(TradeIntent.owner_user_id == owner, TradeIntent.symbol == "2330"))
        .scalars()
        .all()
    )
    assert {row.strategy for row in rows} == {"buy_price_alert", "limit_buy_order"}
