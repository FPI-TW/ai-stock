from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_intent_repository, get_quote_provider, get_symbol_service
from app.main import create_app
from app.services.quote.in_memory import InMemoryQuoteProvider


@pytest.fixture
def mock_symbol_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_intent_repository() -> MagicMock:
    return MagicMock()


@pytest.fixture
def in_memory_quote_provider() -> InMemoryQuoteProvider:
    return InMemoryQuoteProvider()


@pytest.fixture
def client(
    mock_symbol_service: MagicMock,
    mock_intent_repository: MagicMock,
    in_memory_quote_provider: InMemoryQuoteProvider,
) -> Generator[TestClient]:
    """Test client with mock SymbolService + IntentRepository.

    The real `CreateTradeIntentCommand` still runs; we supply a real
    `InMemoryQuoteProvider` so reconcile is exercised, and a `MagicMock` Session so
    `commit` / `rollback` calls in the command are no-ops.
    """

    app = create_app()
    app.dependency_overrides[get_symbol_service] = lambda: mock_symbol_service
    app.dependency_overrides[get_intent_repository] = lambda: mock_intent_repository
    app.dependency_overrides[get_quote_provider] = lambda: in_memory_quote_provider
    app.dependency_overrides[get_db] = lambda: MagicMock()
    yield TestClient(app)
    app.dependency_overrides.clear()
