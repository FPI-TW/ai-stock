"""Test-only in-memory `QuoteProvider`.

This provider keeps quote state purely in process memory. It is the runtime quote
source whenever `QUOTE_PROVIDER=in_memory` (CI, unit tests, integration tests). It
is intentionally never exposed via an HTTP endpoint — tests push snapshots directly
through `push_quote()`. The deprecated `POST /dev/quotes` from BE-V0.5-08 is not
reintroduced here.
"""

import threading

from app.services.quote.base import QuoteProvider, QuoteSnapshot, QuoteUnavailableError


class InMemoryQuoteProvider(QuoteProvider):
    """Thread-safe in-memory snapshot store.

    `subscribe` / `unsubscribe` track a set so the reconciler can observe the
    active subscription state, matching the real provider's lifecycle without
    any network IO. There is no quota — tests aren't constrained by the 5-symbol
    demo limit since that's specific to Shioaji free tier.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, QuoteSnapshot] = {}
        self._subscribed: set[str] = set()
        self._lock = threading.RLock()

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        with self._lock:
            missing = [s for s in symbols if s not in self._snapshots]
            if missing:
                raise QuoteUnavailableError(missing[0])
            return [self._snapshots[s] for s in symbols]

    def subscribe(self, symbol: str) -> None:
        with self._lock:
            self._subscribed.add(symbol)

    def unsubscribe(self, symbol: str) -> None:
        with self._lock:
            self._subscribed.discard(symbol)
            self._snapshots.pop(symbol, None)

    def active_subscriptions(self) -> set[str]:
        with self._lock:
            return set(self._subscribed)

    def startup(self) -> None:
        return None

    def shutdown(self) -> None:
        with self._lock:
            self._snapshots.clear()
            self._subscribed.clear()

    # ------------------------------------------------------------------
    # test affordances — not part of the QuoteProvider protocol
    # ------------------------------------------------------------------

    def push_quote(self, snapshot: QuoteSnapshot) -> None:
        with self._lock:
            self._subscribed.add(snapshot.symbol)
            self._snapshots[snapshot.symbol] = snapshot
