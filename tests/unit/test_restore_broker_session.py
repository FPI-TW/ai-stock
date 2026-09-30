"""`restore_broker_session`: the one login flow shared by the lifespan, account
reactivation and the reconnect loop (docs/architecture.md「per-user 模式的生命週期」).

prepare -> subscribe the owner's open symbols -> mark_login_ok + commit -> activate.
Any failure before commit discards the candidate and records a safe failure code;
the caller decides whether to continue (lifespan / loop) or to swallow (reactivate).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from tests.unit.test_broker_session_pool import _CREDS, FakeProvider, _login_error, _pool, live_session, token_free

from app.commands.broker_account import restore_broker_session
from app.domain.broker_account import (
    BrokerBindInProgressError,
    BrokerLoginFailedError,
    BrokerSessionLimitReachedError,
)
from app.services.quote.base import QuoteProviderUnavailableError

NOW = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)


def _repos(symbols: set[str] | None = None) -> tuple[MagicMock, MagicMock, MagicMock]:
    db, accounts, core_intents = MagicMock(), MagicMock(), MagicMock()
    core_intents.active_or_scheduled_symbols_by_owner.return_value = symbols or set()
    return db, accounts, core_intents


def test_restore_logs_in_subscribes_marks_ok_then_activates() -> None:
    pool, built = _pool()
    db, accounts, core_intents = _repos({"2330", "2317"})
    user_id = uuid4()

    replaced = restore_broker_session(
        db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
    )

    assert replaced is None
    assert live_session(pool, user_id) is built[0]
    assert built[0].subscribed == {"2330", "2317"}
    accounts.mark_login_ok.assert_called_once_with(user_id, now=NOW)
    db.commit.assert_called_once()
    accounts.mark_login_failed.assert_not_called()


def test_restore_returns_the_replaced_session_for_the_caller_to_retire() -> None:
    """Reconnect after a lost login: the dead session stays live until the new one
    is committed and swapped in, then the caller logs it out outside any lock."""
    pool, built = _pool()
    db, accounts, core_intents = _repos()
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))

    replaced = restore_broker_session(
        db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
    )

    assert replaced is built[0]
    assert not built[0].stopped
    assert live_session(pool, user_id) is built[1]


@pytest.mark.parametrize(
    ("fail_with", "expected_code"),
    [
        (_login_error("login_rejected"), "login_rejected"),
        (AttributeError("sdk drift A123456789"), "unknown"),
    ],
)
def test_login_failure_is_recorded_and_raised(fail_with: Exception, expected_code: str) -> None:
    pool, _ = _pool(fail_with=fail_with)
    db, accounts, core_intents = _repos()
    user_id = uuid4()

    with pytest.raises(BrokerLoginFailedError) as info:
        restore_broker_session(
            db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
        )

    assert info.value.code == expected_code
    accounts.mark_login_failed.assert_called_once_with(user_id, expected_code, now=NOW)
    db.commit.assert_called_once()
    accounts.mark_login_ok.assert_not_called()
    assert live_session(pool, user_id) is None
    assert token_free(pool, user_id)


def test_pool_capacity_is_recorded_as_session_limit() -> None:
    pool, _ = _pool(max_sessions=1)
    pool.activate(pool.prepare(uuid4(), _CREDS))
    db, accounts, core_intents = _repos()
    user_id = uuid4()

    with pytest.raises(BrokerSessionLimitReachedError):
        restore_broker_session(
            db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
        )

    accounts.mark_login_failed.assert_called_once_with(user_id, "session_limit", now=NOW)


def test_subscribe_failure_discards_the_candidate_and_keeps_the_old_session() -> None:
    pool, built = _pool(subscribe_fail_with=QuoteProviderUnavailableError("fubon", "realtime_disconnected"))
    db, accounts, core_intents = _repos({"2330"})
    user_id = uuid4()
    built_old = FakeProvider()
    pool._sessions[user_id] = built_old  # an existing live session the failed restore must not touch

    with pytest.raises(QuoteProviderUnavailableError):
        restore_broker_session(
            db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
        )

    assert built[0].stopped  # candidate logged out
    assert live_session(pool, user_id) is built_old
    db.rollback.assert_called_once()
    accounts.mark_login_failed.assert_called_once_with(user_id, "provider_unavailable", now=NOW)
    accounts.mark_login_ok.assert_not_called()
    assert token_free(pool, user_id)


def test_commit_failure_discards_the_candidate_and_records_unknown() -> None:
    pool, built = _pool()
    db, accounts, core_intents = _repos()
    db.commit.side_effect = [RuntimeError("db down"), None]  # second commit records the failure
    user_id = uuid4()

    with pytest.raises(RuntimeError, match="db down"):
        restore_broker_session(
            db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
        )

    assert built[0].stopped
    assert live_session(pool, user_id) is None
    accounts.mark_login_failed.assert_called_once_with(user_id, "unknown", now=NOW)


def test_bind_in_progress_is_not_a_login_failure() -> None:
    """An admin is re-binding this user right now: leave the row alone, try later."""
    pool, _ = _pool()
    db, accounts, core_intents = _repos()
    user_id = uuid4()
    claim = pool.claim(user_id)

    with pytest.raises(BrokerBindInProgressError):
        restore_broker_session(
            db=db, accounts=accounts, core_intents=core_intents, pool=pool, user_id=user_id, credentials=_CREDS, now=NOW
        )

    accounts.mark_login_failed.assert_not_called()
    db.commit.assert_not_called()
    pool.release(claim)
