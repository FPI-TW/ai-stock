"""Development quote provider for V0.5.

Storage is process-local (in-memory dict) and resets on app reload — the work
order explicitly endorses this trade-off. The QuoteProvider Protocol is the
read-side contract that the BE-V0.5-09 evaluator and a future licensed quote
provider will both implement.

Ingest order is symbol-check → build snapshot → validate → store, so a
rejected quote never silently overwrites the previous valid snapshot.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

from app.domain.quote import QuoteSnapshot, QuoteValidator, ValidatedQuote
from app.services.symbol import SymbolService


class QuoteProvider(Protocol):
    """Read-side contract for the quote layer.

    Implemented by DevelopmentQuoteProvider for V0.5 and by a future licensed
    provider. The BE-V0.5-09 evaluator depends on this Protocol, not on any
    concrete adapter, so swapping the dev provider for a real one stays a
    constructor change.

    Contract:
      - Returns one QuoteSnapshot per symbol that has a stored snapshot.
      - Missing symbols are silently skipped (not raised); callers match
        results back by `QuoteSnapshot.symbol`.
      - Result ordering is not guaranteed.
      - Returns an empty list when `symbols` is empty.
    """

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]: ...


class InMemoryQuoteStore:
    """Thread-safe latest-snapshot-per-symbol store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, QuoteSnapshot] = {}

    def upsert(self, snap: QuoteSnapshot) -> None:
        with self._lock:
            self._data[snap.symbol] = snap

    def get(self, symbol: str) -> QuoteSnapshot | None:
        with self._lock:
            return self._data.get(symbol)

    def get_many(self, symbols: list[str]) -> list[QuoteSnapshot]:
        with self._lock:
            return [self._data[s] for s in symbols if s in self._data]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class DevelopmentQuoteProvider:
    """QuoteProvider implementation backed by an in-memory store.

    Server-set `received_at` is generated through an injectable clock so tests
    can pin it to a fixed instant. `build_snapshot` and `store` are split so
    the ingest orchestrator can validate before persisting.
    """

    def __init__(
        self,
        store: InMemoryQuoteStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(tz=UTC))

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        return self._store.get_many(symbols)

    def build_snapshot(
        self,
        *,
        symbol: str,
        bid_price: Decimal | None,
        ask_price: Decimal | None,
        last_price: Decimal | None,
        quote_time: datetime,
    ) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol,
            bid_price=bid_price,
            ask_price=ask_price,
            last_price=last_price,
            quote_time=quote_time,
            received_at=self._clock(),
        )

    def store(self, snapshot: QuoteSnapshot) -> None:
        self._store.upsert(snapshot)

    def get_snapshot(self, symbol: str) -> QuoteSnapshot | None:
        """回傳指定 symbol 的最新 snapshot；無資料回 None。"""
        return self._store.get(symbol)


class DevQuoteIngestService:
    """Orchestrate the dev quote ingest path.

    Symbol existence / tradable check is performed first so the validator can
    stay free of DB dependencies. Storage happens only after validation
    succeeds.
    """

    def __init__(
        self,
        symbol_service: SymbolService,
        validator: QuoteValidator,
        provider: DevelopmentQuoteProvider,
    ) -> None:
        self._symbol_service = symbol_service
        self._validator = validator
        self._provider = provider

    def ingest(
        self,
        *,
        symbol: str,
        bid_price: Decimal | None,
        ask_price: Decimal | None,
        last_price: Decimal | None,
        quote_time: datetime,
    ) -> ValidatedQuote:
        self._symbol_service.get_tradable_symbol(symbol)
        snapshot = self._provider.build_snapshot(
            symbol=symbol,
            bid_price=bid_price,
            ask_price=ask_price,
            last_price=last_price,
            quote_time=quote_time,
        )
        validated = self._validator.validate(snapshot)
        self._provider.store(validated.snapshot)
        return validated

    def get_snapshot(self, symbol: str) -> QuoteSnapshot | None:
        """讀取目前 in-memory store 的 snapshot，無資料回 None。"""
        return self._provider.get_snapshot(symbol)


@lru_cache
def get_dev_quote_store() -> InMemoryQuoteStore:
    return InMemoryQuoteStore()
