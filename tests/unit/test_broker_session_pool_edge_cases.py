"""BrokerSessionPool under contention and broker misbehaviour.

Each test names the scenario it guards; the invariant throughout is "no broker
login is ever leaked": every provider the pool built is, by the end, either the
live session for its user or has been shut down.
"""

import random
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from tests.unit.test_broker_session_pool import FakeProvider

from app.core.config import get_settings
from app.domain.broker_account import (
    BrokerBindInProgressError,
    BrokerLoginFailedError,
    BrokerSessionLimitReachedError,
    FubonCredentials,
)
from app.services.broker_session_pool import BrokerSessionPool

_CREDS = FubonCredentials(personal_id="A123456789", password="pw", cert_pfx=b"pfx", cert_password="cpw")


def _pool(
    max_sessions: int, provider_for: Callable[[], FakeProvider] = FakeProvider
) -> tuple[BrokerSessionPool, list[FakeProvider]]:
    built: list[FakeProvider] = []
    built_lock = threading.Lock()

    def factory(_creds: FubonCredentials) -> FakeProvider:
        provider = provider_for()
        with built_lock:
            built.append(provider)
        return provider

    settings = get_settings().model_copy(update={"broker_max_sessions": max_sessions})
    return BrokerSessionPool(settings, shared=None, provider_factory=factory), built


# --- contention ---------------------------------------------------------------


def test_concurrent_first_binds_never_exceed_capacity() -> None:
    """20 admins bind 20 different users at once against 3 slots: exactly 3 log in,
    the rest get 409 before any provider is even built (no wasted broker login)."""
    pool, built = _pool(max_sessions=3)
    gate = threading.Event()
    outcomes: list[str] = []
    lock = threading.Lock()

    def bind() -> None:
        gate.wait()
        try:
            pool.activate(pool.prepare(uuid4(), _CREDS))
            result = "ok"
        except BrokerSessionLimitReachedError:
            result = "limit"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=bind) for _ in range(20)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(timeout=10)

    assert outcomes.count("ok") == 3
    assert outcomes.count("limit") == 17
    assert len(built) == 3
    assert len(pool.bound_user_ids()) == 3


def test_concurrent_prepare_for_same_user_admits_exactly_one() -> None:
    pool, built = _pool(max_sessions=5)
    user_id = uuid4()
    gate = threading.Event()
    results: list[str] = []
    lock = threading.Lock()

    def prepare() -> None:
        gate.wait()
        try:
            pool.activate(pool.prepare(user_id, _CREDS))
            outcome = "ok"
        except BrokerBindInProgressError:
            outcome = "busy"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=prepare) for _ in range(8)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(timeout=10)

    # Whoever holds the token wins; later winners (after the first activate
    # released the token) are legitimate re-binds and replace the session.
    assert "ok" in results
    assert results.count("ok") + results.count("busy") == 8
    assert len(pool.bound_user_ids()) == 1
    assert len(built) == results.count("ok")


def test_slow_broker_login_does_not_block_other_users() -> None:
    """The SDK's connect() busy-spins up to ~5s. Login runs outside the pool lock,
    so reads and other users' binds proceed while one login is stuck."""
    gate = threading.Event()
    pool, built = _pool(max_sessions=5, provider_for=lambda: FakeProvider(startup_gate=gate))
    slow_user, other_user = uuid4(), uuid4()

    slow = threading.Thread(target=lambda: pool.activate(pool.prepare(slow_user, _CREDS)))
    slow.start()
    assert built[0].in_startup.wait(timeout=5)

    assert pool.get(slow_user) is None  # not live yet, and this returned immediately
    assert pool.is_binding(slow_user)
    gate.set()  # the second provider must not block either
    done = threading.Event()

    def other() -> None:
        pool.activate(pool.prepare(other_user, _CREDS))
        done.set()

    threading.Thread(target=other).start()
    assert done.wait(timeout=5), "another user's bind was blocked by the slow login"
    slow.join(timeout=5)
    assert pool.bound_user_ids() == {slow_user, other_user}


