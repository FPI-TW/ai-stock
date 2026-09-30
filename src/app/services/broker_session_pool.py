"""One broker session per bound user, owned by the process.

`BrokerSessionPool` sits on `app.state.broker_sessions`. Two modes, fixed at
construction:

- **per-user** (`QUOTE_PROVIDER=fubon`, `shared=None`): `dict[user_id, provider]`,
  one `FubonQuoteProvider` per bound user, capped by `BROKER_MAX_SESSIONS`.
- **shared** (`in_memory` or the demo provider): `prepare` is refused with
  `BrokerBindingNotEnabledError` (409, not retryable) because there is no
  per-user login to verify.

Readers: `get` / `require` hand the create-intent and current-price paths the
caller's own session (the shared provider in shared mode). `set_quote_listener`
takes the core dispatcher's `dispatch(snapshot, *, owner_user_id)`; every session
that goes live gets it attached scoped to its owner, so a symbol N users watch is
evaluated once per owner against that owner's intents only (shared mode passes
`owner_user_id=None` and scans everyone, as before).

Replacing a user's session is a two-phase swap so a failed re-bind never takes
the working session down:

    candidate = pool.prepare(user_id, credentials)   # logs in OUTSIDE the lock
    ... subscribe, write DB, commit ...              # any failure -> pool.discard(candidate)
    old = pool.activate(candidate)                   # atomic dict swap, no I/O
    old.shutdown()                                   # outside the lock, best-effort

Tearing one down is symmetric, so a bind and an unbind for the same user can
never interleave (the DB row and the live session would otherwise drift apart):

    claim = pool.claim(user_id)                      # takes the same per-user token
    ... cancel intents, delete DB row, commit ...    # any failure -> pool.release(claim)
    pool.stop(claim)                                 # logout outside the lock, token released

Both `prepare` and `claim` hold the user's single operation token from the
moment they return until activate / discard / stop / release; whoever asks
second gets `BrokerBindInProgressError`.

Thread model: `threading.Lock` guards the dict / pending set only; every broker
call (login, logout, subscribe) happens outside it, so the pool lock never waits
on a provider lock. Callbacks from the SDK thread never enter this module.
"""

import logging
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Protocol
from uuid import UUID

from app.core.config import Settings
from app.domain.broker_account import (
    BrokerAccountNotBoundError,
    BrokerBindingNotEnabledError,
    BrokerBindInProgressError,
    BrokerLoginFailedError,
    BrokerLoginFailureCode,
    BrokerSessionLimitReachedError,
    BrokerSessionSetupError,
    FubonCredentials,
)
from app.services.quote.base import BrokerLoginError, QuoteProvider, QuoteProviderError, QuoteSnapshot
from app.services.quote.factory import build_quote_provider

logger = logging.getLogger(__name__)


class OwnerScopedQuoteListener(Protocol):
    """`TradeIntentCoreDispatcher.dispatch`: `owner_user_id=None` means "every owner"."""

    def __call__(self, snapshot: QuoteSnapshot, *, owner_user_id: UUID | None) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedBrokerSession:
    """A logged-in candidate that is not yet serving quotes for its user."""

    user_id: UUID
    provider: QuoteProvider
    broker_account_no: str


@dataclass(frozen=True, slots=True, eq=False)
class BrokerStopClaim:
    """The user's operation token held by an unbind in progress. Identity-compared."""

    user_id: UUID


def _failure_code(exc: Exception) -> BrokerLoginFailureCode | None:
    """The safe code for a *broker answer*; None when the exception is not one.

    `BrokerLoginError` (raised by any provider's client) already carries a domain
    code, so there is nothing to translate. Any other QuoteProviderError is the
    provider saying "not available". Everything else (ImportError for the missing
    wheel, AttributeError from SDK drift, ...) is ours to fix, not the admin's to
    retry, and is reported separately.
    """
    if isinstance(exc, BrokerLoginError):
        return exc.failure_code
    if isinstance(exc, QuoteProviderError):
        return "provider_unavailable"
    return None


