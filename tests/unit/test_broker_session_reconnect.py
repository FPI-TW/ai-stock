"""`BrokerSessionReconnectLoop`: one 30 s tick that, inside trading-day hours only,
rebuilds dropped market-data sockets and re-logs lost logins through
`restore_bound_user` (docs/architecture.md「per-user 模式的生命週期」)."""

from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from tests.unit.test_broker_session_pool import _CREDS, FakeProvider, _login_error, _pool, live_session

from app.domain.broker_account import LOGIN_FAILURE_MESSAGES
from app.domain.trading_session import TradingSessionService
from app.services.broker_session_pool import BrokerSessionPool
from app.services.broker_session_reconnect import BrokerSessionReconnectLoop

TAIPEI = ZoneInfo("Asia/Taipei")
IN_SESSION = datetime(2026, 9, 30, 10, 0, tzinfo=TAIPEI)  # Wednesday
PRE_OPEN = datetime(2026, 9, 30, 8, 29, tzinfo=TAIPEI)
AFTER_CLOSE = datetime(2026, 9, 30, 13, 35, tzinfo=TAIPEI)
SATURDAY = datetime(2026, 10, 3, 10, 0, tzinfo=TAIPEI)


class ReconnectableFake(FakeProvider):
    """The two flags the Fubon provider exposes plus the socket rebuild."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.login_alive = True
        self.realtime_connected = True
        self.reconnects = 0

    def reconnect_realtime(self) -> None:
        self.reconnects += 1
        self.realtime_connected = True


def _account(user_id: UUID, *, status: str = "active", last_error: str | None = None) -> MagicMock:
    return MagicMock(user_id=user_id, status=status, last_error=last_error)


def _loop(
    pool: BrokerSessionPool,
    *,
    now: datetime = IN_SESSION,
    credentials: object = _CREDS,
    accounts_rows: list[MagicMock] | None = None,
    user_status: str = "active",
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
        session_factory=lambda: db,
        accounts_for=lambda _db: accounts,
        core_intents_for=lambda _db: core_intents,
        users_for=lambda _db: users,
        session_service=TradingSessionService(clock=lambda: now),
        interval_seconds=30,
    )
    return loop, accounts


def _live(pool_kwargs: dict[str, object] | None = None) -> tuple[BrokerSessionPool, list[FakeProvider], UUID]:
    pool, built = _pool(provider_for=lambda: ReconnectableFake(**(pool_kwargs or {})))
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    return pool, built, user_id


@pytest.mark.parametrize("now", [PRE_OPEN, AFTER_CLOSE, SATURDAY])
def test_outside_the_trading_window_nothing_is_touched(now: datetime) -> None:
    pool, built, _ = _live()
    built[0].realtime_connected = False  # type: ignore[attr-defined]
    built[0].login_alive = False  # type: ignore[attr-defined]
    loop, accounts = _loop(pool, now=now)

    loop.run_once()

    assert built[0].reconnects == 0  # type: ignore[attr-defined]
    accounts.get_credentials.assert_not_called()


def test_dropped_market_data_socket_is_rebuilt_in_session() -> None:
    pool, built, _ = _live()
    provider = built[0]
    provider.realtime_connected = False  # type: ignore[attr-defined]
    loop, accounts = _loop(pool)

    loop.run_once()
    loop.run_once()  # connected again: no second rebuild

    assert provider.reconnects == 1  # type: ignore[attr-defined]
    assert provider.realtime_connected  # type: ignore[attr-defined]
    accounts.get_credentials.assert_not_called()


def test_lost_login_is_replaced_by_a_fresh_session_and_the_old_one_retired() -> None:
    pool, built, user_id = _live()
    dead = built[0]
    dead.login_alive = False  # type: ignore[attr-defined]
    dead.realtime_connected = False  # type: ignore[attr-defined]
    loop, accounts = _loop(pool)

    loop.run_once()

    assert dead.reconnects == 0, "no socket rebuild on a dead login"  # type: ignore[attr-defined]
    assert dead.stopped
    assert live_session(pool, user_id) is built[1]
    assert built[1].subscribed == {"2330"}
    accounts.get_credentials.assert_called_once_with(user_id)


def test_failed_relogin_is_retried_next_tick_while_the_dead_session_stays() -> None:
    outcomes = iter([None, _login_error("provider_unavailable"), None])  # live, failed candidate, good candidate
    pool, built = _pool(provider_for=lambda: ReconnectableFake(fail_with=next(outcomes)))
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    built[0].login_alive = False  # type: ignore[attr-defined]
    loop, accounts = _loop(pool)

    loop.run_once()
    assert live_session(pool, user_id) is built[0]  # dead session kept until a replacement exists
    accounts.mark_login_failed.assert_called_once_with(
        user_id, "provider_unavailable", now=IN_SESSION.astimezone(TAIPEI)
    )

    loop.run_once()
    assert live_session(pool, user_id) is built[2]
    assert built[0].stopped
    accounts.mark_login_ok.assert_called_once()


def test_unbound_row_disappeared_drops_the_pending_user() -> None:
    """Admin unbound the user between the login loss and this tick."""
    pool, built, user_id = _live()
    built[0].login_alive = False  # type: ignore[attr-defined]
    loop, _ = _loop(pool, credentials=None)

    loop.run_once()

    assert live_session(pool, user_id) is built[0]  # left as is: no credentials to retry with
    assert len(built) == 1


def test_a_session_the_admin_stopped_is_never_logged_back_in() -> None:
    """Race the question is about: the loop saw the login die, then the admin
    disabled the account (claim -> commit -> stop) before the loop's next tick. The
    binding row is kept on disable, so a loop that remembered the user would log a
    disabled account straight back in. It must only repair sessions that exist."""
    pool, built, user_id = _live()
    built[0].login_alive = False  # type: ignore[attr-defined]
    loop, accounts = _loop(pool, now=PRE_OPEN)
    loop.run_once()  # outside the window: the loss is observed, nothing done yet
    pool.stop(pool.claim(user_id))  # the admin disables the account

    # Disable keeps the binding row; only the user's status says no.
    loop, accounts = _loop(pool, accounts_rows=[_account(user_id)], user_status="disabled")
    loop.run_once()

    assert live_session(pool, user_id) is None
    assert len(built) == 1
    accounts.get_credentials.assert_not_called()


def test_a_session_the_admin_rebound_meanwhile_is_left_alone() -> None:
    """The admin re-bound the user between ticks: the live session is fresh, so no
    second login (which would replace the admin's session with another one)."""
    pool, built, user_id = _live()
    built[0].login_alive = False  # type: ignore[attr-defined]
    replaced = pool.activate(pool.prepare(user_id, _CREDS))  # admin's re-bind, healthy
    assert replaced is built[0]
    loop, accounts = _loop(pool)

    loop.run_once()

    assert live_session(pool, user_id) is built[1]
    assert len(built) == 2
    accounts.get_credentials.assert_not_called()


def test_relogin_is_refused_while_the_admin_holds_the_users_token() -> None:
    """Bind / disable in flight: the token is theirs; the loop backs off to the next tick."""
    pool, built, user_id = _live()
    built[0].login_alive = False  # type: ignore[attr-defined]
    claim = pool.claim(user_id)
    loop, accounts = _loop(pool)

    loop.run_once()

    assert live_session(pool, user_id) is built[0]
    assert len(built) == 1
    accounts.mark_login_failed.assert_not_called()
    pool.release(claim)


def test_tick_survives_a_broken_database() -> None:
    pool, built, user_id = _live()
    built[0].login_alive = False  # type: ignore[attr-defined]
    loop, _ = _loop(pool)
    loop._session_factory = MagicMock(side_effect=RuntimeError("db down"))  # type: ignore[assignment]

    loop.run_once()  # must not raise

    assert live_session(pool, user_id) is built[0]  # still dead: retried next tick


# --- bindings without a live session (startup / crash-restart failures) -----------


def test_binding_whose_startup_login_failed_transiently_is_retried() -> None:
    """Review finding (PR #98): the loop only repaired live sessions, so a binding
    whose login failed at boot — the crash-restart case: Compose restarts inside
    the broker's ~60 s residual-session window and every login answers
    session_limit — stayed `login_failed` for good."""
    pool, built = _pool(provider_for=ReconnectableFake)
    user_id = uuid4()
    row = _account(user_id, status="login_failed", last_error=LOGIN_FAILURE_MESSAGES["session_limit"])
    loop, accounts = _loop(pool, accounts_rows=[row])

    loop.run_once()

    assert live_session(pool, user_id) is built[0]
    assert built[0].subscribed == {"2330"}
    accounts.mark_login_ok.assert_called_once()


def test_bound_row_without_a_session_is_logged_in() -> None:
    """`activate` failed after the bind committed: row says active, pool has nothing."""
    pool, built = _pool(provider_for=ReconnectableFake)
    user_id = uuid4()
    loop, _ = _loop(pool, accounts_rows=[_account(user_id, status="active")])

    loop.run_once()

    assert live_session(pool, user_id) is built[0]


@pytest.mark.parametrize("code", ["login_rejected", "cert_invalid", "credentials_unreadable"])
def test_binding_whose_last_failure_needs_a_human_is_not_retried(code: str) -> None:
    """Wrong password, bad cert, rotated key: retrying every 30 s cannot succeed and
    repeated refused logins risk the broker locking the account. The admin re-binds."""
    pool, built = _pool(provider_for=ReconnectableFake)
    row = _account(uuid4(), status="login_failed", last_error=LOGIN_FAILURE_MESSAGES[code])  # type: ignore[index]
    loop, accounts = _loop(pool, accounts_rows=[row])

    loop.run_once()

    assert built == []
    accounts.get_credentials.assert_not_called()


def test_dead_login_is_not_retried_once_the_broker_rejected_the_credentials() -> None:
    pool, built, user_id = _live()
    built[0].login_alive = False  # type: ignore[attr-defined]
    row = _account(user_id, status="login_failed", last_error=LOGIN_FAILURE_MESSAGES["login_rejected"])
    loop, accounts = _loop(pool, accounts_rows=[row])

    loop.run_once()

    assert len(built) == 1
    accounts.get_credentials.assert_not_called()


def test_run_forever_ticks_then_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    pool, _, _ = _live()
    loop, _ = _loop(pool)
    calls: list[str] = []
    loop.run_once = lambda: calls.append("tick")  # type: ignore[method-assign]

    async def fake_sleep(seconds: float) -> None:
        calls.append(f"sleep {seconds}")
        raise asyncio.CancelledError

    monkeypatch.setattr("app.services.broker_session_reconnect.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run_forever())

    assert calls == ["tick", "sleep 30"]
