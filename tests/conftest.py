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

from app.api.deps import get_database_health_checker  # noqa: E402
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
        "TWAP_WORKER_ENABLED",
        "TWAP_WORKER_INTERVAL_SECONDS",
        "JWT_ACCESS_SECRET",
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
    monkeypatch.setenv("TWAP_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()


@pytest.fixture
def client_factory() -> ClientFactory:
    def make_client(database_available: bool = True) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_database_health_checker] = lambda: lambda: database_available
        return TestClient(app)

    return make_client
