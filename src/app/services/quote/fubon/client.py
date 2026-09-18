"""Thin wrapper around the Fubon Neo SDK for one user's session.

This is the **only** module in the codebase that imports `fubon_neo`, and it does
so lazily inside `_default_sdk_factory` / `_default_realtime_mode`: the wheel is
Linux-only, so macOS dev boxes and unit tests inject a duck-typed fake instead.

One `FubonClient` == one broker login == one SDK instance. Market data, accounting
and (later) order placement all hang off the same `sdk` object.

Threading: the SDK delivers websocket frames and trade-side events on its own
threads. Callbacks here only mutate this object's own state and forward
normalised snapshots to `quote_handler`; the provider layer owns the lock.

Verified SDK behaviour (docs/vendor/fubon/fubon-neo-verified-behavior.md):
- `login().data` is multi-account and unordered → filter `account_type == "stock"`.
- The market-data websocket never reconnects by itself; `on_disconnect` only
  flags, `reconnect_realtime()` rebuilds everything (new token, listeners,
  connect, resubscribe) and the channel-id map is refilled from `subscribed`
  events.
- Trade-side `set_on_event` codes are *strings*: 300/301/304 mean the login is
  gone; 302 is the echo of our own logout.
"""

import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from app.services.quote.base import QuoteProviderUnavailableError, QuoteSnapshot
from app.services.quote.fubon.normalize import aggregates_to_snapshot

logger = logging.getLogger(__name__)

QuoteHandler = Callable[[QuoteSnapshot], None]
FubonLoginFailureCode = Literal["login_rejected", "session_limit", "provider_unavailable", "unknown"]

_LOGIN_LOST_EVENT_CODES = frozenset({"300", "301", "304"})
_SESSION_LIMIT_MARKER = "連線限制"
_CHANNEL = "aggregates"


@dataclass(frozen=True, repr=False)
class FubonCredentials:
    """One user's Fubon login material, in memory only. Never logged, never persisted here."""

    personal_id: str
    password: str
    cert_pfx: bytes
    cert_password: str

    def __repr__(self) -> str:
        return "FubonCredentials(personal_id='***', password='***', cert_pfx=<bytes>, cert_password='***')"


class FubonLoginError(QuoteProviderUnavailableError):
    """Login / session failure with a safe, enumerable code and no SDK text."""

    error_code: ClassVar[str] = "QUOTE_PROVIDER_UNAVAILABLE"

    def __init__(self, failure_code: FubonLoginFailureCode) -> None:
        super().__init__("fubon", failure_code)
        self.failure_code: FubonLoginFailureCode = failure_code


def _default_sdk_factory(ws_url: str | None) -> Callable[[], Any]:
    def build() -> Any:
        from fubon_neo.sdk import FubonSDK

        # 30s pong interval, disconnect after 2 misses — same as the manual probes.
        return FubonSDK(30, 2, url=ws_url) if ws_url else FubonSDK(30, 2)

    return build


def _default_realtime_mode() -> Any:
    from fubon_neo.sdk import Mode

    return Mode.Normal  # Speed mode has no `aggregates` channel.


