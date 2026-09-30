"""Periodic repair of per-user broker sessions (docs/architecture.md「per-user 模式的生命週期」).

The broker closes every market-data websocket after the close and the SDK never
reconnects; a login can also be dropped (trade-side events 300 / 301 / 304). Both
only raise a flag on the provider — this loop is the single place that acts on
them, every `interval_seconds`, and only on a trading day between 08:30 and
13:35 Taipei so nothing thrashes against a broker that is closed for the day.

Every tick walks the stored bindings (`broker_accounts`) against the sessions
that are live now:

- live, login alive, socket down -> `provider.reconnect_realtime()` (login
  stays, the provider resubscribes what it owns).
- live but login gone, or no session at all (login failed at boot — e.g. a
  crash-restart inside the broker's ~60 s residual-session window refused
  everyone with session_limit — or `activate` failed after a bind committed)
  -> `restore_bound_user` once per tick: fresh login from the stored
  credentials, swap, retire the dead session. Failure is recorded on the row and
  the next tick tries again; no backoff.
- a row whose last failure needs a human (`login_rejected`, `cert_invalid`,
  `credentials_unreadable`) is skipped: retrying cannot succeed and repeated
  refused logins risk the broker locking the account. The admin re-binds.
- a row with no session at all only gets a login while the pool has a free
  slot (`session_pool_full` rows wait for an unbind / disable, no churn).

Nothing is remembered between ticks. A session the admin stopped (disable /
unbind) or replaced (re-bind) is simply not there — or is healthy — and
`restore_bound_user` re-checks the user under the per-user token, so a login
can never be brought back for an account that was disabled while the loop was
looking at it.

Runs `run_once` in a worker thread (`asyncio.to_thread`): the SDK's `connect()`
busy-spins for up to ~5 s and must not sit on the event loop. Cancelling
`run_forever` waits for the tick in flight (a login cannot be interrupted) so the
lifespan's `pool.stop_all()` always runs after it. Never raises out of a tick.
Produces no health state and no notifications — repair only.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import time

from sqlalchemy.orm import Session

from app.commands.broker_account import restore_bound_user
from app.domain.broker_account import RETRYABLE_LOGIN_FAILURES, BrokerAccountData, login_failure_code_for
from app.domain.trading_session import TradingSessionService
from app.repositories.broker_account_repository import BrokerAccountRepository
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.repositories.user_repository import UserRepository
from app.services.broker_session_pool import BrokerSessionPool
from app.services.quote.fubon.provider import FubonQuoteProvider

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
        users_for: Callable[[Session], UserRepository],
        session_service: TradingSessionService,
        interval_seconds: float = 30.0,
    ) -> None:
        self._pool = pool
        self._session_factory = session_factory
        self._accounts_for = accounts_for
        self._core_intents_for = core_intents_for
        self._users_for = users_for
        self._session_service = session_service
        self._interval_seconds = interval_seconds

    def run_once(self) -> None:
        try:
            self._tick()
        except Exception:
            logger.exception("broker session repair tick failed")

    async def run_forever(self) -> None:
        while True:
            # A tick may be mid-login in its thread and cannot be interrupted. On
            # cancellation (shutdown) wait for it: the lifespan runs `pool.stop_all()`
            # right after awaiting this task, and a candidate that activated *after*
            # that would be a live broker session nobody ever logs out.
            tick = asyncio.ensure_future(asyncio.to_thread(self.run_once))
            try:
                await asyncio.shield(tick)
            except asyncio.CancelledError:
                await tick
                raise
            await asyncio.sleep(self._interval_seconds)

    def _tick(self) -> None:
        now = self._session_service.now_taipei()
        if not (
            self._session_service.is_trading_day(now.date()) and REPAIR_WINDOW_START <= now.time() < REPAIR_WINDOW_END
        ):
            return
        live = dict(self._pool.live_sessions())
        free_slots = self._pool.free_slots()
        with self._session_factory() as db:
            accounts = self._accounts_for(db)
            core_intents = self._core_intents_for(db)
            users = self._users_for(db)
            for account in accounts.list_all():
                provider = live.get(account.user_id)
                # Per-user mode holds exactly one provider type (product.md: one
                # broker, use the concrete client); anything else is a wiring bug.
                if provider is not None and not isinstance(provider, FubonQuoteProvider):
                    logger.error("cannot repair %s user_id=%s", type(provider).__name__, account.user_id)
                    continue
                if provider is not None and provider.login_alive:
                    if not provider.realtime_connected:
                        try:
                            provider.reconnect_realtime()
                        except Exception as exc:
                            logger.warning(
                                "realtime reconnect failed %s user_id=%s", type(exc).__name__, account.user_id
                            )
                    continue
                if not _worth_retrying(account):
                    continue
                if provider is None:
                    # No session to replace: this needs a slot. A full pool would fail
                    # at `prepare` every tick and rewrite the row each time; wait for
                    # an unbind / disable to free one instead.
                    if free_slots <= 0:
                        continue
                    free_slots -= 1
                outcome = restore_bound_user(
                    db=db,
                    accounts=accounts,
                    core_intents=core_intents,
                    users=users,
                    pool=self._pool,
                    user_id=account.user_id,
                    now=now,
                )
                if outcome == "failed":
                    logger.warning("broker re-login failed user_id=%s; retrying next tick", account.user_id)
                else:
                    logger.info("broker session re-login user_id=%s outcome=%s", account.user_id, outcome)


def _worth_retrying(account: BrokerAccountData) -> bool:
    if account.status != "login_failed":
        return True  # bound and expected live: lost login, or activate failed after commit
    return login_failure_code_for(account.last_error) in RETRYABLE_LOGIN_FAILURES
