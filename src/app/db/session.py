from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine | None:
    database_url = get_settings().database_url
    if not database_url:
        return None
    return create_engine(database_url, pool_pre_ping=True)


def check_database_connectivity() -> bool:
    engine = get_engine()
    if engine is None:
        return False

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False

    return True
