from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_intent_repository, get_symbol_service
from app.main import create_app


@pytest.fixture
def mock_symbol_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_intent_repository() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(mock_symbol_service: MagicMock, mock_intent_repository: MagicMock) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_symbol_service] = lambda: mock_symbol_service
    app.dependency_overrides[get_intent_repository] = lambda: mock_intent_repository
    yield TestClient(app)
    app.dependency_overrides.clear()
