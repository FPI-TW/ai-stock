from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, MetaData, Table, create_engine
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings


def alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture
def migrated_engine() -> Generator[Engine]:
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def reflect_table(engine: Engine, table_name: str) -> Table:
    metadata = MetaData()
    return Table(table_name, metadata, autoload_with=engine)


@pytest.mark.integration
def test_migration_upgrade_creates_v0_5_schema(migrated_engine: Engine) -> None:
    inspector = sa.inspect(migrated_engine)

    assert {"symbols", "trade_intents", "trigger_events", "notifications"}.issubset(inspector.get_table_names())
    assert not {"audit_events", "outbox_events", "notification_deliveries", "import_reports", "users"}.intersection(
        inspector.get_table_names()
    )
    for table_name in ("symbols", "trade_intents", "trigger_events", "notifications"):
        assert "created_at" in {column["name"] for column in inspector.get_columns(table_name)}
    for table_name in ("symbols", "trade_intents", "notifications"):
        assert "updated_at" in {column["name"] for column in inspector.get_columns(table_name)}


@pytest.mark.integration
def test_price_columns_use_numeric_9_4(migrated_engine: Engine) -> None:
    inspector = sa.inspect(migrated_engine)

    expected_columns = {
        "trade_intents": {"target_price_original", "target_price_effective"},
        "trigger_events": {"target_price_effective", "trigger_price"},
    }
    for table_name, column_names in expected_columns.items():
        columns = {column["name"]: column["type"] for column in inspector.get_columns(table_name)}
        for column_name in column_names:
            column_type = columns[column_name]
            assert isinstance(column_type, sa.Numeric)
            assert column_type.precision == 9
            assert column_type.scale == 4


@pytest.mark.integration
def test_migration_downgrade_removes_v0_5_schema() -> None:
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(get_settings().database_url or "")
    try:
        inspector = sa.inspect(engine)
        assert not {"symbols", "trade_intents", "trigger_events", "notifications"}.intersection(
            inspector.get_table_names()
        )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_trade_intent_rejects_non_positive_quantity(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent = build_trade_intent(symbol="2330", quantity_lots=0)

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2330")
        with pytest.raises(IntegrityError):
            connection.execute(reflect_table(migrated_engine, "trade_intents").insert().values(**trade_intent))


@pytest.mark.integration
def test_trade_intent_rejects_price_over_numeric_precision(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent = build_trade_intent(symbol="1301", target_price_effective=Decimal("123456.7890"))

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "1301")
        with pytest.raises((IntegrityError, sa.exc.DataError)):
            connection.execute(reflect_table(migrated_engine, "trade_intents").insert().values(**trade_intent))


@pytest.mark.integration
def test_trade_intent_rejects_invalid_enum_value(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent = build_trade_intent(symbol="0050", strategy="take_profit_alert")

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "0050")
        with pytest.raises(IntegrityError):
            connection.execute(reflect_table(migrated_engine, "trade_intents").insert().values(**trade_intent))


@pytest.mark.integration
def test_symbol_rejects_uppercase_etf_instrument_type(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                reflect_table(migrated_engine, "symbols")
                .insert()
                .values(
                    id=uuid4(),
                    symbol="0050",
                    display_name="元大台灣50",
                    market="TWSE",
                    instrument_type="ETF",
                    tradable_status="tradable",
                )
            )


@pytest.mark.integration
def test_trigger_event_requires_unique_trade_intent_id(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2317")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2317"))
        )
        trigger_events = reflect_table(migrated_engine, "trigger_events")
        connection.execute(trigger_events.insert().values(**build_trigger_event(trade_intent_id)))

        with pytest.raises(IntegrityError):
            connection.execute(trigger_events.insert().values(**build_trigger_event(trade_intent_id)))


@pytest.mark.integration
def test_trigger_event_rejects_unknown_symbol(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2308")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2308"))
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                reflect_table(migrated_engine, "trigger_events")
                .insert()
                .values(**build_trigger_event(trade_intent_id, symbol="9999"))
            )


