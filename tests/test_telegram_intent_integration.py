from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Symbol, TelegramIntentInteraction
from app.db.models.trade_intent_core import TradeIntentCore
from app.repositories.telegram_intent_repository import TelegramIntentInteractionRepository
from tests.db_helpers import ensure_user

OWNER_ID = UUID("000000aa-0000-0000-0000-000000000001")


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture
def migrated_engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.integration
def test_telegram_interaction_has_core_fk_and_one_shared_pending_draft(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    foreign_keys = inspector.get_foreign_keys("telegram_intent_interactions")
    fk_targets = {fk["referred_table"] for fk in foreign_keys}
    assert "trade_intent_core" in fk_targets
    assert "trade_intents" not in fk_targets

    with Session(migrated_engine) as db:
        ensure_user(db, OWNER_ID)
        db.add(
            Symbol(
                id=uuid4(),
                symbol="2330",
                display_name="測試標的",
                market="TWSE",
                instrument_type="stock",
                tradable_status="tradable",
            )
        )
        intent = TradeIntentCore(
            id=uuid4(),
            owner_user_id=OWNER_ID,
            symbol="2330",
            strategy="limit_buy_order",
            execution_mode="notify_only",
            quantity_lots=1,
            trigger_reference_price_type="ask",
            trading_date=date(2026, 8, 19),
            time_in_force="day",
            status="active",
            transaction_mode="single_notification",
            dedup_key="180",
        )
        db.add(intent)
        db.flush()
        intent_id = intent.id
        interaction = TelegramIntentInteraction(
            id=uuid4(),
            owner_user_id=OWNER_ID,
            telegram_chat_id="integration-group",
            kind="draft",
            capability_id="limit_buy",
            strategy="limit_buy_order",
            payload={"symbol": "2330", "quantityLots": 1, "targetPrice": "180"},
            missing_fields=[],
            clarification_attempt_count=0,
            status="pending",
            expires_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
            created_trade_intent_id=intent_id,
        )
        db.add(interaction)
        interaction_id = interaction.id
        db.commit()

        db.add(
            TelegramIntentInteraction(
                id=uuid4(),
                owner_user_id=OWNER_ID,
                telegram_chat_id="integration-group",
                kind="draft",
                capability_id="limit_buy",
                strategy="limit_buy_order",
                payload={},
                missing_fields=[],
                clarification_attempt_count=0,
                status="confirming",
                expires_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        repository = TelegramIntentInteractionRepository(db)
        current = repository.find_pending_for_chat("integration-group", for_update=True)
        assert current is not None
        assert current.id == interaction_id
        assert repository.claim_confirming(interaction_id, chat_id="integration-group") is True
        db.commit()

        confirming = repository.find_pending_for_chat("integration-group", for_update=True)
        assert confirming is not None
        assert confirming.status == "confirming"
        assert repository.claim_confirming(interaction_id, chat_id="integration-group") is False
        assert (
            repository.mark_confirmed(
                interaction_id,
                chat_id="integration-group",
                confirmed_at=datetime(2026, 8, 19, 12, 1, tzinfo=UTC),
                trade_intent_id=intent_id,
            )
            is True
        )
        db.commit()

        confirmed = repository.find_by_id(interaction_id, chat_id="integration-group")
        assert confirmed is not None
        assert confirmed.status == "confirmed"
        assert confirmed.created_trade_intent_id == intent_id
        assert repository.mark_status(interaction_id, "cancelled", chat_id="integration-group") is False
