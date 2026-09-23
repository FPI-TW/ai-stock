"""`QuoteProvider` backed by one user's Fubon Neo session.

One provider == one `FubonClient` == one broker login. The session pool (PR2)
holds one of these per bound user; nothing above this module knows about the SDK.

Threading model is the same as the demo provider: the SDK pushes frames on its
own thread, snapshot mutations are serialised with `threading.RLock`, listeners
run outside the lock and must never raise back into the SDK thread. No
SQLAlchemy session is ever touched here.
"""

import logging
import threading
from typing import TYPE_CHECKING

from fastapi import status

from app.services.quote.base import (
    QuoteListener,
    QuoteProvider,
    QuoteProviderError,
    QuoteProviderUnavailableError,
    QuoteSnapshot,
    QuoteUnavailableError,
)

if TYPE_CHECKING:
    from app.services.quote.fubon.client import FubonClient

logger = logging.getLogger(__name__)

# Fubon: 300 symbols per websocket connection (docs/vendor/fubon/fubon-llms-full.txt).
DEFAULT_MAX_SUBSCRIPTIONS = 300


class FubonSubscriptionLimitExceeded(QuoteProviderError):
    error_code = "QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED"
    http_status = status.HTTP_409_CONFLICT
    default_message = "目前訂閱數已達富邦單一連線上限"

    def __init__(self, *, symbol: str, current: int, limit: int) -> None:
        super().__init__(f"limit={limit} reached when subscribing {symbol}")
        self.symbol = symbol
        self.current = current
        self.limit = limit

    def details(self) -> dict[str, object]:
        return {"symbol": self.symbol, "current": self.current, "limit": self.limit}


class FubonQuoteProvider(QuoteProvider):
    current_price_source = "fubon"

    def __init__(self, *, client: "FubonClient", max_subscriptions: int = DEFAULT_MAX_SUBSCRIPTIONS) -> None:
        self._client = client
        self._max = max_subscriptions
        self._snapshots: dict[str, QuoteSnapshot] = {}
        self._subscribed: set[str] = set()
        self._listeners: list[QuoteListener] = []
        self._lock = threading.RLock()
        self._started = False
        # True while login/connect runs OUTSIDE the lock (the SDK's connect() busy-spins
        # up to ~5s). subscribe() defers the broker call while set; reconnect resends.
        self._connecting = False

    # --- lifecycle -----------------------------------------------------------

    def startup(self) -> None:
        with self._lock:
            if self._started or self._connecting:
                return
            self._connecting = True
        try:
            self._client.login()
            try:
                self._client.set_quote_handler(self._on_snapshot)
                self._client.connect_realtime()
            except Exception:
                # Login already succeeded: release it, or shutdown() (gated on
                # `_started`) never will and the broker keeps the session.
                self._client.logout()
                raise
            with self._lock:
                self._started = True
        finally:
            with self._lock:
                self._connecting = False
        logger.info("fubon session started")

    def shutdown(self) -> None:
        with self._lock:
            if not self._started:
                return
            for symbol in list(self._subscribed):
                try:
                    self._client.unsubscribe(symbol)
                except Exception:
                    # The broker closes the market-data socket itself after
                    # hours (verified ~14:05); unsubscribe on shutdown is
                    # best-effort, logout below is the step that must run.
                    logger.warning("fubon unsubscribe on shutdown failed symbol=%s", symbol)
            self._client.logout()
            self._subscribed.clear()
            self._snapshots.clear()
            self._started = False
            logger.info("fubon session stopped")

    def reconnect_realtime(self) -> None:
        """Rebuild the market-data websocket after a broker-side drop; login stays.

        The provider owns the subscription set, so it is the one that resubscribes.
        """
        with self._lock:
            if self._connecting:
                return  # another thread is already rebuilding the socket
            self._connecting = True
        try:
            self._client.connect_realtime()
        except Exception:
            with self._lock:
                self._connecting = False
            raise
        with self._lock:
            self._connecting = False
            # Symbols subscribed while we were connecting were only recorded;
            # this single pass sends every owned symbol exactly once.
            for symbol in sorted(self._subscribed):
                self._client.subscribe(symbol)

    @property
    def realtime_connected(self) -> bool:
        return self._client.realtime_connected

    @property
    def login_alive(self) -> bool:
        return self._client.login_alive

    # --- QuoteProvider -------------------------------------------------------

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        with self._lock:
            # The book window is 10 minutes wide (BOOK_FRESHNESS_THRESHOLD), so a
            # cached snapshot now outlives a short disconnect. While the socket is
            # down we cannot tell a standing book from one that moved unseen, so
            # refuse to serve the cache instead of letting it pass as current.
            # Callers already treat a missing quote as non-blocking. The listener
            # path is unaffected: a frame arriving *is* proof of a live socket.
            if not self._client.realtime_connected:
                raise QuoteProviderUnavailableError("fubon", "realtime_disconnected")
            missing = [s for s in symbols if s not in self._snapshots]
            if missing:
                raise QuoteUnavailableError(missing[0])
            return [self._snapshots[s] for s in symbols]

    def get_current_price(self, symbol: str) -> QuoteSnapshot:
        with self._lock:
            self._require_started()
        return self._client.get_stock_quote(symbol)

    def subscribe(self, symbol: str) -> None:
        with self._lock:
            if symbol in self._subscribed:
                return
            self._require_started()
            if len(self._subscribed) >= self._max:
                raise FubonSubscriptionLimitExceeded(symbol=symbol, current=len(self._subscribed), limit=self._max)
            if not self._connecting:  # else reconnect_realtime() resends the whole set
                self._client.subscribe(symbol)
            self._subscribed.add(symbol)

    def unsubscribe(self, symbol: str) -> None:
        with self._lock:
            if symbol not in self._subscribed:
                return
            try:
                self._client.unsubscribe(symbol)
            except QuoteProviderError:
                logger.warning("fubon unsubscribe failed for %s; clearing local state anyway", symbol)
            self._subscribed.discard(symbol)
            self._snapshots.pop(symbol, None)

    def active_subscriptions(self) -> set[str]:
        with self._lock:
            return set(self._subscribed)

    def add_quote_listener(self, listener: QuoteListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_quote_listener(self, listener: QuoteListener) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    # --- SDK thread ------------------------------------------------------------

    def _on_snapshot(self, snapshot: QuoteSnapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.symbol] = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                logger.exception("quote listener raised on %s", snapshot.symbol)

    def _require_started(self) -> None:
        if not self._started:
            raise QuoteProviderUnavailableError("fubon", "provider not started; call startup() first")