@pytest.mark.integration
def test_trigger_event_requires_fallback_consistency(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2882")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2882"))
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                reflect_table(migrated_engine, "trigger_events")
                .insert()
                .values(
                    **build_trigger_event(
                        trade_intent_id,
                        symbol="2882",
                        trigger_reference_price_type="last_fallback",
                        fallback_used=False,
                    )
                )
            )


@pytest.mark.integration
def test_trigger_event_allows_last_price_fallback(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2891")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2891"))
        )
        connection.execute(
            reflect_table(migrated_engine, "trigger_events")
            .insert()
            .values(
                **build_trigger_event(
                    trade_intent_id,
                    symbol="2891",
                    trigger_reference_price_type="last_fallback",
                    fallback_used=True,
                )
            )
        )


@pytest.mark.integration
def test_price_triggered_notification_requires_trade_intent(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                reflect_table(migrated_engine, "notifications")
                .insert()
                .values(
                    id=uuid4(),
                    owner_user_id=uuid4(),
                    trade_intent_id=None,
                    type="price_triggered",
                    rendered_title="已觸發",
                    rendered_body="提醒已觸發",
                )
            )


@pytest.mark.integration
def test_price_triggered_notification_accepts_trade_intent(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    trade_intent_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2303")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2303"))
        )
        connection.execute(
            reflect_table(migrated_engine, "notifications")
            .insert()
            .values(
                id=uuid4(),
                owner_user_id=uuid4(),
                trade_intent_id=trade_intent_id,
                type="price_triggered",
                rendered_title="已觸發",
                rendered_body="提醒已觸發",
            )
        )


