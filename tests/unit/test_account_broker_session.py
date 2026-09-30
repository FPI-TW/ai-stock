"""Disable / reactivate against the per-user broker session pool
(docs/architecture.md「per-user 模式的生命週期」): disable logs the session out and
keeps the row; reactivate logs back in from the stored credentials and never
fails the reactivation itself when the broker says no."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from tests.unit.test_broker_session_pool import _CREDS, _login_error, _pool, live_session, token_free

from app.commands.account import DisableUserCommand, DisableUserInput, ReactivateUserCommand, ReactivateUserInput
from app.core.config import get_settings
from app.domain.broker_account import BrokerBindInProgressError, BrokerCredentialKeyError
from app.services.broker_session_pool import BrokerSessionPool

NOW = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)


def _users(user_id: object, status: str) -> MagicMock:
    users = MagicMock()
    users.get_by_id.return_value = MagicMock(id=user_id, status=status)
    return users


def _disable(pool: BrokerSessionPool, users: MagicMock, db: MagicMock | None = None) -> DisableUserCommand:
    intents, core_intents, refresh = MagicMock(), MagicMock(), MagicMock()
    intents.cancel_active_for_owner.return_value = 0
    core_intents.cancel_active_for_owner.return_value = 0
    refresh.revoke_all_active_for_user.return_value = 0
    return DisableUserCommand(db or MagicMock(), users, intents, core_intents, refresh, MagicMock(), pool)


def _reactivate(
    pool: BrokerSessionPool, users: MagicMock, accounts: MagicMock, db: MagicMock | None = None
) -> ReactivateUserCommand:
    core_intents = MagicMock()
    core_intents.active_or_scheduled_symbols_by_owner.return_value = {"2330"}
    return ReactivateUserCommand(db or MagicMock(), users, MagicMock(), accounts, core_intents, pool)


def test_disable_logs_the_session_out_and_leaves_the_binding_row() -> None:
    pool, built = _pool()
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    users = _users(user_id, "active")
    db = MagicMock()

    _disable(pool, users, db).execute(DisableUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW))

    assert built[0].stopped
    assert live_session(pool, user_id) is None
    assert token_free(pool, user_id)
    db.commit.assert_called_once()
    users.disable.assert_called_once()


def test_disable_is_refused_while_a_bind_for_the_user_is_in_flight() -> None:
    pool, _ = _pool()
    user_id = uuid4()
    candidate = pool.prepare(user_id, _CREDS)
    users = _users(user_id, "active")

    with pytest.raises(BrokerBindInProgressError):
        _disable(pool, users).execute(DisableUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW))

    users.disable.assert_not_called()
    pool.discard(candidate)


def test_disable_db_failure_keeps_the_session_and_releases_the_token() -> None:
    pool, built = _pool()
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    db = MagicMock()
    db.commit.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        _disable(pool, _users(user_id, "active"), db).execute(
            DisableUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW)
        )

    assert not built[0].stopped
    assert live_session(pool, user_id) is built[0]
    assert token_free(pool, user_id)
    db.rollback.assert_called_once()


def test_reactivate_logs_the_user_back_in_from_stored_credentials() -> None:
    pool, built = _pool()
    user_id = uuid4()
    accounts = MagicMock()
    accounts.get_credentials.return_value = _CREDS

    _reactivate(pool, _users(user_id, "disabled"), accounts).execute(
        ReactivateUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW)
    )

    assert live_session(pool, user_id) is built[0]
    assert built[0].subscribed == {"2330"}
    accounts.mark_login_ok.assert_called_once_with(user_id, now=NOW)


def test_reactivate_succeeds_even_when_the_broker_login_fails() -> None:
    pool, built = _pool(fail_with=_login_error("login_rejected"))
    user_id = uuid4()
    accounts = MagicMock()
    accounts.get_credentials.return_value = _CREDS
    users = _users(user_id, "disabled")

    _reactivate(pool, users, accounts).execute(
        ReactivateUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW)
    )

    users.reactivate.assert_called_once()
    assert live_session(pool, user_id) is None
    accounts.mark_login_failed.assert_called_once_with(user_id, "login_rejected", now=NOW)


def test_reactivate_with_unreadable_credentials_marks_the_row_and_still_succeeds() -> None:
    """MFA_ENCRYPTION_KEY was rotated: the admin has to re-bind, the account is back."""
    pool, _ = _pool()
    user_id = uuid4()
    accounts = MagicMock()
    accounts.get_credentials.side_effect = BrokerCredentialKeyError("undecryptable")

    _reactivate(pool, _users(user_id, "disabled"), accounts).execute(
        ReactivateUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW)
    )

    accounts.mark_login_failed.assert_called_once_with(user_id, "credentials_unreadable", now=NOW)
    assert live_session(pool, user_id) is None


def test_reactivate_without_a_binding_or_in_shared_mode_touches_no_broker() -> None:
    user_id = uuid4()
    accounts = MagicMock()
    accounts.get_credentials.return_value = None
    pool, _ = _pool()
    _reactivate(pool, _users(user_id, "disabled"), accounts).execute(
        ReactivateUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW)
    )
    accounts.mark_login_ok.assert_not_called()
    accounts.mark_login_failed.assert_not_called()

    shared_accounts = MagicMock()
    shared_pool = BrokerSessionPool(get_settings(), shared=MagicMock())
    _reactivate(shared_pool, _users(user_id, "disabled"), shared_accounts).execute(
        ReactivateUserInput(target_user_id=user_id, actor_admin_id=uuid4(), now=NOW)
    )
    shared_accounts.get_credentials.assert_not_called()
