"""Factory smoke tests — ensures the lazy import contract holds.

`shioaji_demo` must not be imported unless `QUOTE_PROVIDER=shioaji_demo`, so
provider-specific import side effects stay out of in-memory test/runtime paths.
"""

import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.quote.factory import build_quote_provider
from app.services.quote.in_memory import InMemoryQuoteProvider


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build a fresh `Settings` by writing env vars then constructing without .env file."""

    monkeypatch.setenv("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("QUOTE_PROVIDER", "in_memory")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_factory_returns_in_memory_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = build_quote_provider(_settings(monkeypatch))
    assert isinstance(provider, InMemoryQuoteProvider)


def test_factory_does_not_import_shioaji_demo_for_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lazy import guard: choosing in_memory must never load the demo subpackage."""

    sys.modules.pop("app.services.quote.shioaji_demo", None)
    sys.modules.pop("app.services.quote.shioaji_demo.provider", None)
    sys.modules.pop("app.services.quote.shioaji_demo.client", None)

    build_quote_provider(_settings(monkeypatch))

    assert "app.services.quote.shioaji_demo" not in sys.modules
    assert "app.services.quote.shioaji_demo.provider" not in sys.modules


def test_settings_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(monkeypatch, QUOTE_PROVIDER="bogus_provider")
    # pydantic Literal lists the allowed values in its error — that's the
    # actionable bit the work order asks for ("明示已知值清單").
    msg = str(exc_info.value)
    assert "shioaji_demo" in msg
    assert "in_memory" in msg


def test_settings_requires_shioaji_credentials_when_provider_is_shioaji_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("QUOTE_PROVIDER", "shioaji_demo")
    monkeypatch.delenv("SHIOAJI_API_KEY", raising=False)
    monkeypatch.delenv("SHIOAJI_SECRET_KEY", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]
    msg = str(exc_info.value)
    assert "SHIOAJI_API_KEY" in msg
    assert "SHIOAJI_SECRET_KEY" in msg


def test_in_memory_provider_does_not_read_shioaji_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Choosing in_memory must not require SHIOAJI_* env to be set."""

    monkeypatch.delenv("SHIOAJI_API_KEY", raising=False)
    monkeypatch.delenv("SHIOAJI_SECRET_KEY", raising=False)

    settings = _settings(monkeypatch)
    provider = build_quote_provider(settings)

    assert isinstance(provider, InMemoryQuoteProvider)
