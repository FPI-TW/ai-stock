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
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "LOCAL_USER_ID is required when LOCAL_MODE is true" in str(exc_info.value)


def test_missing_local_user_id_with_local_mode_false_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCAL_USER_ID", raising=False)
    monkeypatch.setenv("LOCAL_MODE", "false")
    monkeypatch.setenv("JWT_ACCESS_SECRET", "prod-secret")  # production requires its own secret
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", "prod-mfa-key")  # ...and an MFA key

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.local_mode is False
    assert settings.local_user_id is None


def test_missing_jwt_secret_with_local_mode_false_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "false")
    monkeypatch.delenv("JWT_ACCESS_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "JWT_ACCESS_SECRET is required when LOCAL_MODE is false" in str(exc_info.value)


def test_jwt_secret_falls_back_to_dev_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.delenv("JWT_ACCESS_SECRET", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.jwt_access_secret is None
    assert settings.resolved_jwt_access_secret  # dev fallback is non-empty
    assert settings.cookie_secure is False  # LOCAL_MODE relaxes the Secure flag


def test_malformed_local_user_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "not-a-uuid")
    monkeypatch.setenv("LOCAL_MODE", "true")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_telegram_settings_are_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.telegram_bot_token is None
    assert settings.telegram_chat_id is None
    assert settings.telegram_timeout_seconds == 5.0


def test_telegram_settings_parse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setenv("TELEGRAM_TIMEOUT_SECONDS", "2.5")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.telegram_bot_token == "bot-token"
    assert settings.telegram_chat_id == "chat-1"
    assert settings.telegram_timeout_seconds == 2.5


def test_trusted_proxy_ips_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.trusted_proxy_networks == []


def test_trusted_proxy_ips_parses_mixed_ip_and_cidr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.18.0.0/16, 10.0.0.5")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    networks = [str(net) for net in settings.trusted_proxy_networks]
    assert networks == ["172.18.0.0/16", "10.0.0.5/32"]


def test_malformed_trusted_proxy_ips_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "not-an-ip")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_twap_worker_settings_parse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("TWAP_WORKER_ENABLED", "false")
    monkeypatch.setenv("TWAP_WORKER_INTERVAL_SECONDS", "3.5")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.twap_worker_enabled is False
    assert settings.twap_worker_interval_seconds == 3.5


def test_missing_mfa_key_with_local_mode_false_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "false")
    monkeypatch.setenv("JWT_ACCESS_SECRET", "prod-secret")
    monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "MFA_ENCRYPTION_KEY is required when LOCAL_MODE is false" in str(exc_info.value)


def test_cors_allow_origins_list_parses_and_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", " https://a.example.com , https://b.example.com ,")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_allow_origins_list == ["https://a.example.com", "https://b.example.com"]
