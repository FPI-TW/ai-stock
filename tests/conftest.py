import os

# Module-level defaults — `app.main` 在 import 時建 FastAPI instance，會觸發 Settings 驗證；
# 這裡 setdefault 保證 import 不爆，每個 test 仍由 `clear_settings_cache` autouse fixture
# 重新覆蓋 env，需要負面 case 的 test 再透過自己的 monkeypatch.delenv 移除。
os.environ.setdefault("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
os.environ.setdefault("LOCAL_MODE", "true")

from collections.abc import Generator  # noqa: E402
from typing import Protocol  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_database_health_checker  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402


class ClientFactory(Protocol):
    def __call__(self, database_available: bool = True) -> TestClient: ...


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    for env_name in ("APP_ENV", "APP_NAME", "APP_VERSION", "LOCAL_USER_ID", "LOCAL_MODE", "REQUEST_ID_HEADER"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LOCAL_MODE", "true")
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client_factory() -> ClientFactory:
    def make_client(database_available: bool = True) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_database_health_checker] = lambda: lambda: database_available
        return TestClient(app)

    return make_client
