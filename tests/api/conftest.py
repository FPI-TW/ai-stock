from collections.abc import Generator
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    enforce_mutation_rate_limit,
    get_active_user,
    get_core_intent_limits,
    get_current_user,
    get_db,
    get_idempotency_key,
    get_idempotency_manager,
    get_intent_repository,
    get_kill_switch_provider,
    get_quote_provider,
    get_symbol_service,
    get_trade_intent_core_repository,
)
from app.core.security import RequestUser
from app.main import create_app
from app.services.quote.in_memory import InMemoryQuoteProvider

# Matches LOCAL_USER_ID seeded by conftest; owner-scoped api tests authenticate as this user.
TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class PassthroughIdempotency:
    """Test double for api tests (get_db is mocked, so the real DB-backed manager
    can't run): just executes the wrapped action with no dedup."""

    def run(self, *, execute: object, **_: object) -> object:
        return execute()  # type: ignore[operator]


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
    # T1 cutover：create/list/get/cancel 已改吃新軌 repo；舊軌 dep 仍留給 TWAP endpoint。
    # 同一顆 mock 餵兩軌，既有斷言不必分辨呼叫落在哪個 repo。
    app.dependency_overrides[get_trade_intent_core_repository] = lambda: mock_intent_repository
    app.dependency_overrides[get_quote_provider] = lambda: in_memory_quote_provider
    app.dependency_overrides[get_db] = lambda: MagicMock()
    # The kill-switch provider opens its own real session (via get_session_factory),
    # which would bypass the mocked get_db above and hit a real DB. These api tests
    # don't exercise the kill switch, so treat it as absent ("not halted").
    app.dependency_overrides[get_kill_switch_provider] = lambda: None
    # The mocked intent repo returns MagicMocks from the §15 count queries; disable
    # limit enforcement so `count >= limit` doesn't blow up. Limits are covered by
    # dedicated unit + integration tests.
    app.dependency_overrides[get_core_intent_limits] = lambda: None
    # The §13 mutation rate limit runs real bucket SQL; with get_db mocked it has no
    # real session, so disable it here. Rate limiting is covered by an integration test.
    app.dependency_overrides[enforce_mutation_rate_limit] = lambda: None
    # create/cancel now require an Idempotency-Key + DB-backed manager (§16). These
    # tests don't exercise idempotency, so supply a fixed key and a passthrough manager.
    app.dependency_overrides[get_idempotency_key] = lambda: "test-idempotency-key"
    app.dependency_overrides[get_idempotency_manager] = lambda: PassthroughIdempotency()
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=TEST_USER_ID, role="user")
    # get_active_user does a real DB status lookup; mirror the current-user override so
    # the MagicMock session above isn't queried (write endpoints use ActiveUserDep).
    app.dependency_overrides[get_active_user] = lambda: RequestUser(user_id=TEST_USER_ID, role="user")
    yield TestClient(app)
    app.dependency_overrides.clear()
