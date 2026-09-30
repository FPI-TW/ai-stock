"""`BrokerSessionReconnectLoop`: one 30 s tick that, inside trading-day hours only,
rebuilds dropped market-data sockets and re-logs lost logins / failed bindings
through `restore_bound_user` (docs/architecture.md「per-user 模式的生命週期」).

Sessions are real `FubonQuoteProvider`s over the recording `FakeClient`, so the
flags the loop reads (`login_alive`, `realtime_connected`) are the provider's own.
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from tests.unit.quote.test_fubon_provider import FakeClient
from tests.unit.test_broker_session_pool import _CREDS, _login_error, _settings, live_session

from app.domain.broker_account import LOGIN_FAILURE_MESSAGES, BrokerLoginFailureCode, FubonCredentials
from app.domain.trading_session import TradingSessionService
from app.services.broker_session_pool import BrokerSessionPool
from app.services.broker_session_reconnect import BrokerSessionReconnectLoop
from app.services.quote.fubon.provider import FubonQuoteProvider

TAIPEI = ZoneInfo("Asia/Taipei")
IN_SESSION = datetime(2026, 9, 30, 10, 0, tzinfo=TAIPEI)  # Wednesday
PRE_OPEN = datetime(2026, 9, 30, 8, 29, tzinfo=TAIPEI)
AFTER_CLOSE = datetime(2026, 9, 30, 13, 35, tzinfo=TAIPEI)
SATURDAY = datetime(2026, 10, 3, 10, 0, tzinfo=TAIPEI)


class FailingLoginClient(FakeClient):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    def login(self) -> None:
        raise self.exc


def _pool(client_outcomes: list[Exception | None] | None = None) -> tuple[BrokerSessionPool, list[FakeClient]]:
    """A per-user pool building a real Fubon provider per login; `client_outcomes`
    picks, per built client in order, an exception its login raises (None = ok)."""
    clients: list[FakeClient] = []
    outcomes = iter(client_outcomes or [])

    def factory(_creds: FubonCredentials) -> FubonQuoteProvider:
        exc = next(outcomes, None)
        client = FakeClient() if exc is None else FailingLoginClient(exc)
        clients.append(client)
        return FubonQuoteProvider(client=client)  # type: ignore[arg-type]  # FakeClient stands in for FubonClient

    return BrokerSessionPool(_settings(), shared=None, provider_factory=factory), clients


def _account(user_id: UUID, *, status: str = "active", last_error: str | None = None) -> MagicMock:
    return MagicMock(user_id=user_id, status=status, last_error=last_error)


def _loop(
    pool: BrokerSessionPool,
    *,
    now: datetime = IN_SESSION,
    credentials: object = _CREDS,
    accounts_rows: list[MagicMock] | None = None,
    user_status: str = "active",
    session_factory: MagicMock | None = None,
) -> tuple[BrokerSessionReconnectLoop, MagicMock]:
    """`accounts_rows` defaults to one active row per live session (the normal
    case); pass rows explicitly to describe bindings without a session."""
    db = MagicMock()
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    accounts = MagicMock()
    accounts.get_credentials.return_value = credentials
    accounts.list_all.return_value = (
        accounts_rows if accounts_rows is not None else [_account(user_id) for user_id, _ in pool.live_sessions()]
    )
    core_intents = MagicMock()
    core_intents.active_or_scheduled_symbols_by_owner.return_value = {"2330"}
    users = MagicMock()
    users.get_by_id.return_value = MagicMock(status=user_status)
    loop = BrokerSessionReconnectLoop(
        pool=pool,
        session_factory=session_factory or MagicMock(return_value=db),
        accounts_for=lambda _db: accounts,
        core_intents_for=lambda _db: core_intents,
        users_for=lambda _db: users,
        session_service=TradingSessionService(clock=lambda: now),
        interval_seconds=30,
    )
    return loop, accounts


def _live() -> tuple[BrokerSessionPool, list[FakeClient], UUID]:
    pool, clients = _pool()
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    return pool, clients, user_id


def _connects(client: FakeClient) -> int:
    return client.calls.count("connect")


@pytest.mark.parametrize("now", [PRE_OPEN, AFTER_CLOSE, SATURDAY])
def test_outside_the_trading_window_nothing_is_touched(now: datetime) -> None:
    pool, clients, _ = _live()
    clients[0].realtime_connected = False
    clients[0].login_alive = False
    loop, accounts = _loop(pool, now=now)

    loop.run_once()

    assert _connects(clients[0]) == 1  # startup only
    accounts.get_credentials.assert_not_called()


def test_dropped_market_data_socket_is_rebuilt_in_session() -> None:
    pool, clients, _ = _live()
    client = clients[0]
    client.realtime_connected = False
    loop, accounts = _loop(pool)

    loop.run_once()
    loop.run_once()  # connected again: no second rebuild

    assert _connects(client) == 2  # startup + one repair
    assert client.realtime_connected
    accounts.get_credentials.assert_not_called()


def test_lost_login_is_replaced_by_a_fresh_session_and_the_old_one_retired() -> None:
    pool, clients, user_id = _live()
    dead = clients[0]
    dead.login_alive = False
    dead.realtime_connected = False
    loop, accounts = _loop(pool)

    loop.run_once()

    assert _connects(dead) == 1, "no socket rebuild on a dead login"
    assert dead.calls[-1] == "logout"  # retired after the swap
    fresh = live_session(pool, user_id)
    assert isinstance(fresh, FubonQuoteProvider) and fresh.active_subscriptions() == {"2330"}
    assert "sub:2330" in clients[1].calls
    accounts.get_credentials.assert_called_once_with(user_id)


def test_failed_relogin_is_retried_next_tick_while_the_dead_session_stays() -> None:
    # live, failed candidate, good candidate
    pool, clients = _pool([None, _login_error("provider_unavailable"), None])
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    dead = live_session(pool, user_id)
    clients[0].login_alive = False
    loop, accounts = _loop(pool)

    loop.run_once()
    assert live_session(pool, user_id) is dead  # dead session kept until a replacement exists
    accounts.mark_login_failed.assert_called_once_with(
        user_id, "provider_unavailable", now=IN_SESSION.astimezone(TAIPEI)
    )

    loop.run_once()
    assert live_session(pool, user_id) is not dead
    assert clients[0].calls[-1] == "logout"
    assert clients[2].login_alive
    accounts.mark_login_ok.assert_called_once()


def test_unbound_row_disappeared_drops_the_user() -> None:
    """Admin unbound the user between the login loss and this tick: no row, so the
    loop never even reaches them (a stale live entry would have been stopped by the
    unbind); with a row but no credentials, nothing is attempted either."""
    pool, clients, user_id = _live()
    clients[0].login_alive = False
    dead = live_session(pool, user_id)
    loop, _ = _loop(pool, credentials=None)

    loop.run_once()

    assert live_session(pool, user_id) is dead  # left as is: no credentials to retry with
    assert len(clients) == 1


def test_a_session_the_admin_stopped_is_never_logged_back_in() -> None:
    """Race the question is about: the loop saw the login die, then the admin
    disabled the account (claim -> commit -> stop) before the loop's next tick. The
    binding row is kept on disable, so a loop that remembered the user would log a
    disabled account straight back in. It must only repair sessions that exist."""
    pool, clients, user_id = _live()
    clients[0].login_alive = False
    loop, accounts = _loop(pool, now=PRE_OPEN)
    loop.run_once()  # outside the window: the loss is observed, nothing done yet
    pool.stop(pool.claim(user_id))  # the admin disables the account

    # Disable keeps the binding row; only the user's status says no.
    loop, accounts = _loop(pool, accounts_rows=[_account(user_id)], user_status="disabled")
    loop.run_once()

    assert live_session(pool, user_id) is None
    assert len(clients) == 1
    accounts.get_credentials.assert_not_called()


def test_a_session_the_admin_rebound_meanwhile_is_left_alone() -> None:
    """The admin re-bound the user between ticks: the live session is fresh, so no
    second login (which would replace the admin's session with another one)."""
    pool, clients, user_id = _live()
    clients[0].login_alive = False
    replaced = pool.activate(pool.prepare(user_id, _CREDS))  # admin's re-bind, healthy
    assert replaced is not None and replaced is not live_session(pool, user_id)
    loop, accounts = _loop(pool)

    loop.run_once()

    assert len(clients) == 2
    assert clients[1].login_alive
    accounts.get_credentials.assert_not_called()


def test_relogin_is_refused_while_the_admin_holds_the_users_token() -> None:
    """Bind / disable in flight: the token is theirs; the loop backs off to the next tick."""
    pool, clients, user_id = _live()
    clients[0].login_alive = False
    dead = live_session(pool, user_id)
    claim = pool.claim(user_id)
    loop, accounts = _loop(pool)

    loop.run_once()

    assert live_session(pool, user_id) is dead
    assert len(clients) == 1
    accounts.mark_login_failed.assert_not_called()
    pool.release(claim)


def test_one_users_failed_failure_record_does_not_stop_the_others_in_the_tick() -> None:
    """Two lost logins share the tick's Session. Recording the first user's login
    failure blows up on commit; the Session is rolled back so the second user is
    still restored in the same tick."""
    # live A, live B, A's candidate fails, B's candidate succeeds
    pool, clients = _pool([None, None, _login_error("provider_unavailable"), None])
    user_a, user_b = uuid4(), uuid4()
    pool.activate(pool.prepare(user_a, _CREDS))
    pool.activate(pool.prepare(user_b, _CREDS))
    clients[0].login_alive = False
    clients[1].login_alive = False
    db = MagicMock()
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    db.commit.side_effect = [RuntimeError("db down while recording"), None]
    loop, accounts = _loop(
        pool, accounts_rows=[_account(user_a), _account(user_b)], session_factory=MagicMock(return_value=db)
    )

    loop.run_once()

    db.rollback.assert_called_once()
    assert clients[3].login_alive  # B got its fresh session
    accounts.mark_login_ok.assert_called_once_with(user_b, now=IN_SESSION.astimezone(TAIPEI))


def test_tick_survives_a_broken_database() -> None:
    pool, clients, user_id = _live()
    clients[0].login_alive = False
    dead = live_session(pool, user_id)
    loop, _ = _loop(pool, session_factory=MagicMock(side_effect=RuntimeError("db down")))

    loop.run_once()  # must not raise

    assert live_session(pool, user_id) is dead  # still dead: retried next tick


# --- bindings without a live session (startup / crash-restart failures) -----------


def test_binding_whose_startup_login_failed_transiently_is_retried() -> None:
    """Review finding (PR #98): the loop only repaired live sessions, so a binding
    whose login failed at boot — the crash-restart case: Compose restarts inside
    the broker's ~60 s residual-session window and every login answers
    session_limit — stayed `login_failed` for good."""
    pool, clients = _pool()
    user_id = uuid4()
    row = _account(user_id, status="login_failed", last_error=LOGIN_FAILURE_MESSAGES["session_limit"])
    loop, accounts = _loop(pool, accounts_rows=[row])

    loop.run_once()

    restored = live_session(pool, user_id)
    assert isinstance(restored, FubonQuoteProvider) and restored.active_subscriptions() == {"2330"}
    assert clients[0].login_alive
    accounts.mark_login_ok.assert_called_once()


def test_bound_row_without_a_session_is_logged_in() -> None:
    """`activate` failed after the bind committed: row says active, pool has nothing."""
    pool, clients = _pool()
    user_id = uuid4()
    loop, _ = _loop(pool, accounts_rows=[_account(user_id, status="active")])

    loop.run_once()

    assert live_session(pool, user_id) is not None
    assert clients[0].login_alive


@pytest.mark.parametrize("code", ["login_rejected", "cert_invalid", "credentials_unreadable"])
def test_binding_whose_last_failure_needs_a_human_is_not_retried(code: BrokerLoginFailureCode) -> None:
    """Wrong password, bad cert, rotated key: retrying every 30 s cannot succeed and
    repeated refused logins risk the broker locking the account. The admin re-binds."""
    pool, clients = _pool()
    row = _account(uuid4(), status="login_failed", last_error=LOGIN_FAILURE_MESSAGES[code])
    loop, accounts = _loop(pool, accounts_rows=[row])

    loop.run_once()

    assert clients == []
    accounts.get_credentials.assert_not_called()


def test_dead_login_is_not_retried_once_the_broker_rejected_the_credentials() -> None:
    pool, clients, user_id = _live()
    clients[0].login_alive = False
    row = _account(user_id, status="login_failed", last_error=LOGIN_FAILURE_MESSAGES["login_rejected"])
    loop, accounts = _loop(pool, accounts_rows=[row])

    loop.run_once()

    assert len(clients) == 1
    accounts.get_credentials.assert_not_called()


def test_binding_without_a_session_waits_for_a_free_slot_instead_of_hammering_the_pool() -> None:
    """The pool is full: trying would fail at `prepare` every 30 s and rewrite the
    row each time. Skip until a slot frees (unbind / disable), then log in."""
    pool, clients = _pool()  # BROKER_MAX_SESSIONS = 2
    a, b, waiting = uuid4(), uuid4(), uuid4()
    pool.activate(pool.prepare(a, _CREDS))
    pool.activate(pool.prepare(b, _CREDS))
    rows = [
        _account(a),
        _account(b),
        _account(waiting, status="login_failed", last_error=LOGIN_FAILURE_MESSAGES["session_pool_full"]),
    ]
    loop, accounts = _loop(pool, accounts_rows=rows)

    loop.run_once()
    assert len(clients) == 2
    accounts.get_credentials.assert_not_called()
    accounts.mark_login_failed.assert_not_called()

    pool.stop(pool.claim(a))  # the admin unbinds A: session gone, row gone
    loop, accounts = _loop(pool, accounts_rows=rows[1:])
    loop.run_once()
    assert live_session(pool, waiting) is not None
    assert clients[2].login_alive
    accounts.mark_login_ok.assert_called_once_with(waiting, now=IN_SESSION.astimezone(TAIPEI))


def test_a_provider_that_is_not_fubon_is_reported_not_guessed_healthy() -> None:
    """Per-user mode holds one provider type; anything else is a wiring bug and is
    logged as such instead of being treated as healthy."""
    pool = BrokerSessionPool(_settings(), shared=None, provider_factory=lambda _c: MagicMock())
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    loop, accounts = _loop(pool)

    loop.run_once()

    accounts.get_credentials.assert_not_called()


def test_run_forever_ticks_then_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _, _ = _live()
    loop, _ = _loop(pool)
    calls: list[str] = []
    monkeypatch.setattr(loop, "run_once", lambda: calls.append("tick"))

    async def fake_sleep(seconds: float) -> None:
        calls.append(f"sleep {seconds}")
        raise asyncio.CancelledError

    monkeypatch.setattr("app.services.broker_session_reconnect.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run_forever())

    assert calls == ["tick", "sleep 30"]