def _frames_only(exc: BaseException) -> str:
    # Diagnosable without the message: SDK / socket text may echo login material.
    return "".join(traceback.format_tb(exc.__traceback__))


class BrokerSessionPool:
    def __init__(
        self,
        settings: Settings,
        *,
        shared: QuoteProvider | None = None,
        provider_factory: Callable[[FubonCredentials], QuoteProvider] | None = None,
    ) -> None:
        self._max = settings.broker_max_sessions
        self._quote_provider_name = settings.quote_provider
        self._shared = shared
        self._build = provider_factory or (lambda creds: build_quote_provider(settings, credentials=creds))
        self._lock = threading.Lock()
        self._sessions: dict[UUID, QuoteProvider] = {}
        # user_id -> whoever holds this user's single in-flight operation token: a
        # placeholder while logging in, then the candidate; or an unbind's claim.
        self._pending: dict[UUID, object] = {}
        self._listener: OwnerScopedQuoteListener | None = None

    # --- readers ----------------------------------------------------------------

    @property
    def per_user(self) -> bool:
        return self._shared is None

    @property
    def shared_provider(self) -> QuoteProvider | None:
        """The one provider everyone shares (None in per-user mode). Its startup /
        shutdown belong to the lifespan, not to the pool."""
        return self._shared

    def get(self, user_id: UUID) -> QuoteProvider | None:
        """The session serving `user_id`'s quotes, or None when they have none."""
        if self._shared is not None:
            return self._shared
        with self._lock:
            return self._sessions.get(user_id)

    def require(self, user_id: UUID) -> QuoteProvider:
        provider = self.get(user_id)
        if provider is None:
            raise BrokerAccountNotBoundError()
        return provider

    def live_sessions(self) -> list[tuple[UUID, QuoteProvider]]:
        """Snapshot of the per-user sessions (empty in shared mode)."""
        with self._lock:
            return list(self._sessions.items())

    def set_quote_listener(self, listener: OwnerScopedQuoteListener) -> None:
        """Attach the dispatcher to every session, present and future, scoped to
        the session's owner. Called once by the lifespan."""
        self._listener = listener
        if self._shared is not None:
            self._shared.add_quote_listener(partial(listener, owner_user_id=None))
            return
        for user_id, provider in self.live_sessions():
            provider.add_quote_listener(partial(listener, owner_user_id=user_id))

    # --- two-phase replace ----------------------------------------------------

    def prepare(self, user_id: UUID, credentials: FubonCredentials) -> PreparedBrokerSession:
        """Log in a candidate session for `user_id` without touching the live one.

        Takes the user's operation token and reserves a slot (only for a first
        bind — replacing an existing session needs no extra slot) inside the lock,
        then logs in outside it. On failure the token and slot are released and
        the login failure is raised as a safe `BrokerLoginFailedError`.
        """
        if self._shared is not None:
            # Nothing per-user to log into; refusing keeps `prepare -> DB row` honest.
            # A dedicated, non-retryable error: this is configuration, not a broker outage.
            raise BrokerBindingNotEnabledError(self._quote_provider_name)
        with self._lock:
            if user_id in self._pending:
                raise BrokerBindInProgressError()
            # Slots in use = live sessions + first-bind candidates still logging in.
            # An unbind's claim holds the user's token but never a slot: the session
            # it will stop is already counted (or never existed).
            reserved = len(self._sessions) + sum(
                1
                for uid, holder in self._pending.items()
                if uid not in self._sessions and not isinstance(holder, BrokerStopClaim)
            )
            if user_id not in self._sessions and reserved >= self._max:
                raise BrokerSessionLimitReachedError(self._max)
            self._pending[user_id] = object()
        provider: QuoteProvider | None = None
        logged_in = False
        try:
            provider = self._build(credentials)
            provider.startup()
            logged_in = True  # startup() cleans up after its own failures; we own it from here
            candidate = PreparedBrokerSession(
                user_id=user_id,
                provider=provider,
                broker_account_no=str(getattr(provider, "broker_account_no", "") or ""),
            )
            with self._lock:
                self._pending[user_id] = candidate
        except Exception as exc:
            with self._lock:
                self._pending.pop(user_id, None)
            if logged_in and provider is not None:
                # Logged in, then something after startup() raised: don't leak the login.
                retire_broker_session(provider, user_id)
            code = _failure_code(exc)
            if code is None:
                logger.error(
                    "broker session setup failed user_id=%s exception=%s\n%s",
                    user_id,
                    type(exc).__name__,
                    _frames_only(exc),
                )
                raise BrokerSessionSetupError(type(exc).__name__) from exc
            logger.warning(
                "broker session prepare failed user_id=%s code=%s exception=%s", user_id, code, type(exc).__name__
            )
            raise BrokerLoginFailedError(code) from exc
        return candidate

    def activate(self, candidate: PreparedBrokerSession) -> QuoteProvider | None:
        """Atomically make `candidate` the user's live session. No network, no DB.
        Returns the replaced provider (retire it outside the lock) or None."""
        if self._listener is not None:
            # Before the swap so no frame is lost between "live" and "listened to".
            # Outside the lock: the provider takes its own lock to add a listener.
            candidate.provider.add_quote_listener(partial(self._listener, owner_user_id=candidate.user_id))
        with self._lock:
            if self._pending.get(candidate.user_id) is not candidate:
                raise BrokerBindInProgressError()
            old = self._sessions.get(candidate.user_id)
            self._sessions[candidate.user_id] = candidate.provider
            del self._pending[candidate.user_id]
        return old

    def discard(self, candidate: PreparedBrokerSession) -> None:
        """Drop a candidate that will not be activated; releases token + slot first."""
        with self._lock:
            if self._pending.get(candidate.user_id) is candidate:
                del self._pending[candidate.user_id]
        retire_broker_session(candidate.provider, candidate.user_id)

    # --- teardown ---------------------------------------------------------------

    def claim(self, user_id: UUID) -> BrokerStopClaim:
        """Take the user's operation token for an unbind. Refused while a bind (or
        another unbind) for the same user is mid-flight. Pair with `stop` or `release`."""
        with self._lock:
            if user_id in self._pending:
                raise BrokerBindInProgressError()
            claim = BrokerStopClaim(user_id=user_id)
            self._pending[user_id] = claim
        return claim

    def release(self, claim: BrokerStopClaim) -> None:
        """Give the token back without stopping anything (the unbind's DB work failed)."""
        with self._lock:
            if self._pending.get(claim.user_id) is claim:
                del self._pending[claim.user_id]

    def stop(self, claim: BrokerStopClaim) -> None:
        """Log the user's live session out (no-op when absent) and release the claim."""
        with self._lock:
            if self._pending.get(claim.user_id) is not claim:
                raise BrokerBindInProgressError()
            provider = self._sessions.pop(claim.user_id, None)
            del self._pending[claim.user_id]
        if provider is not None:
            retire_broker_session(provider, claim.user_id)

    def stop_all(self) -> None:
        """Log every live session out, in parallel.

        Each logout may wait up to ~3 s for the market-data socket's close frame
        (verified with the SDK), so N sequential logouts would exceed a container's
        stop grace period once N grows; sessions left over then keep occupying the
        broker's connection cap until it times them out. Parallel keeps the worst
        case at one logout regardless of N (N ≤ BROKER_MAX_SESSIONS ≤ 10).
        """
        with self._lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()
        if not sessions:
            return
        with ThreadPoolExecutor(max_workers=len(sessions), thread_name_prefix="broker-logout") as pool:
            for user_id, provider in sessions:
                pool.submit(retire_broker_session, provider, user_id)


def retire_broker_session(provider: QuoteProvider, user_id: UUID) -> None:
    """Best-effort logout of a session that is no longer (or never became) live.

    Never raises: an already-dropped socket or a broker that stopped answering is
    not a reason to fail the operation that retired the session, and the log line
    carries only the exception class — this is the one place that rule lives.
    """
    try:
        provider.shutdown()
    except Exception as exc:
        logger.warning("broker session shutdown raised %s user_id=%s", type(exc).__name__, user_id)
