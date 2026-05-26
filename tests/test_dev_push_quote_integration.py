"""Integration tests for ``POST /dev/push-quote``.

Covers the end-to-end flow used by the /test page demo:
create intent → push synthetic quote → dispatcher listener fires →
``trigger_events`` and ``notifications`` rows land in the DB.
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
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Notification, Symbol, TriggerEvent
from app.main import create_app

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_NOW = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI).astimezone(UTC)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def push_quote_engine() -> Generator[Engine]:
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
def client(push_quote_engine: Engine) -> Generator[TestClient]:
    with Session(push_quote_engine) as cleaner:
        cleaner.execute(text("TRUNCATE notifications, trigger_events, trade_intents CASCADE"))
        cleaner.commit()

    # `with TestClient(app)` runs lifespan → dispatcher listener is installed,
    # which is what we want to exercise here. The lifespan reads
    # `app.state.session_service`, so swap that *before* entering the context
    # manager (overriding only the dep is not enough — the dispatcher would
    # silently see system time and drop pushed quotes as stale).
    app = create_app()
    app.state.session_service.set_clock(lambda: SESSION_NOW)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.mark.integration
def test_push_quote_triggers_existing_intent_via_dispatcher(
    client: TestClient,
    push_quote_engine: Engine,
) -> None:
    owner = uuid4()

    # 1. Create a buy_price_alert intent. The lifespan listener has been
    #    installed at this point, but no quote exists yet so create stays
    #    `active` (no immediate trigger).
    create = client.post(
        "/trade-intents",
        json={"strategy": "buy_price_alert", "symbol": "2330", "quantityLots": 1, "targetPrice": "600"},
        headers={"X-Local-User-Id": str(owner)},
    )
    assert create.status_code == 201, create.text
    intent_id = UUID(create.json()["data"]["id"])
    assert create.json()["data"]["status"] == "active"

    # 2. Push a quote that meets the buy condition (ask <= target).
    push = client.post(
        "/dev/push-quote",
        json={"symbol": "2330", "askPrice": "599"},
        headers={"X-Local-User-Id": str(owner)},
    )
    assert push.status_code == 200, push.text
    assert push.json()["data"]["symbol"] == "2330"

    # 3. Dispatcher listener should have fired synchronously → DB has both
    #    a trigger_event and a price_triggered notification.
    with Session(push_quote_engine) as verify:
        trigger = verify.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).scalar_one()
        notif = verify.execute(select(Notification).where(Notification.trade_intent_id == intent_id)).scalar_one()
    assert trigger.trigger_reference_price_type == "ask"
    assert notif.type == "price_triggered"
    assert notif.owner_user_id == owner


@pytest.mark.integration
def test_push_quote_below_target_does_not_trigger(client: TestClient, push_quote_engine: Engine) -> None:
    owner = uuid4()
    create = client.post(
        "/trade-intents",
        json={"strategy": "buy_price_alert", "symbol": "2330", "quantityLots": 1, "targetPrice": "600"},
        headers={"X-Local-User-Id": str(owner)},
    )
    intent_id = UUID(create.json()["data"]["id"])

    # ask above target → no trigger
    push = client.post("/dev/push-quote", json={"symbol": "2330", "askPrice": "601"})
    assert push.status_code == 200

    with Session(push_quote_engine) as verify:
        rows = verify.execute(select(TriggerEvent).where(TriggerEvent.trade_intent_id == intent_id)).all()
    assert rows == []


@pytest.mark.integration
def test_push_quote_rejects_payload_with_no_price(client: TestClient) -> None:
    response = client.post("/dev/push-quote", json={"symbol": "2330"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
