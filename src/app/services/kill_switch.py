"""KillSwitchProvider — process-wide read cache for the global trigger-halt flag.

The evaluator dispatch and the create-intent command both ask "are we halted?"
on every quote / create; the admin toggle flips the flag on a request thread and
calls `invalidate()`. In-process only (Postgres-everywhere / single-node — no
Redis). The TTL bounds staleness if the flag is ever flipped straight in the DB
without going through the toggle endpoint.

`is_halted` may be called from broker worker threads (via the dispatcher) and the
request thread concurrently, so cache reads/writes are guarded by a lock.
"""

import threading
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.repositories.system_flag_repository import SystemFlagRepository

GLOBAL_TRIGGER_HALT = "global_trigger_halt"

SessionFactory = Callable[[], Session]
MonotonicClock = Callable[[], float]


class KillSwitchProvider:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        ttl_seconds: float = 30.0,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cached_value: bool | None = None
        self._cached_until: float = 0.0

    def is_halted(self) -> bool:
        with self._lock:
            now = self._monotonic()
            if self._cached_value is not None and now < self._cached_until:
                return self._cached_value
            with self._session_factory() as db:
                value = SystemFlagRepository(db).is_enabled(GLOBAL_TRIGGER_HALT)
            self._cached_value = value
            self._cached_until = now + self._ttl_seconds
            return value

    def invalidate(self) -> None:
        """Drop the cache so the next `is_halted` re-reads the flag from the DB.

        Called by the toggle command after a commit so a flip takes effect at once
        instead of waiting out the TTL."""
        with self._lock:
            self._cached_value = None
            self._cached_until = 0.0
