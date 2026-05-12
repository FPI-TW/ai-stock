from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_database_health_checker
from app.core.config import get_settings
from app.db.session import get_engine
from app.main import create_app


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    for env_name in ("APP_ENV", "APP_NAME", "APP_VERSION", "LOCAL_USER_ID", "LOCAL_MODE", "REQUEST_ID_HEADER"):
        monkeypatch.delenv(env_name, raising=False)
    get_settings.cache_clear()
    get_engine.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()


@pytest.fixture
def client_factory() -> Callable[[bool], TestClient]:
    def make_client(database_available: bool = True) -> TestClient:
        app = create_app()
        app.dependency_overrides[get_database_health_checker] = lambda: lambda: database_available
        return TestClient(app)

    return make_client
