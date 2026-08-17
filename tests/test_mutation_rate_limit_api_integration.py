"""HTTP integration test for the §13 global mutating-endpoint rate limit.

A small capacity is injected via env so the bucket depletes in two requests. The
bucket is keyed per user and shared across endpoints, so create depletes it and a
following cancel is rejected before its own logic runs. A dedicated seeded user
keeps this small-capacity bucket isolated from other integration tests' creates.
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

from app.api.deps import get_active_user, get_current_user, get_idempotency_key, get_trading_session_service
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.auth import User
from app.db.models.core import Symbol
from app.domain.trading_session import TradingSessionService
from app.main import create_app
from tests.db_helpers import INTENT_TABLES

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_NOW_UTC = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI).astimezone(UTC)  # Monday in-session

RATE_USER_ID = UUID("00000000-0000-0000-0000-0000000000aa")


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
        session.add(User(id=RATE_USER_ID, email="rate-limit@local.invalid", role="user", status="active"))
        session.commit()
    try:
        yield eng
    finally:
        with eng.begin() as conn:
            conn.execute(text(f"TRUNCATE {INTENT_TABLES} CASCADE"))
        eng.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session]:
    session = Session(engine)
    session.execute(text(f"TRUNCATE {INTENT_TABLES}, rate_limit_buckets CASCADE"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    monkeypatch.setenv("MUTATION_RATE_LIMIT_CAPACITY", "2")
    monkeypatch.setenv("MUTATION_RATE_LIMIT_REFILL_PER_SECOND", "0.01")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_trading_session_service] = lambda: TradingSessionService(clock=lambda: SESSION_NOW_UTC)
    app.dependency_overrides[get_idempotency_key] = lambda: str(uuid4())
    principal = RequestUser(user_id=RATE_USER_ID, role="user")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_active_user] = lambda: principal
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _payload(target: str) -> dict[str, object]:
    return {"symbol": "2330", "strategy": "buy_price_alert", "quantityLots": 1, "targetPrice": target}


@pytest.mark.integration
def test_mutation_bucket_shared_across_create_and_cancel(client: TestClient) -> None:
    # capacity 2 → two mutations allowed (distinct targets dodge the duplicate guard).
    assert client.post("/trade-intents", json=_payload("100")).status_code == 201
    assert client.post("/trade-intents", json=_payload("101")).status_code == 201

    # Bucket empty: the next mutation — a cancel on a different endpoint — is rejected
    # before the cancel logic runs (so a non-existent id still yields 429, not 404).
    over = client.post(f"/trade-intents/{uuid4()}/cancel")
    assert over.status_code == 429
    body = over.json()["error"]
    assert body["code"] == "RATE_LIMITED"
    assert over.headers.get("Retry-After") is not None
    assert body["details"]["retryAfterSeconds"] >= 1
