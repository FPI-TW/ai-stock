"""`QuoteProvider` implementation backed by the Shioaji demo websocket.

V0.5 hardcode lives entirely in this package — the rest of the codebase only sees
the `QuoteProvider` protocol from `app.services.quote.base`.

Threading model: the Shioaji SDK invokes callbacks on its own worker threads. We
serialise snapshot mutations with `threading.RLock` and do **not** touch the
SQLAlchemy session from inside a callback (that's the FastAPI request thread's
job, not the broker thread's).
"""

import logging
import threading
from typing import TYPE_CHECKING

from app.core.config import Settings
from app.services.quote.base import (
    QuoteListener,
    QuoteProvider,
    QuoteProviderError,
    QuoteProviderUnavailableError,
    QuoteSnapshot,
    QuoteUnavailableError,
)
from app.services.quote.shioaji_demo.allowlist import (
    DEFAULT_DEMO_ALLOWED_SYMBOLS,
    SymbolNotAvailableInDemo,
    parse_allowlist_override,
)
from app.services.quote.shioaji_demo.normalize import (
    BidAskPayload,
    TickPayload,
    build_snapshot,
)
from app.services.quote.shioaji_demo.quota import (
    DEFAULT_MAX_SUBSCRIPTIONS,
    QuoteSubscriptionLimitExceeded,
)

if TYPE_CHECKING:
    from app.services.quote.shioaji_demo.client import ShioajiClient

logger = logging.getLogger(__name__)


