"""Periodic GC for the idempotency_keys table.

Every create/cancel writes one idempotency record; only same-key reuse deletes the
old row inline, so without this sweep the table grows without bound. This deletes
records past their 24h expires_at on an interval (default hourly), backed by the
ix_idempotency_keys_expires_at index. Mirrors TwapSliceScheduler's shape: a single
short-lived session per pass, exceptions swallowed so the loop never dies.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.repositories.idempotency_repository import IdempotencyRepository

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class IdempotencyCleanupScheduler:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        interval_seconds: float,
        clock: Clock = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._interval_seconds = interval_seconds
        self._clock = clock

    def run_once(self) -> int:
        try:
            with self._session_factory() as db:
                deleted = IdempotencyRepository(db).delete_expired(self._clock())
                db.commit()
            if deleted:
                logger.info("idempotency cleanup removed %d expired record(s)", deleted)
            return deleted
        except Exception:
            logger.exception("idempotency cleanup pass failed")
            return 0

    async def run_forever(self) -> None:
        while True:
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(self._interval_seconds)
