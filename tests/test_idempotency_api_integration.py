"""HTTP integration tests for §16 idempotency on create, against real PostgreSQL.

Exercises the real Idempotency-Key contract (no key override): replay returns the
original result without a second side effect, a reused key with a different body is a
409, and a missing key is a 400.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_active_user, get_current_user, get_trading_session_service
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.core import Symbol
from app.db.models.trade_intent_core import TradeIntentCore
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from app.repositories.idempotency_repository import IdempotencyRepository
from app.services.idempotency_cleanup import IdempotencyCleanupScheduler
from tests.db_helpers import INTENT_TABLES

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_NOW_UTC = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI).astimezone(UTC)  # Monday in-session

OWNER_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


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
                display_name="2330",
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
            conn.execute(text(f"TRUNCATE {INTENT_TABLES}, idempotency_keys CASCADE"))
        eng.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session]:
    session = Session(engine)
    session.execute(text(f"TRUNCATE {INTENT_TABLES}, idempotency_keys CASCADE"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_trading_session_service] = lambda: TradingSessionService(clock=lambda: SESSION_NOW_UTC)
    principal = RequestUser(user_id=OWNER_USER_ID, role="user")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    return TestClient(app)


def _payload(target: str = "100") -> dict[str, object]:
    return {"symbol": "2330", "strategy": "buy_price_alert", "quantityLots": 1, "targetPrice": target}


def _intent_count(db_session: Session) -> int:
    return int(db_session.execute(select(func.count()).select_from(TradeIntentCore)).scalar_one())


@pytest.mark.integration
def test_replay_returns_original_result_without_second_side_effect(db_session: Session, client: TestClient) -> None:
    headers = {"Idempotency-Key": "key-replay-1"}

    first = client.post("/trade-intents", json=_payload("100"), headers=headers)
    assert first.status_code == 201

    second = client.post("/trade-intents", json=_payload("100"), headers=headers)
    assert second.status_code == 201
    assert second.json() == first.json()  # identical replayed response
    assert _intent_count(db_session) == 1  # no duplicate intent created


@pytest.mark.integration
def test_same_key_different_payload_is_conflict(db_session: Session, client: TestClient) -> None:
    headers = {"Idempotency-Key": "key-conflict-1"}
    assert client.post("/trade-intents", json=_payload("100"), headers=headers).status_code == 201

    conflict = client.post("/trade-intents", json=_payload("101"), headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert _intent_count(db_session) == 1  # the conflicting create did not run


@pytest.mark.integration
def test_missing_idempotency_key_is_rejected(db_session: Session, client: TestClient) -> None:
    resp = client.post("/trade-intents", json=_payload("100"))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert _intent_count(db_session) == 0


@pytest.mark.integration
def test_cleanup_deletes_expired_records_only(engine: Engine, db_session: Session) -> None:
    now = datetime(2026, 6, 12, tzinfo=UTC)
    repo = IdempotencyRepository(db_session)
    repo.create(
        user_id=OWNER_USER_ID,
        key="expired",
        endpoint="create_intent",
        request_hash="h1",
        response_snapshot={"x": 1},
        now=now - timedelta(hours=48),
        expires_at=now - timedelta(hours=24),  # already expired
    )
    repo.create(
        user_id=OWNER_USER_ID,
        key="live",
        endpoint="create_intent",
        request_hash="h2",
        response_snapshot={"x": 2},
        now=now,
        expires_at=now + timedelta(hours=24),  # still live
    )
    db_session.commit()

    scheduler = IdempotencyCleanupScheduler(
        session_factory=lambda: Session(engine),
        interval_seconds=3600.0,
        clock=lambda: now,
    )
    deleted = scheduler.run_once()

    assert deleted == 1
    assert repo.get(OWNER_USER_ID, "expired") is None
    assert repo.get(OWNER_USER_ID, "live") is not None
