from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from app.core.security import RequestUser, get_owner_user_id


def test_get_owner_user_id_returns_user_id() -> None:
    user_id = uuid4()
    user = RequestUser(user_id=user_id, role="user")

    assert get_owner_user_id(user) == user_id


def test_request_user_is_frozen() -> None:
    user = RequestUser(user_id=uuid4(), role="user")

    with pytest.raises(FrozenInstanceError):
        user.user_id = uuid4()  # type: ignore[misc]


def test_request_user_defaults_session_and_mfa() -> None:
    # session_id / mfa_verified default so test overrides can pass just id + role.
    user = RequestUser(user_id=uuid4(), role="admin")

    assert user.session_id is not None
    assert user.mfa_verified is False
