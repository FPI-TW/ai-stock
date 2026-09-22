"""FubonQuoteProvider over a duck-typed fake FubonClient.

Mirrors the shioaji_demo provider tests: no SDK, no network; the client is a
recording fake so the provider's bookkeeping (started flag, subscription set,
snapshot cache, listeners, quota) is what gets exercised.
"""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.quote.base import QuoteProviderUnavailableError, QuoteSnapshot, QuoteUnavailableError
from app.services.quote.fubon.client import QuoteHandler
from app.services.quote.fubon.provider import FubonQuoteProvider, FubonSubscriptionLimitExceeded

TAIPEI = ZoneInfo("Asia/Taipei")


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.quote_handler: QuoteHandler | None = None
        self.rest_snapshot: QuoteSnapshot | None = None
        self.login_alive = False
        self.realtime_connected = False

    def login(self) -> None:
        self.calls.append("login")
        self.login_alive = True

    def logout(self) -> None:
        self.calls.append("logout")
        self.login_alive = False

    def set_quote_handler(self, handler: QuoteHandler) -> None:
        self.quote_handler = handler

    def connect_realtime(self) -> None:
        self.calls.append("connect")
        self.realtime_connected = True

    def subscribe(self, symbol: str) -> None:
        self.calls.append(f"sub:{symbol}")

    def unsubscribe(self, symbol: str) -> None:
        self.calls.append(f"unsub:{symbol}")

    def get_stock_quote(self, symbol: str) -> QuoteSnapshot:
        self.calls.append(f"rest:{symbol}")
        assert self.rest_snapshot is not None
        return self.rest_snapshot

    def push(self, snapshot: QuoteSnapshot) -> None:
        assert self.quote_handler is not None
        self.quote_handler(snapshot)


def _snapshot(symbol: str = "2330", last: str = "568") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=Decimal("567"),
        ask_price=Decimal("568"),
        last_price=Decimal(last),
        quote_time=datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI),
        last_trade_time=datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI),
        received_at=datetime.now(tz=UTC),
    )


def _started(max_subscriptions: int = 300) -> tuple[FubonQuoteProvider, FakeClient]:
    client = FakeClient()
    provider = FubonQuoteProvider(client=client, max_subscriptions=max_subscriptions)  # type: ignore[arg-type]
    provider.startup()
    return provider, client


def test_startup_logs_in_wires_handler_and_connects() -> None:
    provider, client = _started()

    assert client.calls == ["login", "connect"]
    assert client.quote_handler is not None
    assert provider.active_subscriptions() == set()

    provider.startup()  # idempotent
    assert client.calls == ["login", "connect"]


def test_startup_logs_out_when_connect_realtime_fails() -> None:
    class ConnectFailsClient(FakeClient):
        def connect_realtime(self) -> None:
            self.calls.append("connect")
            raise RuntimeError("ws down")

    client = ConnectFailsClient()
    provider = FubonQuoteProvider(client=client, max_subscriptions=300)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        provider.startup()

    assert client.calls == ["login", "connect", "logout"]
    assert client.login_alive is False
    provider.shutdown()  # never started → no second logout
    assert client.calls == ["login", "connect", "logout"]


def test_shutdown_still_logs_out_when_unsubscribe_raises() -> None:
    class UnsubscribeFailsClient(FakeClient):
        def unsubscribe(self, symbol: str) -> None:
            self.calls.append(f"unsub:{symbol}")
            raise RuntimeError("Connection to remote host was lost.")

    client = UnsubscribeFailsClient()
    provider = FubonQuoteProvider(client=client, max_subscriptions=300)  # type: ignore[arg-type]
    provider.startup()
    provider.subscribe("2330")
    provider.subscribe("2317")
    client.calls.clear()

    provider.shutdown()  # must not raise

    assert client.calls[-1] == "logout"
    assert set(client.calls[:-1]) == {"unsub:2330", "unsub:2317"}
    assert client.login_alive is False
    assert provider.active_subscriptions() == set()


def test_shutdown_unsubscribes_everything_then_logs_out() -> None:
    provider, client = _started()
    provider.subscribe("2330")
    provider.subscribe("2317")
    client.calls.clear()

    provider.shutdown()

    assert client.calls[-1] == "logout"
    assert set(client.calls[:-1]) == {"unsub:2330", "unsub:2317"}
    assert provider.active_subscriptions() == set()


