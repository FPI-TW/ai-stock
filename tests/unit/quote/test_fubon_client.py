"""FubonClient against a duck-typed fake SDK (no `fubon_neo` on macOS / CI)."""

import json
import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.quote.base import QuoteProviderUnavailableError, QuoteSnapshot
from app.services.quote.fubon.client import FubonClient, FubonLoginError

PFX_BYTES = b"not-really-a-pfx"


class FakeWs:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., None]] = {}
        self.subscribed: list[dict[str, str]] = []
        self.unsubscribed: list[dict[str, str]] = []
        self.connect_calls = 0
        self._next_channel = 0

    def on(self, event: str, handler: Callable[..., None]) -> None:
        self.handlers[event] = handler

    def connect(self) -> None:
        self.connect_calls += 1

    def subscribe(self, params: dict[str, str]) -> None:
        self.subscribed.append(params)
        self._next_channel += 1
        self.emit({"event": "subscribed", "data": {"id": f"ch-{self._next_channel}", **params}})

    def unsubscribe(self, params: dict[str, str]) -> None:
        self.unsubscribed.append(params)

    def emit(self, message: dict[str, Any]) -> None:
        self.handlers["message"](json.dumps(message))

    def drop(self) -> None:
        self.handlers["disconnect"](None, None)


class FakeSdk:
    def __init__(
        self,
        *,
        login_result: SimpleNamespace,
        quote_payload: dict[str, Any] | None = None,
        quote_error: Exception | None = None,
    ) -> None:
        self.login_result = login_result
        self.login_args: tuple[str, str, str, str] | None = None
        self.cert_path_existed_during_login: bool | None = None
        self.cert_bytes_during_login: bytes | None = None
        self.on_event: Callable[[str, str], None] | None = None
        self.init_realtime_calls: list[object] = []
        self.ws_instances: list[FakeWs] = []
        self.logged_out = False
        self._quote_payload = quote_payload
        self._quote_error = quote_error
        self.marketdata: Any = None

    def login(self, personal_id: str, password: str, cert_path: str, cert_password: str) -> SimpleNamespace:
        self.login_args = (personal_id, password, cert_path, cert_password)
        self.cert_path_existed_during_login = os.path.exists(cert_path)
        if self.cert_path_existed_during_login:
            self.cert_bytes_during_login = Path(cert_path).read_bytes()
        return self.login_result

    def set_on_event(self, callback: Callable[[str, str], None]) -> None:
        self.on_event = callback

    def init_realtime(self, mode: object) -> None:
        self.init_realtime_calls.append(mode)
        ws = FakeWs()
        self.ws_instances.append(ws)
        sdk = self

        class _Intraday:
            def quote(self, *, symbol: str) -> dict[str, Any]:
                if sdk._quote_error is not None:
                    raise sdk._quote_error
                assert sdk._quote_payload is not None
                return {**sdk._quote_payload, "symbol": symbol}

        self.marketdata = SimpleNamespace(
            websocket_client=SimpleNamespace(stock=ws),
            rest_client=SimpleNamespace(stock=SimpleNamespace(intraday=_Intraday())),
        )

    def logout(self) -> bool:
        self.logged_out = True
        return True

    @property
    def ws(self) -> FakeWs:
        return self.ws_instances[-1]


def _account(account_type: str, account: str) -> SimpleNamespace:
    return SimpleNamespace(branch_no="20203", account=account, account_type=account_type, name="測試")


def _ok_login() -> SimpleNamespace:
    # Options account first on purpose: the client must filter by account_type, not take data[0].
    return SimpleNamespace(
        is_success=True, message=None, data=[_account("futopt", "9623985"), _account("stock", "7900015")]
    )


def _client(sdk: FakeSdk) -> FubonClient:
    return FubonClient(
        personal_id="A123456789",
        password="pw",
        cert_pfx=PFX_BYTES,
        cert_password="certpw",
        sdk_factory=lambda: sdk,
        realtime_mode="normal",
    )


def _aggregates(symbol: str = "2330") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "lastPrice": 999,
        "bids": [{"price": 567, "size": 1}],
        "asks": [{"price": 568, "size": 1}],
        "lastTrade": {"price": 568, "size": 1, "time": 1685338200000000},
    }


# --- login ---------------------------------------------------------------


def test_login_picks_stock_account_and_cleans_up_cert_file() -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)

    client.login()

    assert sdk.login_args is not None
    personal_id, password, cert_path, cert_password = sdk.login_args
    assert (personal_id, password, cert_password) == ("A123456789", "pw", "certpw")
    assert sdk.cert_path_existed_during_login is True
    assert sdk.cert_bytes_during_login == PFX_BYTES
    assert not os.path.exists(cert_path), "pfx temp file must be deleted right after login"
    assert client.account is not None
    assert client.account.account == "7900015"
    assert client.login_alive is True
    assert sdk.on_event is not None


def test_login_rejected_maps_to_safe_error_without_sdk_text() -> None:
    sdk = FakeSdk(login_result=SimpleNamespace(is_success=False, message="密碼錯誤 A123456789", data=None))
    client = _client(sdk)

    with pytest.raises(FubonLoginError) as exc_info:
        client.login()

    assert exc_info.value.failure_code == "login_rejected"
    assert "A123456789" not in str(exc_info.value)
    assert "A123456789" not in json.dumps(exc_info.value.details(), ensure_ascii=False)


def test_login_session_limit_maps_to_session_limit_code() -> None:
    sdk = FakeSdk(
        login_result=SimpleNamespace(is_success=False, message="Login Error, 超過本應用程式連線限制==>[10]", data=None)
    )

    with pytest.raises(FubonLoginError) as exc_info:
        _client(sdk).login()

    assert exc_info.value.failure_code == "session_limit"


