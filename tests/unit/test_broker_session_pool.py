"""BrokerSessionPool over a recording fake provider: two-phase replace, capacity,
per-user operation token, failure-code mapping, shared mode."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings, get_settings
from app.domain.broker_account import (
    BrokerAccountNotBoundError,
    BrokerBindInProgressError,
    BrokerLoginFailedError,
    BrokerSessionLimitReachedError,
    FubonCredentials,
)
from app.services.broker_session_pool import BrokerSessionPool
from app.services.quote.base import QuoteListener, QuoteProviderUnavailableError, QuoteSnapshot

_CREDS = FubonCredentials(personal_id="A123456789", password="pw", cert_pfx=b"pfx", cert_password="cpw")


class FakeProvider:
    def __init__(self, *, fail_with: Exception | None = None, account_no: str = "9876543") -> None:
        self.fail_with = fail_with
        self.broker_account_no = account_no
        self.started = False
        self.stopped = False
        self.subscribed: set[str] = set()
        self.listeners: list[QuoteListener] = []

    def startup(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.started = True

    def shutdown(self) -> None:
        self.stopped = True

    def subscribe(self, symbol: str) -> None:
        self.subscribed.add(symbol)

    def unsubscribe(self, symbol: str) -> None:
        self.subscribed.discard(symbol)

    def active_subscriptions(self) -> set[str]:
        return set(self.subscribed)

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        return []

    def add_quote_listener(self, listener: QuoteListener) -> None:
        self.listeners.append(listener)

    def remove_quote_listener(self, listener: QuoteListener) -> None:
        self.listeners.remove(listener)


class _SdkLoginError(Exception):
    """Shape of FubonLoginError without importing the Fubon package."""

    def __init__(self, failure_code: str) -> None:
        super().__init__("SDK text that must never surface")
        self.failure_code = failure_code


def _settings(max_sessions: int = 2) -> Settings:
    return get_settings().model_copy(update={"broker_max_sessions": max_sessions})


def _pool(max_sessions: int = 2, **provider_kwargs: object) -> tuple[BrokerSessionPool, list[FakeProvider]]:
    built: list[FakeProvider] = []

    def factory(_creds: FubonCredentials) -> FakeProvider:
        provider = FakeProvider(**provider_kwargs)  # type: ignore[arg-type]
        built.append(provider)
        return provider

    return BrokerSessionPool(_settings(max_sessions), shared=None, provider_factory=factory), built


def _snapshot() -> QuoteSnapshot:
    now = datetime.now(tz=UTC)
    return QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal("1"),
        ask_price=Decimal("2"),
        last_price=Decimal("1"),
        quote_time=now,
        last_trade_time=now,
        received_at=now,
    )


def test_prepare_then_activate_makes_session_live_and_reads_account_no() -> None:
    pool, built = _pool()
    user_id = uuid4()

    candidate = pool.prepare(user_id, _CREDS)
    assert pool.get(user_id) is None  # not live until activate
    assert candidate.broker_account_no == "9876543"

    assert pool.activate(candidate) is None
    assert pool.get(user_id) is built[0]
    assert pool.require(user_id) is built[0]
    assert pool.bound_user_ids() == {user_id}
    assert built[0].started


def test_require_unbound_user_raises() -> None:
    pool, _ = _pool()
    with pytest.raises(BrokerAccountNotBoundError):
        pool.require(uuid4())


def test_activate_attaches_owner_scoped_listener() -> None:
    pool, built = _pool()
    user_id = uuid4()
    seen: list[tuple[str, UUID | None]] = []

    def listener(snapshot: QuoteSnapshot, *, owner_user_id: UUID | None) -> None:
        seen.append((snapshot.symbol, owner_user_id))

    pool.set_quote_listener(listener)
    pool.activate(pool.prepare(user_id, _CREDS))

    assert len(built[0].listeners) == 1
    built[0].listeners[0](_snapshot())
    assert seen == [("2330", user_id)]


def test_rebind_replaces_session_and_returns_old_provider() -> None:
    pool, built = _pool()
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))

    old = pool.activate(pool.prepare(user_id, _CREDS))

    assert old is built[0]
    assert pool.get(user_id) is built[1]
    assert not built[0].stopped  # caller shuts the old one down outside the lock


@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (_SdkLoginError("login_rejected"), "login_rejected"),
        (_SdkLoginError("session_limit"), "session_limit"),
        (QuoteProviderUnavailableError("fubon", "realtime_connect_failed"), "provider_unavailable"),
        (RuntimeError("boom A123456789"), "unknown"),
    ],
)
def test_prepare_failure_maps_to_safe_code_and_releases_token(exc: Exception, expected_code: str) -> None:
    pool, _ = _pool(max_sessions=1, fail_with=exc)
    user_id = uuid4()

    with pytest.raises(BrokerLoginFailedError) as info:
        pool.prepare(user_id, _CREDS)

    assert info.value.code == expected_code
    assert "A123456789" not in str(info.value)
    assert not pool.is_binding(user_id)
    # Token and slot released: the same user may try again, and the slot is free.
    ok_pool, _ = _pool(max_sessions=1)
    ok_pool.activate(ok_pool.prepare(user_id, _CREDS))


def test_capacity_counts_live_and_pending_but_not_rebinds() -> None:
    pool, _ = _pool(max_sessions=1)
    first, second = uuid4(), uuid4()
    pool.activate(pool.prepare(first, _CREDS))

    with pytest.raises(BrokerSessionLimitReachedError) as info:
        pool.prepare(second, _CREDS)
    assert info.value.limit == 1

    # Replacing the existing user's session needs no extra slot.
    rebind = pool.prepare(first, _CREDS)
    with pytest.raises(BrokerSessionLimitReachedError):
        pool.prepare(second, _CREDS)
    pool.discard(rebind)

    # A pending first bind reserves the slot until activated or discarded.
    pool.stop(first)
    pending = pool.prepare(second, _CREDS)
    with pytest.raises(BrokerSessionLimitReachedError):
        pool.prepare(first, _CREDS)
    pool.discard(pending)
    pool.activate(pool.prepare(first, _CREDS))


def test_same_user_second_prepare_and_stop_are_refused_while_pending() -> None:
    pool, _ = _pool()
    user_id = uuid4()
    candidate = pool.prepare(user_id, _CREDS)

    assert pool.is_binding(user_id)
    with pytest.raises(BrokerBindInProgressError):
        pool.prepare(user_id, _CREDS)
    with pytest.raises(BrokerBindInProgressError):
        pool.stop(user_id)

    pool.activate(candidate)
    assert not pool.is_binding(user_id)


def test_discard_releases_token_and_shuts_candidate_down() -> None:
    pool, built = _pool()
    user_id = uuid4()
    candidate = pool.prepare(user_id, _CREDS)

    pool.discard(candidate)

    assert built[0].stopped
    assert pool.get(user_id) is None
    with pytest.raises(BrokerBindInProgressError):
        pool.activate(candidate)  # a discarded candidate can never go live


def test_stop_logs_out_and_is_idempotent() -> None:
    pool, built = _pool()
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))

    pool.stop(user_id)
    pool.stop(user_id)

    assert built[0].stopped
    assert pool.get(user_id) is None


def test_stop_all_shuts_every_live_session_down() -> None:
    pool, built = _pool()
    for _ in range(2):
        pool.activate(pool.prepare(uuid4(), _CREDS))

    pool.stop_all()

    assert all(p.stopped for p in built)
    assert pool.bound_user_ids() == set()


def test_shared_mode_serves_everyone_and_refuses_prepare() -> None:
    shared = FakeProvider()
    pool = BrokerSessionPool(_settings(), shared=shared)

    assert not pool.per_user
    assert pool.get(uuid4()) is shared
    assert pool.require(uuid4()) is shared
    with pytest.raises(BrokerLoginFailedError) as info:
        pool.prepare(uuid4(), _CREDS)
    assert info.value.code == "provider_unavailable"
    pool.stop_all()
    assert not shared.stopped  # lifespan owns the shared provider's shutdown
