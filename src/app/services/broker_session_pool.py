"""One broker session per bound user, owned by the process.

`BrokerSessionPool` sits on `app.state.broker_sessions`. Two modes, fixed at
construction:

- **per-user** (`QUOTE_PROVIDER=fubon`, `shared=None`): `dict[user_id, provider]`,
  one `FubonQuoteProvider` per bound user, capped by `BROKER_MAX_SESSIONS`.
- **shared** (`in_memory` or the demo provider): every user resolves to the one shared
  provider; `prepare` is refused because there is no per-user login to verify.

Replacing a user's session is a two-phase swap so a failed re-bind never takes
the working session down:

    candidate = pool.prepare(user_id, credentials)   # logs in OUTSIDE the lock
    ... subscribe, write DB, commit ...              # any failure -> pool.discard(candidate)
    old = pool.activate(candidate)                   # atomic dict swap, no I/O
    old.shutdown()                                   # outside the lock, best-effort

Thread model: `threading.Lock` guards the dict / pending set only; every broker
call (login, logout, subscribe) happens outside it. Callbacks from the SDK thread
never enter this module.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from app.core.config import Settings
from app.domain.broker_account import (
    BrokerAccountNotBoundError,
    BrokerBindInProgressError,
    BrokerLoginFailedError,
    BrokerLoginFailureCode,
    BrokerSessionLimitReachedError,
    FubonCredentials,
)
from app.services.quote.base import QuoteProvider, QuoteProviderError, QuoteSnapshot
from app.services.quote.factory import build_quote_provider

logger = logging.getLogger(__name__)

_LOGIN_CODES: frozenset[str] = frozenset({"login_rejected", "session_limit", "provider_unavailable"})


class OwnerScopedQuoteListener(Protocol):
    """What PR3's dispatcher looks like: `dispatch(snapshot, *, owner_user_id=...)`."""

    def __call__(self, snapshot: QuoteSnapshot, *, owner_user_id: UUID | None) -> None: ...


@dataclass(frozen=True, slots=True)
class PreparedBrokerSession:
    """A logged-in candidate that is not yet serving quotes for its user."""

    user_id: UUID
    provider: QuoteProvider
    broker_account_no: str


def _failure_code(exc: Exception) -> BrokerLoginFailureCode:
    # FubonLoginError carries `failure_code`; duck-typed so this module never
    # imports the Fubon package (the in_memory runtime must not load it).
    code = getattr(exc, "failure_code", None)
    if code in _LOGIN_CODES:
        return cast(BrokerLoginFailureCode, code)
    if isinstance(exc, QuoteProviderError):
        return "provider_unavailable"
    return "unknown"


class BrokerSessionPool:
    def __init__(
        self,
        settings: Settings,
        *,
        shared: QuoteProvider | None = None,
        provider_factory: Callable[[FubonCredentials], QuoteProvider] | None = None,
    ) -> None:
        self._max = settings.broker_max_sessions
        self._shared = shared
        self._build = provider_factory or (lambda creds: build_quote_provider(settings, credentials=creds))
        self._lock = threading.Lock()
        self._sessions: dict[UUID, QuoteProvider] = {}
        # user_id -> the candidate that holds this user's single in-flight operation token.
        self._pending: dict[UUID, PreparedBrokerSession | None] = {}
        self._listener: OwnerScopedQuoteListener | None = None

    @property
    def per_user(self) -> bool:
        return self._shared is None

    def set_quote_listener(self, listener: OwnerScopedQuoteListener) -> None:
        """Every session activated from now on forwards its frames as
        `listener(snapshot, owner_user_id=<that user>)`. Shared mode: the caller
        wires the shared provider itself (owner `None`), as today."""
        with self._lock:
            self._listener = listener

    # --- reads ------------------------------------------------------------------

    def get(self, user_id: UUID) -> QuoteProvider | None:
        if self._shared is not None:
            return self._shared
        with self._lock:
            return self._sessions.get(user_id)

    def require(self, user_id: UUID) -> QuoteProvider:
        provider = self.get(user_id)
        if provider is None:
            raise BrokerAccountNotBoundError()
        return provider

    def bound_user_ids(self) -> set[UUID]:
        with self._lock:
            return set(self._sessions)

    def is_binding(self, user_id: UUID) -> bool:
        with self._lock:
            return user_id in self._pending

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
            raise BrokerLoginFailedError("provider_unavailable")
        with self._lock:
            if user_id in self._pending:
                raise BrokerBindInProgressError()
            reserved = len(self._sessions) + sum(1 for uid in self._pending if uid not in self._sessions)
            if user_id not in self._sessions and reserved >= self._max:
                raise BrokerSessionLimitReachedError(self._max)
            self._pending[user_id] = None
        try:
            provider = self._build(credentials)
            provider.startup()
        except Exception as exc:
            code = _failure_code(exc)
            logger.warning("broker session prepare failed user_id=%s code=%s", user_id, code)
            with self._lock:
                self._pending.pop(user_id, None)
            raise BrokerLoginFailedError(code) from exc
        candidate = PreparedBrokerSession(
            user_id=user_id,
            provider=provider,
            broker_account_no=str(getattr(provider, "broker_account_no", "") or ""),
        )
        with self._lock:
            self._pending[user_id] = candidate
        return candidate

    def activate(self, candidate: PreparedBrokerSession) -> QuoteProvider | None:
        """Atomically make `candidate` the user's live session. No network, no DB.
        Returns the replaced provider (shut it down outside the lock) or None."""
        with self._lock:
            if self._pending.get(candidate.user_id) is not candidate:
                raise BrokerBindInProgressError()
            if self._listener is not None:
                candidate.provider.add_quote_listener(self._owner_listener(candidate.user_id))
            old = self._sessions.get(candidate.user_id)
            self._sessions[candidate.user_id] = candidate.provider
            del self._pending[candidate.user_id]
        return old

    def discard(self, candidate: PreparedBrokerSession) -> None:
        """Drop a candidate that will not be activated; releases token + slot first."""
        with self._lock:
            if self._pending.get(candidate.user_id) is candidate:
                del self._pending[candidate.user_id]
        _shutdown_quietly(candidate.provider, candidate.user_id)

    # --- teardown ---------------------------------------------------------------

    def stop(self, user_id: UUID) -> None:
        """Log the user's live session out (no-op when absent). Refused while a bind
        for the same user is mid-flight, so it cannot race the candidate swap."""
        with self._lock:
            if user_id in self._pending:
                raise BrokerBindInProgressError()
            provider = self._sessions.pop(user_id, None)
        if provider is not None:
            _shutdown_quietly(provider, user_id)

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()
        for user_id, provider in sessions:
            _shutdown_quietly(provider, user_id)

    # --- internals ----------------------------------------------------------------

    def _owner_listener(self, user_id: UUID) -> Callable[[QuoteSnapshot], None]:
        listener = self._listener
        assert listener is not None

        def forward(snapshot: QuoteSnapshot) -> None:
            listener(snapshot, owner_user_id=user_id)

        return forward


def _shutdown_quietly(provider: QuoteProvider, user_id: UUID) -> None:
    try:
        provider.shutdown()
    except Exception as exc:
        # Already logged-out sessions or a broker-side drop; nothing to retry.
        logger.warning("broker session shutdown raised %s user_id=%s", type(exc).__name__, user_id)
