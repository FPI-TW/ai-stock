"""HTTP integration tests for the §15 creation caps, against real PostgreSQL.

Caps are injected small via a get_intent_limits override so the boundary is hit
without creating 200 rows. No quote is pushed, so every create stays `active` and
counts toward the caps.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.api.deps import (
    get_active_user,
    get_current_user,
    get_idempotency_key,
    get_intent_limits,
    get_trading_session_service,
)
from app.commands.trade_intent import IntentLimits
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.core import Symbol
from app.domain.trading_session import TradingSessionService
from app.main import create_app

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_NOW_UTC = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI).astimezone(UTC)  # Monday in-session

OWNER_USER_ID = UUID("00000000-0000-0000-0000-000000000001")  # seeded active admin/user


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
        for sym in ("2330", "2317"):
            session.add(
                Symbol(
                    id=uuid4(),
                    symbol=sym,
                    display_name=sym,
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
    session.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _client(limits: IntentLimits) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_trading_session_service] = lambda: TradingSessionService(clock=lambda: SESSION_NOW_UTC)
    app.dependency_overrides[get_intent_limits] = lambda: limits
    app.dependency_overrides[get_idempotency_key] = lambda: str(uuid4())
    principal = RequestUser(user_id=OWNER_USER_ID, role="user")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    return TestClient(app)


def _payload(symbol: str, target: str) -> dict[str, object]:
    return {"symbol": symbol, "strategy": "buy_price_alert", "quantityLots": 1, "targetPrice": target}


@pytest.mark.integration
def test_symbol_cap_rejects_21st_on_same_symbol(db_session: Session) -> None:
    # per_symbol small (=2); per_user high so the symbol cap is what trips.
    client = _client(IntentLimits(per_user=100, per_symbol=2))

    # Distinct target prices avoid the duplicate-active guard.
    assert client.post("/trade-intents", json=_payload("2330", "100")).status_code == 201
    assert client.post("/trade-intents", json=_payload("2330", "101")).status_code == 201

    over = client.post("/trade-intents", json=_payload("2330", "102"))
    assert over.status_code == 409
    body = over.json()["error"]
    assert body["code"] == "SYMBOL_INTENT_LIMIT_EXCEEDED"
    assert body["details"]["symbol"] == "2330"
    assert body["details"]["limit"] == 2
    assert body["details"]["current"] == 2


@pytest.mark.integration
def test_user_cap_rejects_when_total_at_limit(db_session: Session) -> None:
    # per_user small (=2); per_symbol high so the user cap is what trips (spread across symbols).
    client = _client(IntentLimits(per_user=2, per_symbol=100))

    assert client.post("/trade-intents", json=_payload("2330", "100")).status_code == 201
    assert client.post("/trade-intents", json=_payload("2317", "100")).status_code == 201

    over = client.post("/trade-intents", json=_payload("2317", "101"))
    assert over.status_code == 409
    body = over.json()["error"]
    assert body["code"] == "USER_INTENT_LIMIT_EXCEEDED"
    assert body["details"]["limit"] == 2
    assert body["details"]["current"] == 2