def test_login_without_stock_account_is_rejected() -> None:
    sdk = FakeSdk(login_result=SimpleNamespace(is_success=True, message=None, data=[_account("futopt", "9623985")]))

    with pytest.raises(FubonLoginError) as exc_info:
        _client(sdk).login()

    assert exc_info.value.failure_code == "login_rejected"


# --- realtime ------------------------------------------------------------


def test_connect_realtime_subscribes_and_tracks_channel_ids() -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()
    client.connect_realtime()

    client.subscribe("2330")
    client.subscribe("2317")
    client.unsubscribe("2330")

    assert sdk.init_realtime_calls == ["normal"]
    assert sdk.ws.connect_calls == 1
    assert sdk.ws.subscribed == [
        {"channel": "aggregates", "symbol": "2330"},
        {"channel": "aggregates", "symbol": "2317"},
    ]
    assert sdk.ws.unsubscribed == [{"id": "ch-1"}]
    assert client.realtime_connected is True


def test_data_frames_reach_quote_handler_as_snapshots() -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    received: list[QuoteSnapshot] = []
    client.set_quote_handler(received.append)
    client.login()
    client.connect_realtime()
    client.subscribe("2330")

    sdk.ws.emit({"event": "data", "data": _aggregates(), "id": "ch-1", "channel": "aggregates"})
    sdk.ws.emit({"event": "heartbeat", "data": {"time": 1}})

    assert len(received) == 1
    assert received[0].symbol == "2330"
    assert received[0].last_price == Decimal("568")


def test_disconnect_only_flags_and_connect_again_rebuilds_channel_map() -> None:
    # Resubscribing is the provider's job (it owns the subscription set); the
    # client only has to come back with a fresh websocket and an empty channel map.
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()
    client.connect_realtime()
    client.subscribe("2330")
    first_ws = sdk.ws

    first_ws.drop()
    assert client.realtime_connected is False
    assert first_ws.connect_calls == 1, "on_disconnect must not reconnect by itself"

    client.connect_realtime()

    assert len(sdk.init_realtime_calls) == 2
    assert sdk.ws is not first_ws
    assert sdk.ws.connect_calls == 1
    assert sdk.ws.subscribed == [], "client does not resubscribe on its own"
    assert client.realtime_connected is True
    client.unsubscribe("2330")
    assert sdk.ws.unsubscribed == [], "stale channel id from the old socket must not be reused"
    client.subscribe("2330")
    client.unsubscribe("2330")
    assert sdk.ws.unsubscribed == [{"id": "ch-1"}]


def test_reconnect_failure_is_treated_as_login_lost() -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()
    client.connect_realtime()

    def boom(mode: object) -> None:
        raise RuntimeError("token exchange failed for A123456789")

    sdk.init_realtime = boom  # type: ignore[method-assign]

    with pytest.raises(QuoteProviderUnavailableError) as exc_info:
        client.connect_realtime()

    assert client.login_alive is False
    assert "A123456789" not in str(exc_info.value)


# --- trade-side events ---------------------------------------------------


@pytest.mark.parametrize("code", ["300", "301", "304"])
def test_disconnect_event_codes_flag_login_lost(code: str) -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()
    assert sdk.on_event is not None

    sdk.on_event(code, "whatever")

    assert client.login_alive is False


@pytest.mark.parametrize("code", ["100", "200", "201", "302", "500"])
def test_other_event_codes_keep_login_alive(code: str) -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()
    assert sdk.on_event is not None

    sdk.on_event(code, "whatever")

    assert client.login_alive is True


# --- REST quote ----------------------------------------------------------


def test_get_stock_quote_normalises_rest_payload() -> None:
    sdk = FakeSdk(login_result=_ok_login(), quote_payload=_aggregates())
    client = _client(sdk)
    client.login()
    client.connect_realtime()

    snapshot = client.get_stock_quote("2317")

    assert snapshot.symbol == "2317"
    assert snapshot.last_price == Decimal("568")
    assert snapshot.bid_price == Decimal("567")


def test_get_stock_quote_rate_limited_maps_to_provider_error() -> None:
    error = Exception("rate limited")
    error.status_code = 429  # type: ignore[attr-defined]
    sdk = FakeSdk(login_result=_ok_login(), quote_error=error)
    client = _client(sdk)
    client.login()
    client.connect_realtime()

    with pytest.raises(QuoteProviderUnavailableError) as exc_info:
        client.get_stock_quote("2330")

    assert exc_info.value.details() == {"provider": "fubon", "reason": "rate_limited"}


def test_logout_calls_sdk_and_marks_login_dead() -> None:
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()

    client.logout()

    assert sdk.logged_out is True
    assert client.login_alive is False


def test_ws_connect_failure_keeps_login_alive() -> None:
    # Real-SDK smoke (2026-09-18): the trade login succeeded but the market-data
    # websocket was refused with "Maximum number of connections reached". That is a
    # realtime-layer failure, not a lost login — the reconnect loop must not
    # escalate it into a full re-login.
    sdk = FakeSdk(login_result=_ok_login())
    client = _client(sdk)
    client.login()
    original_init = sdk.init_realtime

    def init_then_refuse(mode: object) -> None:
        original_init(mode)

        def refuse() -> None:
            raise RuntimeError("authentication timeout")

        sdk.ws.connect = refuse  # type: ignore[method-assign]

    sdk.init_realtime = init_then_refuse  # type: ignore[method-assign]

    with pytest.raises(QuoteProviderUnavailableError) as exc_info:
        client.connect_realtime()

    assert not isinstance(exc_info.value, FubonLoginError)
    assert exc_info.value.details() == {"provider": "fubon", "reason": "realtime_connect_failed"}
    assert client.login_alive is True
    assert client.realtime_connected is False
