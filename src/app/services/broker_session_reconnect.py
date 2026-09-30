"""Periodic repair of per-user broker sessions (docs/architecture.md「per-user 模式的生命週期」).

The broker closes every market-data websocket after the close and the SDK never
reconnects; a login can also be dropped (trade-side events 300 / 301 / 304). Both
only raise a flag on the provider — this loop is the single place that acts on
them, every `interval_seconds`, and only on a trading day between 08:30 and
13:35 Taipei so nothing thrashes against a broker that is closed for the day.

- socket down, login alive  -> `provider.reconnect_realtime()` (login stays,
  the provider resubscribes what it owns).
- login gone                -> the user goes into `_pending`; each tick tries
  `restore_bound_user` once (fresh login from the stored credentials, swap,
  retire the dead session). Failure keeps them pending, no backoff.

Runs `run_once` in a worker thread (`asyncio.to_thread`): the SDK's `connect()`
busy-spins for up to ~5 s and must not sit on the event loop. Never raises out
of a tick. Produces no health state and no notifications — repair only.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time
from uuid import UUID

from sqlalchemy.orm import Session

from app.commands.broker_account import restore_bound_user
from app.domain.trading_session import TradingSessionService
from app.repositories.broker_account_repository import BrokerAccountRepository
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.broker_session_pool import BrokerSessionPool

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

# ponytail: weekday gate only; the market-calendar table (V2) replaces is_trading_day.
REPAIR_WINDOW_START = time(8, 30)
REPAIR_WINDOW_END = time(13, 35)


class BrokerSessionReconnectLoop:
    def __init__(
        self,
        *,
        pool: BrokerSessionPool,
        session_factory: SessionFactory,
        accounts_for: Callable[[Session], BrokerAccountRepository],
        core_intents_for: Callable[[Session], TradeIntentCoreRepository],
        session_service: TradingSessionService,
        interval_seconds: float = 30.0,
    ) -> None:
        self._pool = pool
        self._session_factory = session_factory
        self._accounts_for = accounts_for
        self._core_intents_for = core_intents_for
        self._session_service = session_service
        self._interval_seconds = interval_seconds
        self._pending: set[UUID] = set()

    def pending_user_ids(self) -> set[UUID]:
        return set(self._pending)

    def run_once(self) -> None:
        try:
            self._tick()
        except Exception:
            logger.exception("broker session repair tick failed")

    async def run_forever(self) -> None:
        while True:
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(self._interval_seconds)

    def _tick(self) -> None:
        now = self._session_service.now_taipei()
        if not (
            self._session_service.is_trading_day(now.date()) and REPAIR_WINDOW_START <= now.time() < REPAIR_WINDOW_END
        ):
            return
        for user_id, provider in self._pool.live_sessions():
            # Duck-typed: only the Fubon provider has these; a provider without
            # them (test fakes, a future broker) is treated as healthy.
            if not getattr(provider, "login_alive", True):
                self._pending.add(user_id)
            elif not getattr(provider, "realtime_connected", True):
                try:
                    provider.reconnect_realtime()  # type: ignore[attr-defined]
                except Exception as exc:
                    logger.warning("realtime reconnect failed %s user_id=%s", type(exc).__name__, user_id)
        if self._pending:
            self._relogin_pending(now)

    def _relogin_pending(self, now: datetime) -> None:
        with self._session_factory() as db:
            accounts = self._accounts_for(db)
            core_intents = self._core_intents_for(db)
            for user_id in sorted(self._pending, key=str):
                outcome = restore_bound_user(
                    db=db, accounts=accounts, core_intents=core_intents, pool=self._pool, user_id=user_id, now=now
                )
                if outcome == "failed":
                    logger.warning("broker re-login failed user_id=%s; retrying next tick", user_id)
                    continue
                self._pending.discard(user_id)  # live again, or unbound meanwhile
                logger.info("broker session re-login user_id=%s outcome=%s", user_id, outcome)