@pytest.mark.integration
def test_trade_intent_rejects_duplicate_active_intent(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    owner_user_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2454")
        trade_intents = reflect_table(migrated_engine, "trade_intents")
        connection.execute(
            trade_intents.insert().values(**build_trade_intent(symbol="2454", owner_user_id=owner_user_id))
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                trade_intents.insert().values(
                    **build_trade_intent(symbol="2454", owner_user_id=owner_user_id, status="scheduled")
                )
            )


@pytest.mark.integration
def test_trade_intent_accepts_limit_order_strategies(migrated_engine: Engine) -> None:
    """BE-V0.5-15: limit_buy_order / limit_sell_order pass the strategy CHECK."""
    with migrated_engine.begin() as connection:
        insert_symbol(connection, uuid4(), "2603")
        trade_intents = reflect_table(migrated_engine, "trade_intents")
        for strategy in ("limit_buy_order", "limit_sell_order"):
            connection.execute(
                trade_intents.insert().values(
                    **build_trade_intent(symbol="2603", strategy=strategy, owner_user_id=uuid4())
                )
            )


@pytest.mark.integration
def test_trade_intent_rejects_unknown_transaction_mode(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        insert_symbol(connection, uuid4(), "2615")
        trade_intents = reflect_table(migrated_engine, "trade_intents")
        with pytest.raises(IntegrityError):
            connection.execute(
                trade_intents.insert().values(
                    **build_trade_intent(symbol="2615"),
                    transaction_mode="bogus",
                )
            )


@pytest.mark.integration
def test_trade_intent_defaults_partial_fill_columns_to_v0_5_baseline(
    migrated_engine: Engine,
) -> None:
    """Insert without specifying the new columns → DB server defaults kick in."""
    trade_intent_id = uuid4()
    with migrated_engine.begin() as connection:
        insert_symbol(connection, uuid4(), "2618")
        trade_intents = reflect_table(migrated_engine, "trade_intents")
        connection.execute(trade_intents.insert().values(**build_trade_intent(id=trade_intent_id, symbol="2618")))
        row = connection.execute(sa.select(trade_intents).where(trade_intents.c.id == trade_intent_id)).mappings().one()

    assert row["transaction_mode"] == "single_notification"
    assert row["notification_mode"] == "single"
    assert row["filled_quantity_lots"] == 0
    assert row["last_fill_at"] is None


@pytest.mark.integration
def test_trigger_event_rejects_negative_filled_quantity_lots(migrated_engine: Engine) -> None:
    trade_intent_id = uuid4()
    with migrated_engine.begin() as connection:
        insert_symbol(connection, uuid4(), "2884")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2884"))
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                reflect_table(migrated_engine, "trigger_events")
                .insert()
                .values(**build_trigger_event(trade_intent_id, symbol="2884", filled_quantity_lots=-1))
            )


@pytest.mark.integration
def test_notification_accepts_limit_order_triggered_type(migrated_engine: Engine) -> None:
    """BE-V0.5-15: limit_order_triggered is a valid notification type and still
    requires a linked trade_intent_id."""
    trade_intent_id = uuid4()
    with migrated_engine.begin() as connection:
        insert_symbol(connection, uuid4(), "2885")
        connection.execute(
            reflect_table(migrated_engine, "trade_intents")
            .insert()
            .values(**build_trade_intent(id=trade_intent_id, symbol="2885", strategy="limit_buy_order"))
        )
        notifications = reflect_table(migrated_engine, "notifications")
        connection.execute(
            notifications.insert().values(
                id=uuid4(),
                owner_user_id=uuid4(),
                trade_intent_id=trade_intent_id,
                type="limit_order_triggered",
                rendered_title="2885 限價買單已觸發",
                rendered_body="限價買單\n成交 1 張 / 委託 1 張",
            )
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                notifications.insert().values(
                    id=uuid4(),
                    owner_user_id=uuid4(),
                    trade_intent_id=None,
                    type="limit_order_triggered",
                    rendered_title="?",
                    rendered_body="?",
                )
            )


@pytest.mark.integration
def test_trade_intent_allows_duplicate_after_terminal_status(migrated_engine: Engine) -> None:
    symbol_id = uuid4()
    owner_user_id = uuid4()

    with migrated_engine.begin() as connection:
        insert_symbol(connection, symbol_id, "2412")
        trade_intents = reflect_table(migrated_engine, "trade_intents")
        connection.execute(
            trade_intents.insert().values(
                **build_trade_intent(symbol="2412", owner_user_id=owner_user_id, status="triggered")
            )
        )
        connection.execute(
            trade_intents.insert().values(
                **build_trade_intent(symbol="2412", owner_user_id=owner_user_id, status="active")
            )
        )


def insert_symbol(connection: sa.Connection, symbol_id: UUID, symbol: str) -> None:
    connection.execute(
        reflect_table(connection.engine, "symbols")
        .insert()
        .values(
            id=symbol_id,
            symbol=symbol,
            display_name=f"{symbol} Test",
            market="TWSE",
            instrument_type="stock",
            tradable_status="tradable",
        )
    )


def build_trade_intent(
    *,
    id: UUID | None = None,
    owner_user_id: UUID | None = None,
    symbol: str,
    quantity_lots: int = 1,
    strategy: str = "buy_price_alert",
    status: str = "active",
    target_price_effective: Decimal = Decimal("100.0000"),
) -> dict[str, object]:
    return {
        "id": id or uuid4(),
        "owner_user_id": owner_user_id or uuid4(),
        "symbol": symbol,
        "strategy": strategy,
        "execution_mode": "notify_only",
        "quantity_lots": quantity_lots,
        "target_price_original": Decimal("100.0000"),
        "target_price_effective": target_price_effective,
        "trigger_reference_price_type": "ask",
        "trading_date": date(2026, 5, 12),
        "time_in_force": "day",
        "status": status,
    }


def build_trigger_event(
    trade_intent_id: UUID,
    symbol: str = "2317",
    trigger_reference_price_type: str = "ask",
    fallback_used: bool = False,
    filled_quantity_lots: int = 1,
) -> dict[str, object]:
    return {
        "id": uuid4(),
        "trade_intent_id": trade_intent_id,
        "owner_user_id": uuid4(),
        "symbol": symbol,
        "quote_snapshot": {"symbol": symbol, "ask_price": "100.0000"},
        "target_price_effective": Decimal("100.0000"),
        "trigger_price": Decimal("100.0000"),
        "trigger_reference_price_type": trigger_reference_price_type,
        "fallback_used": fallback_used,
        "filled_quantity_lots": filled_quantity_lots,
        "triggered_at": datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
    }
