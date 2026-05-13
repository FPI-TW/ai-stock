from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.core import Symbol
from app.db.seed import seed_symbols
from app.db.session import get_engine


@pytest.fixture(scope="module")
def seed_engine() -> Generator[Engine]:
    """Integration test engine. Assumes alembic upgrade head 已執行。"""
    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL not configured")
    yield engine


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
