"""Integration tests for the Postgres token-bucket RateLimiter against real PG."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rate_limiter import RateLimiter

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def rl_engine() -> Generator[Engine]:
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
def test_consume_depletes_then_refills_over_time(rl_engine: Engine) -> None:
    bucket = f"test:{uuid4()}"
    with Session(rl_engine) as session:
        limiter = RateLimiter(session)

        # Capacity 5: the first five consumes at t0 all succeed.
        for _ in range(5):
            assert limiter.consume(bucket, capacity=5, refill_per_second=1.0, now=_T0).allowed is True
        session.commit()

        # Sixth at t0 is denied, with a finite retry_after (1 token / 1 per second).
        denied = limiter.consume(bucket, capacity=5, refill_per_second=1.0, now=_T0)
        assert denied.allowed is False
        assert denied.retry_after_seconds == pytest.approx(1.0)
        session.commit()

        # Two seconds later, two tokens have refilled, so the next consume passes.
        refilled = limiter.consume(bucket, capacity=5, refill_per_second=1.0, now=_T0 + timedelta(seconds=2))
        assert refilled.allowed is True
        assert refilled.remaining == pytest.approx(1.0)
        session.commit()


@pytest.mark.integration
def test_bucket_state_persists_across_sessions(rl_engine: Engine) -> None:
    bucket = f"test:{uuid4()}"

    # Drain a capacity-2, non-refilling bucket in one transaction.
    with Session(rl_engine) as session:
        limiter = RateLimiter(session)
        assert limiter.consume(bucket, capacity=2, refill_per_second=0.0, now=_T0).allowed is True
        assert limiter.consume(bucket, capacity=2, refill_per_second=0.0, now=_T0).allowed is True
        session.commit()

    # A brand-new session sees the depleted bucket and is denied.
    with Session(rl_engine) as session:
        denied = RateLimiter(session).consume(bucket, capacity=2, refill_per_second=0.0, now=_T0)
        assert denied.allowed is False
        assert denied.retry_after_seconds == float("inf")
        session.commit()
