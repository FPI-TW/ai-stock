"""Integration tests for POST /dev/evaluate-quotes — BE-V0.5-09."""

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_quote_provider, get_trading_session_service
from app.core.config import get_settings
from app.db.models.core import Notification, Symbol, TriggerEvent
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from app.repositories.intent_repository import IntentRepository
from app.services.quote.base import QuoteSnapshot
from app.services.quote.in_memory import InMemoryQuoteProvider

TAIPEI = ZoneInfo("Asia/Taipei")
# Monday inside the regular session — used for both `now` (via injected clock)
# and quote_time so the evaluator's dual session guard passes.
SESSION_NOW_TAIPEI = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
SESSION_NOW_UTC = SESSION_NOW_TAIPEI.astimezone(UTC)
SESSION_QUOTE_TIME = datetime(2026, 5, 11, 9, 59, 55, tzinfo=TAIPEI)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


def _scrub_trailing_rows_before_downgrade(database_url: str) -> None:
    """Defensive cleanup before alembic downgrade.

    The BE-V0.5-16 trailing migration's downgrade re-adds a strategy CHECK
    constraint that excludes ``trailing_stop_alert``; any pre-existing
    trailing row (e.g. from a previously-failed test) makes downgrade abort
    with IntegrityError. Wipe trailing rows up front so the fixture can
    survive cross-module test failures.
    """
    pre_engine = create_engine(database_url)
    try:
        with pre_engine.connect() as c:
            check = c.execute(text("SELECT to_regclass('public.trade_intents')")).scalar()
            if check is None:
                return  # tables not present — nothing to scrub
            c.execute(
                text(
                    "DELETE FROM trigger_events WHERE trade_intent_id IN "
                    "(SELECT id FROM trade_intents WHERE strategy='trailing_stop_alert')"
                )
            )
            c.execute(
                text(
                    "DELETE FROM notifications WHERE trade_intent_id IN "
                    "(SELECT id FROM trade_intents WHERE strategy='trailing_stop_alert')"
                )
            )
            c.execute(text("DELETE FROM trade_intents WHERE strategy='trailing_stop_alert'"))
            c.commit()
    finally:
        pre_engine.dispose()