def test_subscribe_before_startup_is_unavailable() -> None:
    provider = FubonQuoteProvider(client=FakeClient(), max_subscriptions=300)  # type: ignore[arg-type]

    with pytest.raises(QuoteProviderUnavailableError):
        provider.subscribe("2330")


def test_subscribe_is_idempotent_and_enforces_quota() -> None:
    provider, client = _started(max_subscriptions=2)

    provider.subscribe("2330")
    provider.subscribe("2330")
    provider.subscribe("2317")

    assert client.calls.count("sub:2330") == 1
    with pytest.raises(FubonSubscriptionLimitExceeded) as exc_info:
        provider.subscribe("2454")
    assert exc_info.value.http_status == 409
    assert exc_info.value.details() == {"symbol": "2454", "current": 2, "limit": 2}
    assert provider.active_subscriptions() == {"2330", "2317"}


def test_pushed_snapshot_is_cached_and_fires_listeners() -> None:
    provider, client = _started()
    provider.subscribe("2330")
    seen: list[QuoteSnapshot] = []
    provider.add_quote_listener(seen.append)

    def bad_listener(_snapshot: QuoteSnapshot) -> None:
        raise RuntimeError("listener bug must not reach the SDK thread")

    provider.add_quote_listener(bad_listener)

    client.push(_snapshot(last="568"))
    client.push(_snapshot(last="569"))

    [cached] = provider.get_quotes(["2330"])
    assert cached.last_price == Decimal("569")
    assert [s.last_price for s in seen] == [Decimal("568"), Decimal("569")]


def test_get_quotes_raises_for_symbol_without_snapshot() -> None:
    provider, _ = _started()
    provider.subscribe("2330")

    with pytest.raises(QuoteUnavailableError):
        provider.get_quotes(["2330"])


def test_unsubscribe_drops_cache_and_is_noop_when_unknown() -> None:
    provider, client = _started()
    provider.subscribe("2330")
    client.push(_snapshot())
    client.calls.clear()

    provider.unsubscribe("2330")
    provider.unsubscribe("2330")

    assert client.calls == ["unsub:2330"]
    with pytest.raises(QuoteUnavailableError):
        provider.get_quotes(["2330"])


def test_unsubscribe_clears_local_state_when_client_reports_provider_error() -> None:
    class UnsubscribeUnavailableClient(FakeClient):
        def unsubscribe(self, symbol: str) -> None:
            self.calls.append(f"unsub:{symbol}")
            raise QuoteProviderUnavailableError("fubon", "unsubscribe_failed")

    client = UnsubscribeUnavailableClient()
    provider = FubonQuoteProvider(client=client, max_subscriptions=300)  # type: ignore[arg-type]
    provider.startup()
    provider.subscribe("2330")
    client.push(_snapshot())

    provider.unsubscribe("2330")  # must not raise

    assert provider.active_subscriptions() == set()
    with pytest.raises(QuoteUnavailableError):
        provider.get_quotes(["2330"])


def test_get_current_price_delegates_to_rest() -> None:
    provider, client = _started()
    client.rest_snapshot = _snapshot(symbol="2317")

    snapshot = provider.get_current_price("2317")

    assert snapshot.symbol == "2317"
    assert client.calls[-1] == "rest:2317"


def test_get_current_price_before_startup_is_unavailable() -> None:
    provider = FubonQuoteProvider(client=FakeClient(), max_subscriptions=300)  # type: ignore[arg-type]

    with pytest.raises(QuoteProviderUnavailableError):
        provider.get_current_price("2330")


def test_remove_listener_is_idempotent() -> None:
    provider, client = _started()
    provider.subscribe("2330")
    seen: list[QuoteSnapshot] = []
    provider.add_quote_listener(seen.append)
    provider.remove_quote_listener(seen.append)
    provider.remove_quote_listener(seen.append)

    client.push(_snapshot())

    assert seen == []


def test_reconnect_realtime_reconnects_then_resubscribes_owned_set() -> None:
    provider, client = _started()
    provider.subscribe("2330")
    provider.subscribe("2317")
    provider.unsubscribe("2317")
    client.realtime_connected = False
    client.login_alive = False
    assert provider.realtime_connected is False
    assert provider.login_alive is False
    client.calls.clear()

    provider.reconnect_realtime()

    assert client.calls == ["connect", "sub:2330"]
    assert provider.realtime_connected is True
