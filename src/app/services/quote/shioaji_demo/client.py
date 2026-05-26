"""Thin wrapper around the `shioaji` SDK.

This is the **only** module in the codebase that imports `shioaji`. The rest of
`shioaji_demo/` and everything above interacts with the SDK exclusively through
this class. Goals:

- Centralise SDK quirks (callback registration, contract lookup).
- Make the SDK swappable for a fake in unit tests via duck-typed `client` injection.
- Keep import side-effects contained — if `shioaji` is missing, only this module
  blows up with ImportError, which `factory.py` catches and re-raises with an
  actionable message.

Callback runs on Shioaji's worker thread. Callers must take the provider lock when
mutating shared state. SQLAlchemy sessions must never be touched from these threads.
"""

from collections.abc import Callable
from typing import Any

import shioaji as sj

from app.services.quote.base import QuoteProviderUnavailableError, QuoteSnapshot, QuoteUnavailableError
from app.services.quote.shioaji_demo.normalize import (
    BidAskPayload,
    TickPayload,
    first,
    now_utc,
    timestamp_to_taipei,
    to_decimal,
    to_taipei,
)

TickHandler = Callable[[TickPayload], None]
BidAskHandler = Callable[[BidAskPayload], None]


class ShioajiClient:
    """Manages login / subscribe / callback wiring against the live Shioaji SDK."""

    def __init__(self, *, api_key: str, secret_key: str, simulation: bool) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._simulation = simulation
        self._api: Any | None = None

    def login(self) -> None:
        if self._api is not None:
            return
        try:
            api = sj.Shioaji(simulation=self._simulation)
            api.login(api_key=self._api_key, secret_key=self._secret_key)
        except Exception as exc:  # pragma: no cover - exercised in manual verification
            raise QuoteProviderUnavailableError("shioaji", f"login failed: {exc!s}") from exc
        self._api = api

    def logout(self) -> None:
        if self._api is None:
            return
        try:
            self._api.logout()
        except Exception:  # pragma: no cover
            # Best-effort shutdown — swallow so the lifespan teardown can finish.
            pass
        self._api = None

    def set_tick_handler(self, handler: TickHandler) -> None:
        """Register a normalised tick callback. Multiple calls replace the previous handler."""

        api = self._require_api()

        @api.on_tick_stk_v1()  # type: ignore[misc]
        def _on_tick(_exchange: Any, tick: Any) -> None:
            last = to_decimal(getattr(tick, "close", None))
            if last is None:
                return
            quote_time = to_taipei(getattr(tick, "datetime", None))
            handler(
                TickPayload(
                    symbol=str(getattr(tick, "code", "")),
                    last_price=last,
                    quote_time=quote_time,
                )
            )

    def set_bidask_handler(self, handler: BidAskHandler) -> None:
        api = self._require_api()

        @api.on_bidask_stk_v1()  # type: ignore[misc]
        def _on_bidask(_exchange: Any, bidask: Any) -> None:
            bid = to_decimal(first(getattr(bidask, "bid_price", None)))
            ask = to_decimal(first(getattr(bidask, "ask_price", None)))
            if bid is None and ask is None:
                return
            quote_time = to_taipei(getattr(bidask, "datetime", None))
            handler(
                BidAskPayload(
                    symbol=str(getattr(bidask, "code", "")),
                    bid_price=bid,
                    ask_price=ask,
                    quote_time=quote_time,
                )
            )

    def subscribe(self, symbol: str) -> None:
        api = self._require_api()
        contract = self._resolve_contract(symbol)
        try:
            api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.Tick, version=sj.constant.QuoteVersion.v1)
            api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.BidAsk, version=sj.constant.QuoteVersion.v1)
        except Exception as exc:  # pragma: no cover
            raise QuoteProviderUnavailableError("shioaji", f"subscribe failed for {symbol}: {exc!s}") from exc

    def unsubscribe(self, symbol: str) -> None:
        api = self._require_api()
        contract = self._resolve_contract(symbol)
        tick = sj.constant.QuoteType.Tick
        bidask = sj.constant.QuoteType.BidAsk
        version = sj.constant.QuoteVersion.v1
        try:
            api.quote.unsubscribe(contract, quote_type=tick, version=version)
            api.quote.unsubscribe(contract, quote_type=bidask, version=version)
        except Exception:  # pragma: no cover
            # Unsubscribe on shutdown is best-effort; logging-only would be too noisy.
            pass

    def get_stock_snapshot(self, symbol: str) -> QuoteSnapshot:
        api = self._require_api()
        contract = self._resolve_contract(symbol)
        try:
            snapshots = api.snapshots([contract])
        except Exception as exc:  # pragma: no cover
            raise QuoteProviderUnavailableError("shioaji", f"snapshot failed for {symbol}: {exc!s}") from exc
        if not snapshots:
            raise QuoteUnavailableError(symbol)

        snapshot = snapshots[0]
        code = str(getattr(snapshot, "code", symbol) or symbol)
        return QuoteSnapshot(
            symbol=code,
            bid_price=to_decimal(getattr(snapshot, "buy_price", None)),
            ask_price=to_decimal(getattr(snapshot, "sell_price", None)),
            last_price=to_decimal(getattr(snapshot, "close", None)),
            quote_time=timestamp_to_taipei(getattr(snapshot, "ts", None)),
            received_at=now_utc(),
        )

    def _resolve_contract(self, symbol: str) -> Any:
        api = self._require_api()
        contract = api.Contracts.Stocks[symbol]
        if contract is None:
            raise QuoteProviderUnavailableError("shioaji", f"contract not found for {symbol}")
        return contract

    def _require_api(self) -> Any:
        if self._api is None:
            raise QuoteProviderUnavailableError("shioaji", "client not logged in")
        return self._api
