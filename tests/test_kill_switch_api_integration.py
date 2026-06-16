"""HTTP integration tests for the L2 kill switch, against real PostgreSQL.

Covers: admin toggle on/off writes audit + GET reflects state; reason is required;
non-admin / unverified-2FA are blocked; and — the core safety property — when the
switch is on, a create whose price condition is already met commits as `active`
WITHOUT a TriggerEvent or notification, then triggers again once the switch is off.
"""

from collections.abc import Generator
from datetime import UTC, datetime
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

from app.api.deps import (
    get_active_user,
    get_current_user,
    get_idempotency_key,
    get_quote_provider,
    get_trading_session_service,
)
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.auth import AuditEvent
from app.db.models.core import Notification, Symbol, TriggerEvent
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from app.services.quote.base import QuoteSnapshot
from app.services.quote.in_memory import InMemoryQuoteProvider

TAIPEI = ZoneInfo("Asia/Taipei")
# Monday inside the regular session, so a created day-intent lands `active`.
SESSION_NOW_TAIPEI = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
SESSION_NOW_UTC = SESSION_NOW_TAIPEI.astimezone(UTC)
SESSION_QUOTE_TIME = datetime(2026, 5, 11, 9, 59, 55, tzinfo=TAIPEI)

# Seeded by the L1 migration as an active admin — usable as both the kill-switch
# actor and the intent owner.
ADMIN_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    eng = create_engine(get_settings().database_url or "")
    with Session(eng) as session:
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
        yield eng
    finally:
        with eng.begin() as conn:
            conn.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
        eng.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session]:
    session = Session(engine)
    session.execute(text("TRUNCATE notifications, trigger_events, trade_intents, system_flags, audit_events CASCADE"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def quote_provider() -> InMemoryQuoteProvider:
    return InMemoryQuoteProvider()


def _client(quote_provider: InMemoryQuoteProvider, principal: RequestUser) -> TestClient:
    session_with_clock = TradingSessionService(clock=lambda: SESSION_NOW_UTC)
    app = create_app()
    app.dependency_overrides[get_quote_provider] = lambda: quote_provider
    app.dependency_overrides[get_trading_session_service] = lambda: session_with_clock
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    # Unique key per request → the real manager runs transparently (no replay/conflict).
    app.dependency_overrides[get_idempotency_key] = lambda: str(uuid4())
    return TestClient(app)


@pytest.fixture
def admin_client(quote_provider: InMemoryQuoteProvider) -> TestClient:
    # Each test builds its own create_app(); overrides are local to that app, so no
    # teardown clear is needed.
    return _client(quote_provider, RequestUser(user_id=ADMIN_USER_ID, role="admin", mfa_verified=True))


def _snapshot(ask: str = "99.5") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="2330",
        quote_time=SESSION_QUOTE_TIME,
        received_at=SESSION_QUOTE_TIME,
        ask_price=Decimal(ask),
        bid_price=None,
        last_price=None,
    )


def _create_payload(target: str = "99.5") -> dict[str, object]:
    return {"symbol": "2330", "strategy": "buy_price_alert", "quantityLots": 1, "targetPrice": target}


@pytest.mark.integration
def test_toggle_on_then_off_writes_audit_and_get_reflects_state(db_session: Session, admin_client: TestClient) -> None:
    on = admin_client.post("/admin/kill-switch", json={"enabled": True, "reason": "市場異常先全停"})
    assert on.status_code == 200
    assert on.json()["enabled"] is True

    state = admin_client.get("/admin/kill-switch")
    assert state.status_code == 200
    body = state.json()
    assert body["enabled"] is True
    assert body["reason"] == "市場異常先全停"
    assert body["updatedBy"] == str(ADMIN_USER_ID)

    off = admin_client.post("/admin/kill-switch", json={"enabled": False, "reason": "恢復"})
    assert off.status_code == 200
    assert off.json()["enabled"] is False

    events = db_session.execute(select(AuditEvent).order_by(AuditEvent.occurred_at)).scalars().all()
    types = [e.event_type for e in events]
    assert types == ["kill_switch_enabled", "kill_switch_disabled"]
    assert events[0].actor_type == "admin"
    assert events[0].actor_id == ADMIN_USER_ID
    assert events[0].event_metadata["reason"] == "市場異常先全停"


@pytest.mark.integration
def test_reason_is_required(db_session: Session, admin_client: TestClient) -> None:
    resp = admin_client.post("/admin/kill-switch", json={"enabled": True})
    assert resp.status_code == 422
    blank = admin_client.post("/admin/kill-switch", json={"enabled": True, "reason": "   "})
    assert blank.status_code == 422


@pytest.mark.integration
def test_non_admin_and_unverified_2fa_are_blocked(db_session: Session, quote_provider: InMemoryQuoteProvider) -> None:
    user_client = _client(quote_provider, RequestUser(user_id=ADMIN_USER_ID, role="user", mfa_verified=True))
    resp = user_client.post("/admin/kill-switch", json={"enabled": True, "reason": "x"})
    assert resp.status_code == 403

    no_mfa_client = _client(quote_provider, RequestUser(user_id=ADMIN_USER_ID, role="admin", mfa_verified=False))
    resp = no_mfa_client.post("/admin/kill-switch", json={"enabled": True, "reason": "x"})
    assert resp.status_code == 403


@pytest.mark.integration
def test_create_with_met_condition_does_not_trigger_while_halted_then_resumes(
    db_session: Session, admin_client: TestClient, quote_provider: InMemoryQuoteProvider
) -> None:
    quote_provider.push_quote(_snapshot(ask="99.5"))  # ask == target → condition met

    # Halt, then create an intent whose condition is already met.
    admin_client.post("/admin/kill-switch", json={"enabled": True, "reason": "halt"})
    halted = admin_client.post("/trade-intents", json=_create_payload(target="99.5"))
    assert halted.status_code == 201
    halted_id = UUID(halted.json()["data"]["id"])
    assert halted.json()["data"]["status"] == "active"  # NOT triggered
    assert db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == halted_id)).first() is None
    assert db_session.execute(select(Notification).where(Notification.trade_intent_id == halted_id)).first() is None

    # Cancel the halted intent so the duplicate-active guard doesn't block the
    # resume create below (same symbol + strategy).
    assert admin_client.post(f"/trade-intents/{halted_id}/cancel").status_code == 200

    # Resume — a fresh create with the same met condition triggers immediately.
    # Re-push: cancelling the only intent on 2330 unsubscribed it, dropping the
    # provider's stored snapshot.
    admin_client.post("/admin/kill-switch", json={"enabled": False, "reason": "resume"})
    quote_provider.push_quote(_snapshot(ask="99.5"))
    resumed = admin_client.post("/trade-intents", json=_create_payload(target="99.5"))
    assert resumed.status_code == 201
    resumed_id = UUID(resumed.json()["data"]["id"])
    assert resumed.json()["data"]["status"] == "triggered"
    assert (
        db_session.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == resumed_id)).scalar_one()
        is not None
    )
