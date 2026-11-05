"""Factory smoke tests — ensures the lazy import contract holds.

`shioaji_demo` must not be imported unless `QUOTE_PROVIDER=shioaji_demo`, so
provider-specific import side effects stay out of in-memory test/runtime paths.
"""

import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.quote.factory import build_quote_provider
from app.services.quote.fubon.client import FubonCredentials
from app.services.quote.fubon.provider import FubonQuoteProvider
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
    assert "fubon" in msg


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


def _fubon_credentials() -> FubonCredentials:
    return FubonCredentials(personal_id="A123456789", password="pw", cert_pfx=b"pfx", cert_password="certpw")


def test_factory_builds_fubon_provider_from_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fubon is per-user: the factory needs that user's credentials, never `.env` secrets."""

    settings = _settings(monkeypatch, QUOTE_PROVIDER="fubon")

    provider = build_quote_provider(settings, credentials=_fubon_credentials())

    assert isinstance(provider, FubonQuoteProvider)


def test_factory_fubon_without_credentials_is_a_programming_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, QUOTE_PROVIDER="fubon")

    with pytest.raises(ValueError, match="credentials"):
        build_quote_provider(settings)


def test_factory_does_not_import_fubon_for_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(sys.modules):
        if name.startswith("app.services.quote.fubon"):
            sys.modules.pop(name)

    build_quote_provider(_settings(monkeypatch))

    assert not [name for name in sys.modules if name.startswith("app.services.quote.fubon")]


def test_fubon_credentials_repr_masks_secrets() -> None:
    text = repr(_fubon_credentials())

    assert "A123456789" not in text
    assert "pw" not in text
    assert "certpw" not in text
