from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


def test_settings_parses_local_user_id_as_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    get_settings.cache_clear()

    settings = get_settings()

    assert isinstance(settings.local_user_id, UUID)
    assert settings.local_user_id == UUID("11111111-2222-3333-4444-555555555555")


def test_missing_local_user_id_with_local_mode_true_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_USER_ID", raising=False)
    monkeypatch.setenv("LOCAL_MODE", "true")

    with pytest.raises(ValidationError) as exc_info:
        Settings()

    assert "LOCAL_USER_ID is required when LOCAL_MODE is true" in str(exc_info.value)


def test_missing_local_user_id_with_local_mode_false_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_USER_ID", raising=False)
    monkeypatch.setenv("LOCAL_MODE", "false")

    settings = Settings()

    assert settings.local_mode is False
    assert settings.local_user_id is None


def test_malformed_local_user_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "not-a-uuid")
    monkeypatch.setenv("LOCAL_MODE", "true")

    with pytest.raises(ValidationError):
        Settings()
