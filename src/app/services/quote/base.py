"""Provider-agnostic types shared by every quote provider.

Concrete providers live in sibling modules.
Upper layers (evaluator, intent service, API) should depend on this module
and never reach into a specific provider's package.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Protocol, runtime_checkable

from fastapi import status


@dataclass(frozen=True)
class QuoteSnapshot:
    """The latest known quote for a single symbol.

    Two broker-supplied timestamps, one per price kind (Asia/Taipei, tz-aware):

    - `quote_time`: when this frame (the book: bid/ask) was last updated. Session
      and freshness checks use it, because triggers read bid/ask first.
    - `last_trade_time`: when `last_price` actually traded, or None before the
      first match of the day. A thin stock can have a fresh book and a trade from
      an hour ago; the evaluator only accepts `last_price` as a fallback when this
      is fresh too.

    `received_at` is when the provider observed the frame locally (UTC, tz-aware).
    Any of these being naive is a bug — providers attach timezone info first.
    """

    symbol: str
    bid_price: Decimal | None
    ask_price: Decimal | None
    last_price: Decimal | None
    quote_time: datetime
    last_trade_time: datetime | None
    received_at: datetime


QuoteListener = Callable[[QuoteSnapshot], None]
"""Listener fired by a provider whenever a fresh snapshot is stored.

Listeners run inline on whatever thread delivered the snapshot — for licensed
broker providers that is a broker SDK worker thread, so a listener must not
touch a request-scoped SQLAlchemy session and must swallow its own exceptions.
"""


@runtime_checkable
class QuoteProvider(Protocol):
    """Interface every concrete provider implements.

    The split: `subscribe` / `unsubscribe` manage the broker-side subscription set;
    `get_quotes` reads the in-memory snapshot cache that broker callbacks update.
    `startup` / `shutdown` bracket session lifecycle and are called by FastAPI's
    lifespan — pure providers (`InMemoryQuoteProvider`) treat them as no-ops.

    `get_quotes` returns the LAST KNOWN snapshot per symbol, never a
    guaranteed-fresh one. A dropped market-data socket leaves the cache frozen
    until the next update; no provider clears it on disconnect. Callers that act
    on a price must gate on `QuoteSnapshot.quote_time` themselves — that is what
    `QuoteEvaluator`'s freshness check exists for, and every new-track consumer
    goes through it.

    `add_quote_listener` / `remove_quote_listener` let upper layers (the evaluation
    dispatcher) subscribe to / unsubscribe from snapshot updates so the broker
    callback can drive evaluation without the provider knowing what evaluator
    looks like. `remove_quote_listener` is idempotent — unregistering a listener
    that was never added is a no-op.
    """

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]: ...
    def subscribe(self, symbol: str) -> None: ...
    def unsubscribe(self, symbol: str) -> None: ...
    def active_subscriptions(self) -> set[str]: ...
    def startup(self) -> None: ...
    def shutdown(self) -> None: ...
    def add_quote_listener(self, listener: QuoteListener) -> None: ...
    def remove_quote_listener(self, listener: QuoteListener) -> None: ...


class QuoteProviderError(Exception):
    """Base for every provider-layer error.

    Subclasses declare class-level `error_code` and `http_status` so the FastAPI
    error handler can map them to envelopes without ever importing the concrete
    subclass — that keeps provider-specific error classes out of `api/errors.py`.

    The base defaults fall back to a generic 500 envelope when a subclass forgets
    to override; concrete subclasses must always override `error_code` /
    `http_status` / `default_message`.
    """

    error_code: ClassVar[str] = "INTERNAL_ERROR"
    http_status: ClassVar[int] = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message: ClassVar[str] = "行情服務發生錯誤"

    def details(self) -> dict[str, object]:
        return {}


class QuoteUnavailableError(QuoteProviderError):
    """Symbol is subscribed but no snapshot has arrived yet (cold start window)."""

    error_code = "QUOTE_UNAVAILABLE"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    default_message = "尚未收到該標的的行情報價"

    def __init__(self, symbol: str) -> None:
        super().__init__(symbol)
        self.symbol = symbol

    def details(self) -> dict[str, object]:
        return {"symbol": self.symbol}


class QuoteProviderUnavailableError(QuoteProviderError):
    """Provider session is down (login failed, websocket dropped, etc.)."""

    error_code = "QUOTE_PROVIDER_UNAVAILABLE"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    default_message = "行情服務暫時無法使用"

    def __init__(self, provider: str, reason: str | None = None) -> None:
        super().__init__(f"{provider}: {reason}" if reason else provider)
        self.provider = provider
        self.reason = reason

    def details(self) -> dict[str, object]:
        d: dict[str, object] = {"provider": self.provider}
        if self.reason:
            d["reason"] = self.reason
        return d
