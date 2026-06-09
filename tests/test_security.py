from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from app.core.config import Settings
from app.core.security import RequestUser, build_local_user, get_owner_user_id


def test_build_local_user_returns_request_user(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_uuid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.setenv("LOCAL_USER_ID", str(expected_uuid))
    monkeypatch.setenv("LOCAL_MODE", "true")

    user = build_local_user(Settings())

    assert isinstance(user, RequestUser)
    assert user.user_id == expected_uuid
    assert user.role == "local"


def test_get_owner_user_id_returns_user_id() -> None:
    user_id = uuid4()
    user = RequestUser(user_id=user_id, role="local")

    assert get_owner_user_id(user) == user_id


def test_request_user_is_frozen() -> None:
    user = RequestUser(user_id=uuid4(), role="local")

    with pytest.raises(FrozenInstanceError):
        user.user_id = uuid4()  # type: ignore[misc]


def test_build_local_user_raises_when_local_user_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_USER_ID", raising=False)
    monkeypatch.setenv("LOCAL_MODE", "false")
    monkeypatch.setenv("JWT_ACCESS_SECRET", "prod-secret")  # production secret so Settings validates
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError, match="LOCAL_USER_ID missing"):
        build_local_user(settings)