@pytest.fixture(scope="module")
def dev_engine() -> Generator[Engine]:
    config = _alembic_config()
    _scrub_trailing_rows_before_downgrade(get_settings().database_url or "")
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
        session.add(
            Symbol(
                id=uuid4(),
                symbol="2317",
                display_name="鴻海",
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
        _scrub_trailing_rows_before_downgrade(get_settings().database_url or "")
        command.downgrade(config, "base")


@pytest.fixture
def db_session(dev_engine: Engine) -> Generator[Session]:
    session = Session(dev_engine)
    # Endpoint loads active intents owner-agnostically, so per-test isolation
    # depends on truncating intent / trigger / notification rows up front.
    session.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repo(db_session: Session) -> IntentRepository:
    return IntentRepository(db_session)


@pytest.fixture
def quote_provider() -> InMemoryQuoteProvider:
    return InMemoryQuoteProvider()


def _build_client(quote_provider: InMemoryQuoteProvider, now_utc: datetime) -> Generator[TestClient]:
    session_with_clock = TradingSessionService(clock=lambda: now_utc)
    app = create_app()
    app.dependency_overrides[get_quote_provider] = lambda: quote_provider
    app.dependency_overrides[get_trading_session_service] = lambda: session_with_clock
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client(quote_provider: InMemoryQuoteProvider) -> Generator[TestClient]:
    yield from _build_client(quote_provider, SESSION_NOW_UTC)


@pytest.fixture
def weekend_client(quote_provider: InMemoryQuoteProvider) -> Generator[TestClient]:
    # Saturday 10:00 Taipei — outside the regular session.
    weekend_utc = datetime(2026, 5, 16, 2, 0, tzinfo=UTC)
    yield from _build_client(quote_provider, weekend_utc)


def _create_active_intent(
    repo: IntentRepository,
    db: Session,
    *,
    owner_user_id: UUID,
    symbol: str = "2330",
    target_price: str = "100.0000",
    strategy: str = "buy_price_alert",
) -> UUID:
    # PR #12 made IntentRepository.create flush-only and return UUID;
    # the test must commit so the HTTP endpoint (separate session) can see it.
    intent_id = repo.create(
        owner_user_id=owner_user_id,
        symbol=symbol,
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


def _create_active_trailing_intent(
    repo: IntentRepository,
    db: Session,
    *,
    owner_user_id: UUID,
    symbol: str = "2330",
    position_side: str = "long",
    trail_mode: str = "percentage",
    trail_value: str = "5",
) -> UUID:
    intent_id = repo.create(
        owner_user_id=owner_user_id,
        symbol=symbol,
        strategy="trailing_stop_alert",
        quantity_lots=1,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="bid" if position_side == "long" else "ask",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        execution_mode="notify_only",
        status="active",
        position_side=position_side,
        trail_mode=trail_mode,
        trail_value=Decimal(trail_value),
    )
    db.commit()
    return intent_id


def _snapshot(
    symbol: str,
    *,
    ask: str | None = None,
    bid: str | None = None,
    last: str | None = None,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        quote_time=SESSION_QUOTE_TIME,
        received_at=SESSION_QUOTE_TIME,
        ask_price=Decimal(ask) if ask else None,
        bid_price=Decimal(bid) if bid else None,
        last_price=Decimal(last) if last else None,
    )


@pytest.mark.integration
def test_evaluate_buys_at_target_triggers_intent_and_writes_three_rows(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, target_price="100.0000")
    quote_provider.push_quote(_snapshot("2330", ask="99.0000"))

    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["evaluatedSymbols"] == ["2330"]
    assert body["data"]["triggeredIntentIds"] == [str(intent_id)]

    # intent status flipped
    intent_after = repo.find_by_id(intent_id, owner)
    assert intent_after.status == "triggered"

    # trigger_event row exists with full quote snapshot
    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_price == Decimal("99.0000")
    assert trigger_row.trigger_reference_price_type == "ask"
    assert trigger_row.fallback_used is False
    assert trigger_row.quote_snapshot["symbol"] == "2330"
    assert trigger_row.quote_snapshot["ask_price"] == "99.0000"

    # notification row exists
    notification_row = db_session.execute(
        select(Notification).where(Notification.trade_intent_id == intent_id)
    ).scalar_one()
    assert notification_row.type == "price_triggered"
    assert "2330 到價提醒已觸發" == notification_row.rendered_title


@pytest.mark.integration
def test_evaluate_sell_bid_above_target_triggers_intent(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(
        repo,
        db_session,
        owner_user_id=owner,
        target_price="100.0000",
        strategy="sell_price_alert",
    )
    quote_provider.push_quote(_snapshot("2330", bid="101.0000"))  # bid > target

    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    assert response.json()["data"]["triggeredIntentIds"] == [str(intent_id)]

    intent_after = repo.find_by_id(intent_id, owner)
    assert intent_after.status == "triggered"

    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_price == Decimal("101.0000")
    assert trigger_row.trigger_reference_price_type == "bid"
    assert trigger_row.fallback_used is False
    assert trigger_row.quote_snapshot["bid_price"] == "101.0000"


@pytest.mark.integration
def test_evaluate_with_no_symbols_evaluates_all_actives(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_2330 = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    intent_2317 = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2317", target_price="50.0000")
    quote_provider.push_quote(_snapshot("2330", ask="99.0000"))
    quote_provider.push_quote(_snapshot("2317", ask="49.0000"))

    response = client.post("/dev/evaluate-quotes", json={})

    assert response.status_code == 200
    body = response.json()
    assert set(body["data"]["evaluatedSymbols"]) >= {"2330", "2317"}
    assert set(body["data"]["triggeredIntentIds"]) == {str(intent_2330), str(intent_2317)}


@pytest.mark.integration
def test_evaluate_quote_unavailable_skips_intent(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    # quote_provider intentionally empty — no quote for 2330

    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    assert response.json()["data"]["triggeredIntentIds"] == []
    intent_after = repo.find_by_id(intent_id, owner)
    assert intent_after.status == "active"
    assert db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).first() is None


@pytest.mark.integration
def test_evaluate_condition_not_met_does_not_trigger(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    quote_provider.push_quote(_snapshot("2330", ask="101.0000"))  # above target

    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    assert response.json()["data"]["triggeredIntentIds"] == []
    intent_after = repo.find_by_id(intent_id, owner)
    assert intent_after.status == "active"


@pytest.mark.integration
def test_evaluate_cancelled_intent_is_not_loaded(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    repo.cancel(intent_id, owner)
    db_session.commit()
    quote_provider.push_quote(_snapshot("2330", ask="99.0000"))

    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    assert response.json()["data"]["triggeredIntentIds"] == []


@pytest.mark.integration
def test_evaluate_last_fallback_path_persists_metadata(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    quote_provider.push_quote(_snapshot("2330", last="99.0000"))  # ask missing → fallback last

    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    assert response.json()["data"]["triggeredIntentIds"] == [str(intent_id)]

    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_reference_price_type == "last_fallback"
    assert trigger_row.fallback_used is True
    assert trigger_row.quote_snapshot["last_price"] == "99.0000"


@pytest.mark.integration
def test_evaluate_duplicate_call_does_not_create_second_trigger(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    quote_provider.push_quote(_snapshot("2330", ask="99.0000"))

    first = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    second = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert first.json()["data"]["triggeredIntentIds"] == [str(intent_id)]
    # status guard catches the second pass — intent already triggered, so
    # repo.system_list_active_by_symbols won't even return it.
    assert second.json()["data"]["triggeredIntentIds"] == []

    trigger_count = (
        db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalars().all()
    )
    assert len(trigger_count) == 1


@pytest.mark.integration
def test_evaluate_outside_session_skips_all(
    db_session: Session,
    weekend_client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_intent(repo, db_session, owner_user_id=owner, symbol="2330", target_price="100.0000")
    quote_provider.push_quote(_snapshot("2330", ask="99.0000"))

    response = weekend_client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    assert response.status_code == 200
    assert response.json()["data"]["triggeredIntentIds"] == []
    intent_after = repo.find_by_id(intent_id, owner)
    assert intent_after.status == "active"


# ---------------------------------------------------------------------------
# BE-V0.5-16 trailing stop scenarios (spec §測試要求 line 370-385)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_trailing_long_percentage_5_quote_sequence(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    """Spec example: long 5% trailing, quote sequence 90 → 100 → 95 → 94.

    Verifies that the dispatcher loop persists watermark mutations even on
    non-triggering quotes, and that the trigger fires only when bid breaks
    the latest dynamic_trigger_price.
    """
    owner = uuid4()
    intent_id = _create_active_trailing_intent(
        repo, db_session, owner_user_id=owner, position_side="long", trail_value="5"
    )

    # Quote 1: last=90 → watermark_high=90, dynamic=85.5
    quote_provider.push_quote(_snapshot("2330", bid="90", ask="90.1", last="90"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    after_1 = repo.find_by_id(intent_id, owner)
    assert after_1.watermark_high == Decimal("90")
    assert after_1.dynamic_trigger_price == Decimal("85.5")
    assert after_1.status == "active"

    # Quote 2: last=100 → watermark up to 100, dynamic up to 95.0
    quote_provider.push_quote(_snapshot("2330", bid="100", ask="100.1", last="100"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    after_2 = repo.find_by_id(intent_id, owner)
    assert after_2.watermark_high == Decimal("100")
    assert after_2.dynamic_trigger_price == Decimal("95.0")
    assert after_2.status == "active"

    # Quote 3: last=95 (lower) → watermark unchanged, bid 95.1 > dynamic 95 → no trigger
    quote_provider.push_quote(_snapshot("2330", bid="95.1", ask="95.2", last="95"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    after_3 = repo.find_by_id(intent_id, owner)
    assert after_3.watermark_high == Decimal("100")
    assert after_3.dynamic_trigger_price == Decimal("95.0")
    assert after_3.status == "active"

    # Quote 4: bid 94 ≤ dynamic 95 → trigger.
    quote_provider.push_quote(_snapshot("2330", bid="94", ask="94.5", last="94"))
    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    assert response.json()["data"]["triggeredIntentIds"] == [str(intent_id)]

    after_4 = repo.find_by_id(intent_id, owner)
    assert after_4.status == "triggered"

    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_price == Decimal("94.0000")
    assert trigger_row.trigger_reference_price_type == "bid"
    assert trigger_row.fallback_used is False
    # spec §178: target_price_effective overloaded with dynamic_trigger_price_at_trigger
    assert trigger_row.target_price_effective == Decimal("95.0000")
    assert trigger_row.watermark_at_trigger == Decimal("100.0000")
    assert trigger_row.dynamic_trigger_price_at_trigger == Decimal("95.0000")

    notification_row = db_session.execute(
        select(Notification).where(Notification.trade_intent_id == intent_id)
    ).scalar_one()
    assert notification_row.type == "trailing_stop_triggered"
    assert notification_row.rendered_title == "2330 移動出場已觸發"
    assert "策略：移動出場（多單）" in notification_row.rendered_body
    assert "今日最高價：100.00" in notification_row.rendered_body
    assert "94.00" in notification_row.rendered_body


@pytest.mark.integration
def test_trailing_short_fixed_amount_5_quote_sequence(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    """Spec example: short fixed=5, quote sequence 110 → 100 → 105 → 106."""
    owner = uuid4()
    intent_id = _create_active_trailing_intent(
        repo,
        db_session,
        owner_user_id=owner,
        position_side="short",
        trail_mode="fixed_amount",
        trail_value="5",
    )

    quote_provider.push_quote(_snapshot("2330", bid="109", ask="110", last="110"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    after_1 = repo.find_by_id(intent_id, owner)
    assert after_1.watermark_low == Decimal("110")
    assert after_1.dynamic_trigger_price == Decimal("115")

    quote_provider.push_quote(_snapshot("2330", bid="99", ask="100", last="100"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    after_2 = repo.find_by_id(intent_id, owner)
    assert after_2.watermark_low == Decimal("100")
    assert after_2.dynamic_trigger_price == Decimal("105")

    # Quote 3: last=105 (higher) → watermark unchanged, ask 104.5 < dynamic 105 → no trigger
    quote_provider.push_quote(_snapshot("2330", bid="104", ask="104.5", last="105"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    after_3 = repo.find_by_id(intent_id, owner)
    assert after_3.watermark_low == Decimal("100")
    assert after_3.dynamic_trigger_price == Decimal("105")
    assert after_3.status == "active"

    # Quote 4: ask 106 ≥ dynamic 105 → trigger
    quote_provider.push_quote(_snapshot("2330", bid="105.5", ask="106", last="106"))
    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    assert response.json()["data"]["triggeredIntentIds"] == [str(intent_id)]

    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_price == Decimal("106.0000")
    assert trigger_row.trigger_reference_price_type == "ask"
    assert trigger_row.watermark_at_trigger == Decimal("100.0000")
    assert trigger_row.dynamic_trigger_price_at_trigger == Decimal("105.0000")


@pytest.mark.integration
def test_trailing_long_bid_missing_falls_back_to_last(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    owner = uuid4()
    intent_id = _create_active_trailing_intent(
        repo, db_session, owner_user_id=owner, position_side="long", trail_value="5"
    )

    # Seed watermark with first quote, then use a bid-missing quote that
    # would trigger via last fallback.
    quote_provider.push_quote(_snapshot("2330", bid="100", ask="100.1", last="100"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    # Bid missing, last=94 ≤ dynamic 95.0 → trigger via last_fallback
    quote_provider.push_quote(_snapshot("2330", ask="95.5", last="94"))
    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    assert response.json()["data"]["triggeredIntentIds"] == [str(intent_id)]

    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_reference_price_type == "last_fallback"
    assert trigger_row.fallback_used is True
    assert trigger_row.trigger_price == Decimal("94.0000")


@pytest.mark.integration
def test_trailing_short_ask_missing_falls_back_to_last(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    """Spec §測試要求 line 380: bid/ask 缺失 last fallback 路徑各方向。
    Short 對應 ask 缺失走 last_fallback (long 已由
    test_trailing_long_bid_missing_falls_back_to_last 覆蓋)。"""
    owner = uuid4()
    intent_id = _create_active_trailing_intent(
        repo, db_session, owner_user_id=owner, position_side="short", trail_value="5"
    )

    # Seed watermark_low at 100 → dynamic = 100 × 1.05 = 105.0
    quote_provider.push_quote(_snapshot("2330", bid="99.5", ask="100", last="100"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    # ask missing, last=106 ≥ dynamic 105.0 → trigger via last_fallback
    quote_provider.push_quote(_snapshot("2330", bid="105.5", last="106"))
    response = client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})
    assert response.json()["data"]["triggeredIntentIds"] == [str(intent_id)]

    trigger_row = db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
    assert trigger_row.trigger_reference_price_type == "last_fallback"
    assert trigger_row.fallback_used is True
    assert trigger_row.trigger_price == Decimal("106.0000")


@pytest.mark.integration
def test_trailing_long_and_short_coexist_independently(
    db_session: Session,
    client: TestClient,
    repo: IntentRepository,
    quote_provider: InMemoryQuoteProvider,
) -> None:
    """Same symbol, same trail_value, long + short — unique index respects position_side."""
    owner = uuid4()
    long_id = _create_active_trailing_intent(
        repo, db_session, owner_user_id=owner, position_side="long", trail_value="5"
    )
    short_id = _create_active_trailing_intent(
        repo, db_session, owner_user_id=owner, position_side="short", trail_value="5"
    )

    # Quote moves both watermarks (long: high, short: low) since both start NULL.
    quote_provider.push_quote(_snapshot("2330", bid="99.5", ask="100", last="100"))
    client.post("/dev/evaluate-quotes", json={"symbols": ["2330"]})

    long_after = repo.find_by_id(long_id, owner)
    short_after = repo.find_by_id(short_id, owner)
    assert long_after.watermark_high == Decimal("100")
    assert long_after.watermark_low is None
    assert short_after.watermark_low == Decimal("100")
    assert short_after.watermark_high is None
    # Neither should be triggered on this quote.
    assert long_after.status == "active"
    assert short_after.status == "active"
