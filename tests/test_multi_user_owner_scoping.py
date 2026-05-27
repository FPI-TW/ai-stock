"""End-to-end owner-scoping via the LOCAL_MODE X-Local-User-Id header.

Mirrors the /test page's multi-user demo flow: alice creates an intent, bob
lists and sees none, alice lists and sees hers. Exercises the full HTTP +
repo + owner-filter chain (the unit-level dep test is in
``test_deps_current_user.py``).
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

from app.api.deps import get_trading_session_service
from app.core.config import get_settings
from app.db.models.core import Symbol
from app.domain.trading_session import TradingSessionService
from app.main import create_app

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_NOW = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI).astimezone(UTC)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def multi_user_engine() -> Generator[Engine]:
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
def client(multi_user_engine: Engine) -> Generator[TestClient]:
    # Truncate between tests so list comparisons stay clean.
    with Session(multi_user_engine) as cleaner:
        cleaner.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
        cleaner.commit()

    app = create_app()
    # Fixed session clock inside regular trading hours so intents stay `active`
    # for the simple list assertions below.
    fixed_session = TradingSessionService(clock=lambda: SESSION_NOW)
    app.dependency_overrides[get_trading_session_service] = lambda: fixed_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _create_intent(client: TestClient, owner: UUID) -> str:
    response = client.post(
        "/trade-intents",
        json={
            "strategy": "buy_price_alert",
            "symbol": "2330",
            "quantityLots": 1,
            "targetPrice": "600",
        },
        headers={"X-Local-User-Id": str(owner)},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _list_intents(client: TestClient, owner: UUID) -> list[dict]:
    response = client.get("/trade-intents", headers={"X-Local-User-Id": str(owner)})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert isinstance(data, list)
    return data


@pytest.mark.integration
def test_owner_scoping_via_header(client: TestClient) -> None:
    alice = uuid4()
    bob = uuid4()

    intent_id = _create_intent(client, alice)

    bob_intents = _list_intents(client, bob)
    assert bob_intents == [], "bob must not see alice's intent"

    alice_intents = _list_intents(client, alice)
    assert any(i["id"] == intent_id for i in alice_intents), "alice must see her own intent"


@pytest.mark.integration
def test_owner_scoping_blocks_cross_user_detail(client: TestClient) -> None:
    alice = uuid4()
    bob = uuid4()

    intent_id = _create_intent(client, alice)

    response = client.get(
        f"/trade-intents/{intent_id}",
        headers={"X-Local-User-Id": str(bob)},
    )
    assert response.status_code == 404, "bob fetching alice's intent must look like 'not found'"
