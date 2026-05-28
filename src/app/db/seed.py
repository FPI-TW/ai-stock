"""V0.5 minimal symbol seed.

Idempotent upsert of a fixed local symbol set. No external fetching —
V1 importer is expected to supersede this.
"""

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.core import Symbol
from app.db.session import get_session_factory

logger = logging.getLogger(__name__)


PRODUCTION_SYMBOL_SEED: list[dict[str, Any]] = [
    {
        "symbol": "2330",
        "display_name": "台積電",
        "market": "TWSE",
        "instrument_type": "stock",
        "tradable_status": "tradable",
    },
    {
        "symbol": "2317",
        "display_name": "鴻海",
        "market": "TWSE",
        "instrument_type": "stock",
        "tradable_status": "tradable",
    },
    {
        "symbol": "0050",
        "display_name": "元大台灣50",
        "market": "TWSE",
        "instrument_type": "etf",
        "tradable_status": "tradable",
    },
    {
        "symbol": "00878",
        "display_name": "國泰永續高股息",
        "market": "TWSE",
        "instrument_type": "etf",
        "tradable_status": "tradable",
    },
]

# 測試專用標的：用來驗證 halted tradable_status 在 service / API 層的拒絕行為。
# V1 importer 上線後應從 seed 移除，改放到 test fixture。
TEST_FIXTURE_SYMBOL_SEED: list[dict[str, Any]] = [
    {
        "symbol": "9999",
        "display_name": "測試停牌股票",
        "market": "TWSE",
        "instrument_type": "stock",
        "tradable_status": "halted",
    },
]

SYMBOL_SEED: list[dict[str, Any]] = PRODUCTION_SYMBOL_SEED + TEST_FIXTURE_SYMBOL_SEED


def seed_symbols(db: Session) -> None:
    for entry in SYMBOL_SEED:
        stmt = (
            insert(Symbol)
            .values(id=uuid4(), **entry)
            .on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "display_name": entry["display_name"],
                    "market": entry["market"],
                    "instrument_type": entry["instrument_type"],
                    "tradable_status": entry["tradable_status"],
                },
            )
        )
        db.execute(stmt)

    db.commit()
    logger.info("Seeded %d symbols", len(SYMBOL_SEED))


if __name__ == "__main__":
    session_factory = get_session_factory()
    with session_factory() as db_session:
        seed_symbols(db_session)