class ShioajiQuoteProvider(QuoteProvider):
    def __init__(
        self,
        *,
        client: "ShioajiClient",
        allowed_symbols: frozenset[str] = DEFAULT_DEMO_ALLOWED_SYMBOLS,
        max_subscriptions: int = DEFAULT_MAX_SUBSCRIPTIONS,
    ) -> None:
        self._client = client
        self._allowed = allowed_symbols
        self._max = max_subscriptions
        self._snapshots: dict[str, QuoteSnapshot] = {}
        self._subscribed: set[str] = set()
        self._listeners: list[QuoteListener] = []
        self._lock = threading.RLock()
        self._started = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "ShioajiQuoteProvider":
        # `Settings._enforce_shioaji_credentials` guarantees these are non-None when
        # quote_provider == "shioaji_demo"; the raise below is defence-in-depth that
        # survives `python -O` (which strips `assert`).
        if not settings.shioaji_api_key or not settings.shioaji_secret_key:
            raise RuntimeError(
                "SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY missing for quote_provider=shioaji_demo; "
                "Settings validation should have caught this — please check config wiring."
            )

        from app.services.quote.shioaji_demo.client import ShioajiClient

        client = ShioajiClient(
            api_key=settings.shioaji_api_key,
            secret_key=settings.shioaji_secret_key,
            simulation=settings.shioaji_simulation,
        )
        return cls(
            client=client,
            allowed_symbols=parse_allowlist_override(settings.shioaji_demo_allowed_symbols),
            max_subscriptions=settings.shioaji_max_subscriptions,
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def startup(self) -> None:
        with self._lock:
            if self._started:
                return
            self._client.login()
            self._client.set_tick_handler(self._on_tick)
            self._client.set_bidask_handler(self._on_bidask)
            self._started = True
            logger.info("shioaji login ok (simulation=%s)", getattr(self._client, "_simulation", "?"))

    def shutdown(self) -> None:
        with self._lock:
            if not self._started:
                return
            for symbol in list(self._subscribed):
                self._client.unsubscribe(symbol)
            self._client.logout()
            self._subscribed.clear()
            self._snapshots.clear()
            self._started = False
            logger.info("shioaji logout ok")

    def mark_started_for_tests(self) -> None:
        """Test-only escape hatch to skip the real `startup()` (which would call
        shioaji login). Tests construct the provider with a duck-typed mock client
        and call this to flip into the "started" state without going through the
        SDK login path. Do NOT use in production code.
        """
        with self._lock:
            self._started = True

    # ------------------------------------------------------------------
    # QuoteProvider protocol
    # ------------------------------------------------------------------

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        with self._lock:
            missing = [s for s in symbols if s not in self._snapshots]
            if missing:
                raise QuoteUnavailableError(missing[0])
            return [self._snapshots[s] for s in symbols]

    def get_current_price(self, symbol: str) -> QuoteSnapshot:
        if symbol not in self._allowed:
            raise SymbolNotAvailableInDemo(symbol, self._allowed)

        with self._lock:
            if not self._started:
                raise QuoteProviderUnavailableError("shioaji", "provider not started; call startup() first")
        return self._client.get_stock_snapshot(symbol)

    def subscribe(self, symbol: str) -> None:
        # Allowlist check first — the SDK never sees a rejected symbol.
        if symbol not in self._allowed:
            raise SymbolNotAvailableInDemo(symbol, self._allowed)

        with self._lock:
            if symbol in self._subscribed:
                return
            # "provider not started" is a more fundamental failure than "quota
            # full"; surface it first so callers don't get a misleading 409
            # when the real cause is an unstarted provider.
            if not self._started:
                raise QuoteProviderUnavailableError("shioaji", "provider not started; call startup() first")
            if len(self._subscribed) >= self._max:
                raise QuoteSubscriptionLimitExceeded(
                    symbol=symbol,
                    current=len(self._subscribed),
                    limit=self._max,
                )
            try:
                self._client.subscribe(symbol)
            except QuoteProviderError:
                self._client.unsubscribe(symbol)
                raise
            self._subscribed.add(symbol)

    def unsubscribe(self, symbol: str) -> None:
        with self._lock:
            if symbol not in self._subscribed:
                return
            try:
                self._client.unsubscribe(symbol)
            except QuoteProviderError:
                # Already-shutdown unsubscribe shouldn't propagate; subscription set
                # is the source of truth for the reconciler.
                logger.warning("shioaji unsubscribe failed for %s; clearing local state anyway", symbol)
            self._subscribed.discard(symbol)
            self._snapshots.pop(symbol, None)

    def active_subscriptions(self) -> set[str]:
        with self._lock:
            return set(self._subscribed)

    # ------------------------------------------------------------------
    # QuoteProvider protocol — listener support
    # ------------------------------------------------------------------

    def add_quote_listener(self, listener: QuoteListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_quote_listener(self, listener: QuoteListener) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # callbacks (worker threads — do NOT touch SQLAlchemy session here)
    # ------------------------------------------------------------------

    def _on_tick(self, payload: TickPayload) -> None:
        with self._lock:
            previous = self._snapshots.get(payload.symbol)
            snapshot = build_snapshot(
                symbol=payload.symbol,
                previous=previous,
                last_price=payload.last_price,
                quote_time=payload.quote_time,
            )
            self._snapshots[payload.symbol] = snapshot
            listeners = list(self._listeners)
        self._fire_listeners(snapshot, listeners)

    def _on_bidask(self, payload: BidAskPayload) -> None:
        with self._lock:
            previous = self._snapshots.get(payload.symbol)
            snapshot = build_snapshot(
                symbol=payload.symbol,
                previous=previous,
                bid_price=payload.bid_price,
                ask_price=payload.ask_price,
                quote_time=payload.quote_time,
            )
            self._snapshots[payload.symbol] = snapshot
            listeners = list(self._listeners)
        self._fire_listeners(snapshot, listeners)

    def _fire_listeners(self, snapshot: QuoteSnapshot, listeners: list[QuoteListener]) -> None:
        """Run listeners outside the snapshot lock so dispatch latency can't
        block concurrent broker callbacks. A misbehaving listener must never
        kill subsequent dispatches or propagate up to the SDK thread.
        """
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                logger.exception("quote listener raised on %s", snapshot.symbol)
