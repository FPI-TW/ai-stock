from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.core import Symbol
from app.db.seed import seed_symbols


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def seed_engine() -> Generator[Engine]:
    config = _alembic_config()
    database_url = get_settings().database_url
    if database_url is None:
        pytest.skip("DATABASE_URL not configured")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.mark.integration
def test_seed_symbols_idempotent(seed_engine: Engine) -> None:
    with Session(seed_engine) as db:
        seed_symbols(db)
        count_1 = db.query(Symbol).count()
        assert count_1 >= 6

        seed_symbols(db)
        count_2 = db.query(Symbol).count()
        assert count_1 == count_2


@pytest.mark.integration
def test_seed_data_correctness(seed_engine: Engine) -> None:
    with Session(seed_engine) as db:
        seed_symbols(db)

        tsmc: Any = db.execute(select(Symbol).where(Symbol.symbol == "2330")).scalar_one()
        assert tsmc.display_name == "台積電"
        assert tsmc.instrument_type == "stock"
        assert tsmc.tradable_status == "tradable"

        etf50: Any = db.execute(select(Symbol).where(Symbol.symbol == "0050")).scalar_one()
        assert etf50.symbol == "0050"
        assert isinstance(etf50.symbol, str)

        halted: Any = db.execute(select(Symbol).where(Symbol.symbol == "9999")).scalar_one()
        assert halted.tradable_status == "halted"
