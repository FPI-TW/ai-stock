import os

# Module-level defaults — `app.main` 在 import 時建 FastAPI instance，會觸發 Settings 驗證
# 與 `build_quote_provider(settings)`，所以這些 env 必須在 import app 之前就位。
# `clear_settings_cache` autouse fixture 會在每個 test 之間重新覆蓋 env；需要負面 case
# （例如測 QUOTE_PROVIDER 未知值）的 test 可自行透過 monkeypatch.setenv 改寫。
os.environ.setdefault("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("LOCAL_MODE", "true")
os.environ.setdefault("QUOTE_PROVIDER", "in_memory")

from collections.abc import Generator  # noqa: E402
from typing import (
    Protocol,  # noqa: E402
)

import pytest  # noqa: E402
from alembic import command as _alembic_command  # noqa: E402
from alembic.config import Config as _AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine as _sa_create_engine  # noqa: E402
from sqlalchemy import text as _sa_text  # noqa: E402

from app.api.deps import get_database_health_checker  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402

# --- Integration-test DB reset hardening ---------------------------------------
# Integration modules reset via `command.downgrade(config, "base")`. The production
# migrations carry downgrade guards (refuse to drop while expired / market / account-
# disabled status rows exist), so a prior module's leftover rows make the next
# module's reset blow up. Wrap downgrade-to-base to TRUNCATE the data tables first;
# this is test-only and never touches the real migration behaviour. Downgrades to a
# specific revision (e.g. the migration tests asserting a guard fires) are untouched.
_real_alembic_downgrade = _alembic_command.downgrade


def _truncate_then_downgrade(config: _AlembicConfig, revision: str, sql: bool = False, tag: str | None = None) -> None:
    if revision == "base":
        url = config.get_main_option("sqlalchemy.url")
        if url:
            engine = _sa_create_engine(url)
            try:
                with engine.begin() as conn:
                    conn.execute(_sa_text("TRUNCATE trade_intents, twap_slices, trigger_events, notifications CASCADE"))
            except Exception:
                pass  # tables may not exist yet (DB already at base)
            finally:
                engine.dispose()
    _real_alembic_downgrade(config, revision, sql=sql, tag=tag)


_alembic_command.downgrade = _truncate_then_downgrade


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