class FubonClient:
    def __init__(
        self,
        *,
        personal_id: str,
        password: str,
        cert_pfx: bytes,
        cert_password: str,
        ws_url: str | None = None,
        sdk_factory: Callable[[], Any] | None = None,
        realtime_mode: Any | None = None,
    ) -> None:
        self._personal_id = personal_id
        self._password = password
        self._cert_pfx = cert_pfx
        self._cert_password = cert_password
        self._sdk_factory = sdk_factory or _default_sdk_factory(ws_url)
        self._realtime_mode = realtime_mode
        self._sdk: Any | None = None
        self._quote_handler: QuoteHandler | None = None
        self._subscribed: set[str] = set()
        self._channels: dict[str, str] = {}
        self.account: Any | None = None
        self.login_alive = False
        self.realtime_connected = False

    def __repr__(self) -> str:  # never leak credentials via logging / tracebacks
        return f"FubonClient(account={getattr(self.account, 'account', None)!r}, login_alive={self.login_alive})"

    # --- session -----------------------------------------------------------

    def login(self) -> None:
        if self._sdk is not None:
            return
        sdk = self._sdk_factory()
        try:
            result = self._login_with_temp_cert(sdk)
        except Exception as exc:
            logger.warning("fubon login raised %s", type(exc).__name__)
            raise FubonLoginError("provider_unavailable") from exc
        if not getattr(result, "is_success", False):
            message = str(getattr(result, "message", "") or "")
            code: FubonLoginFailureCode = "session_limit" if _SESSION_LIMIT_MARKER in message else "login_rejected"
            logger.warning("fubon login rejected code=%s", code)
            raise FubonLoginError(code)
        stock_accounts = [a for a in (result.data or []) if getattr(a, "account_type", None) == "stock"]
        if not stock_accounts:
            logger.warning("fubon login returned no stock account")
            raise FubonLoginError("login_rejected")
        self.account = stock_accounts[0]
        sdk.set_on_event(self._on_trade_event)
        self._sdk = sdk
        self.login_alive = True

    def _login_with_temp_cert(self, sdk: Any) -> Any:
        # The SDK only accepts a file path; NamedTemporaryFile is created 0600.
        with tempfile.NamedTemporaryFile(suffix=".pfx", delete=False) as cert_file:
            cert_file.write(self._cert_pfx)
            cert_path = cert_file.name
        try:
            return sdk.login(self._personal_id, self._password, cert_path, self._cert_password)
        finally:
            try:
                os.unlink(cert_path)
            except OSError:  # pragma: no cover - already gone
                pass

    def logout(self) -> None:
        sdk = self._sdk
        if sdk is None:
            return
        try:
            sdk.logout()
        except Exception as exc:  # pragma: no cover - best-effort teardown
            logger.warning("fubon logout raised %s", type(exc).__name__)
        self._sdk = None
        self.login_alive = False
        self.realtime_connected = False

    def _on_trade_event(self, code: Any, _content: Any) -> None:
        code = str(code)
        if code in _LOGIN_LOST_EVENT_CODES:
            self.login_alive = False
            logger.warning("fubon trade session lost event_code=%s", code)
        elif code == "201":
            logger.warning("fubon login warning event_code=201")

    # --- realtime ------------------------------------------------------------

    def set_quote_handler(self, handler: QuoteHandler) -> None:
        self._quote_handler = handler

    def connect_realtime(self) -> None:
        sdk = self._require_sdk()
        mode = self._realtime_mode if self._realtime_mode is not None else _default_realtime_mode()
        self._channels = {}
        self.realtime_connected = False
        try:
            sdk.init_realtime(mode)  # exchanges the trade login for a market-data token
        except Exception as exc:
            # No token means the login behind it is gone → full re-login needed.
            self.login_alive = False
            logger.warning("fubon realtime token exchange raised %s", type(exc).__name__)
            raise FubonLoginError("provider_unavailable") from exc
        try:
            ws = sdk.marketdata.websocket_client.stock
            ws.on("message", self._on_message)
            ws.on("disconnect", self._on_disconnect)
            ws.on("error", self._on_error)
            ws.connect()
        except Exception as exc:
            # Websocket-level refusal (e.g. connection cap, auth timeout): the trade
            # login is still valid, only the realtime leg failed. Seen live 2026-09-18.
            logger.warning("fubon realtime websocket connect raised %s", type(exc).__name__)
            raise QuoteProviderUnavailableError("fubon", "realtime_connect_failed") from exc
        self.realtime_connected = True

    def reconnect_realtime(self) -> None:
        self.connect_realtime()
        for symbol in sorted(self._subscribed):
            self._ws().subscribe({"channel": _CHANNEL, "symbol": symbol})

    def subscribe(self, symbol: str) -> None:
        self._subscribed.add(symbol)
        self._ws().subscribe({"channel": _CHANNEL, "symbol": symbol})

    def unsubscribe(self, symbol: str) -> None:
        self._subscribed.discard(symbol)
        channel_id = self._channels.pop(symbol, None)
        if channel_id is not None:
            self._ws().unsubscribe({"id": channel_id})

    def subscribed_symbols(self) -> set[str]:
        return set(self._subscribed)

    def _on_message(self, raw: Any) -> None:
        try:
            message = json.loads(raw) if isinstance(raw, str | bytes) else raw
        except ValueError:
            return
        event = message.get("event")
        data = message.get("data") or {}
        if event == "subscribed":
            self._channels[str(data.get("symbol"))] = str(data.get("id"))
        elif event in ("data", "snapshot") and self._quote_handler is not None:
            self._quote_handler(aggregates_to_snapshot(data))

    def _on_disconnect(self, *_args: Any) -> None:
        self.realtime_connected = False
        logger.warning("fubon realtime websocket disconnected")

    def _on_error(self, *_args: Any) -> None:
        logger.warning("fubon realtime websocket error")

    # --- REST --------------------------------------------------------------

    def get_stock_quote(self, symbol: str) -> QuoteSnapshot:
        sdk = self._require_sdk()
        try:
            payload = sdk.marketdata.rest_client.stock.intraday.quote(symbol=symbol)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            reason = "rate_limited" if status == 429 else "quote_failed"
            logger.warning("fubon rest quote failed symbol=%s status=%s", symbol, status)
            raise QuoteProviderUnavailableError("fubon", reason) from exc
        return aggregates_to_snapshot(payload)

    # --- internals -----------------------------------------------------------

    def _require_sdk(self) -> Any:
        if self._sdk is None:
            raise FubonLoginError("provider_unavailable")
        return self._sdk

    def _ws(self) -> Any:
        return self._require_sdk().marketdata.websocket_client.stock
