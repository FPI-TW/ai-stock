from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.symbol_deps import get_symbol_service
from app.symbol_main import create_symbol_app


@pytest.fixture
def mock_symbol_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(mock_symbol_service: MagicMock) -> Generator[TestClient]:
    app = create_symbol_app()
    app.dependency_overrides[get_symbol_service] = lambda: mock_symbol_service
    yield TestClient(app)
    app.dependency_overrides.clear()