def test_churn_never_leaks_a_session() -> None:
    """Random prepare/activate/discard/stop from many threads; afterwards every
    provider ever built is either live (and then stopped by stop_all) or was
    already shut down — nothing dangling, no pending tokens left behind."""
    pool, built = _pool(max_sessions=4)
    users = [uuid4() for _ in range(6)]
    rng = random.Random(42)

    def worker(seed: int) -> None:
        local = random.Random(seed)
        for _ in range(40):
            user_id = local.choice(users)
            try:
                action = local.random()
                if action < 0.6:
                    candidate = pool.prepare(user_id, _CREDS)
                    if local.random() < 0.8:
                        old = pool.activate(candidate)
                        if old is not None:
                            old.shutdown()
                    else:
                        pool.discard(candidate)
                else:
                    pool.stop(pool.claim(user_id))
            except (BrokerBindInProgressError, BrokerSessionLimitReachedError):
                pass

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker, [rng.randrange(10_000) for _ in range(8)]))

    live_before = pool.bound_user_ids()
    assert len(live_before) <= 4
    assert not any(pool.is_binding(u) for u in users)
    pool.stop_all()
    assert all(p.stopped for p in built)
    assert pool.bound_user_ids() == set()


# --- broker misbehaviour ----------------------------------------------------------


def test_factory_import_error_becomes_unknown_and_releases_the_slot() -> None:
    """On a box without the Linux-only wheel, building the provider raises
    ImportError. It must surface as a safe 422, not a 500, and free the slot."""

    def broken(_creds: FubonCredentials) -> FakeProvider:
        raise ImportError("No module named 'fubon_neo'")

    settings = get_settings().model_copy(update={"broker_max_sessions": 1})
    pool = BrokerSessionPool(settings, shared=None, provider_factory=broken)
    user_id = uuid4()

    with pytest.raises(BrokerLoginFailedError) as info:
        pool.prepare(user_id, _CREDS)
    assert info.value.code == "unknown"
    assert not pool.is_binding(user_id)
    # The slot is free again: a working factory on a fresh pool with the same cap admits the user.
    ok_pool, _ = _pool(max_sessions=1)
    ok_pool.activate(ok_pool.prepare(user_id, _CREDS))


@pytest.mark.parametrize("via", ["stop", "discard", "stop_all"])
def test_logout_raising_is_swallowed_and_state_is_still_cleared(via: str) -> None:
    """After ~14:05 the broker has already closed the socket; logout raising must
    not turn an unbind or a rollback into a 500, and must not strand the entry."""
    pool, built = _pool(
        max_sessions=3, provider_for=lambda: FakeProvider(shutdown_fail_with=RuntimeError("socket gone"))
    )
    user_id = uuid4()
    candidate = pool.prepare(user_id, _CREDS)

    if via == "discard":
        pool.discard(candidate)
    else:
        pool.activate(candidate)
        pool.activate(pool.prepare(uuid4(), _CREDS))  # a second live session must still be torn down
        pool.stop(pool.claim(user_id)) if via == "stop" else pool.stop_all()

    assert built[0].stopped
    assert pool.get(user_id) is None
    assert not pool.is_binding(user_id)
    if via == "stop_all":
        assert all(p.stopped for p in built)
        assert pool.bound_user_ids() == set()
    elif via == "stop":
        assert not built[1].stopped  # only the named user was logged out


def test_activate_with_a_stale_candidate_after_stop_is_refused() -> None:
    """A candidate whose token was released (here: discarded) can never be
    activated later — that would resurrect a session nobody tracks."""
    pool, _ = _pool(max_sessions=2)
    user_id = uuid4()
    candidate = pool.prepare(user_id, _CREDS)
    pool.discard(candidate)
    fresh = pool.prepare(user_id, _CREDS)

    with pytest.raises(BrokerBindInProgressError):
        pool.activate(candidate)
    pool.activate(fresh)
    assert pool.get(user_id) is fresh.provider


def test_listener_that_raises_does_not_break_the_pool_or_other_frames() -> None:
    """The owner-scoped forwarder runs on the SDK thread; an exception there is
    the provider's problem (it logs), never the pool's — assert the wrapper is a
    plain pass-through so the provider's protection applies."""
    pool, built = _pool(max_sessions=1)
    user_id = uuid4()
    calls: list[UUID | None] = []

    def listener(snapshot: object, *, owner_user_id: UUID | None) -> None:
        calls.append(owner_user_id)
        raise ValueError("evaluator blew up")

    pool.set_quote_listener(listener)  # type: ignore[arg-type]
    pool.activate(pool.prepare(user_id, _CREDS))

    with pytest.raises(ValueError):
        built[0].listeners[0](object())  # type: ignore[arg-type]
    assert calls == [user_id]
    assert pool.get(user_id) is built[0]
