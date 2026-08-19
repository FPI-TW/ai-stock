import os

# Module-level defaults — `app.main` 在 import 時建 FastAPI instance，會觸發 Settings 驗證
# 與 `build_quote_provider(settings)`，所以這些 env 必須在 import app 之前就位。
# `clear_settings_cache` autouse fixture 會在每個 test 之間重新覆蓋 env；需要負面 case
# （例如測 QUOTE_PROVIDER 未知值）的 test 可自行透過 monkeypatch.setenv 改寫。
os.environ.setdefault("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("LOCAL_MODE", "true")
os.environ.setdefault("QUOTE_PROVIDER", "in_memory")

from collections.abc import Generator  # noqa: E402
from typing import Protocol  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_database_health_checker, get_mailer  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402


class ClientFactory(Protocol):
    def __call__(self, database_available: bool = True) -> TestClient: ...


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    for env_name in (
        "APP_ENV",
        "APP_NAME",
        "APP_VERSION",
        "LOCAL_USER_ID",
        "LOCAL_MODE",
        "REQUEST_ID_HEADER",
        "CORS_ALLOW_ORIGINS",
        "QUOTE_PROVIDER",
        "SHIOAJI_API_KEY",
        "SHIOAJI_SECRET_KEY",
        "SHIOAJI_MAX_SUBSCRIPTIONS",
        "SHIOAJI_SIMULATION",
        "SHIOAJI_DEMO_ALLOWED_SYMBOLS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_TIMEOUT_SECONDS",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEGRAM_OWNER_EMAIL",
        "TELEGRAM_WEBHOOK_URL",
        "TELEGRAM_LLM_MODEL",
        "TELEGRAM_LLM_TIMEOUT_SECONDS",
        "DEEPSEEK_API_KEY",
        "TWAP_WORKER_ENABLED",
        "TWAP_WORKER_INTERVAL_SECONDS",
        "IDEMPOTENCY_CLEANUP_ENABLED",
        "JWT_ACCESS_SECRET",
        "MFA_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("QUOTE_PROVIDER", "in_memory")
    monkeypatch.setenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3001,http://127.0.0.1:3001,"
        "http://localhost:3100,http://127.0.0.1:3100",
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    # Override (rather than only delete) inbound values so BaseSettings cannot
    # fall back to a developer's ignored .env and accidentally use live bot/LLM
    # configuration during tests.
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    monkeypatch.setenv("TELEGRAM_OWNER_EMAIL", "admin@tingfong.com")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "")
    monkeypatch.setenv("TELEGRAM_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("TELEGRAM_LLM_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("TWAP_WORKER_ENABLED", "false")
    monkeypatch.setenv("IDEMPOTENCY_CLEANUP_ENABLED", "false")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_mailer.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    get_mailer.cache_clear()


@pytest.fixture
def client_factory() -> ClientFactory:
    def make_client(database_available: bool = True) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_database_health_checker] = lambda: lambda: database_available
        return TestClient(app)

    return make_client
