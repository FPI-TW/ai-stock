"""Integration test for the `ActiveUserDep` status gate (deps.get_active_user).

Access tokens are stateless JWTs valid until TTL, so logout/disable cannot revoke an
already-issued token. State-changing endpoints therefore re-check the account status
in the DB via `ActiveUserDep`. This verifies that gate directly against a real DB,
using a throwaway route so the assertion doesn't depend on the create-intent stack.
"""

from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.api.deps import ActiveUserDep, get_current_user
from app.core.config import get_settings
from app.core.security import RequestUser
from app.main import create_app
from tests.db_helpers import ensure_user


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    eng = create_engine(get_settings().database_url or "")
    try:
        yield eng
    finally:
        eng.dispose()
        command.downgrade(config, "base")


def _attach_active_only_route(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/__test/active-only")
    def active_only(user: ActiveUserDep) -> dict[str, str]:
        return {"userId": str(user.user_id)}

    app.include_router(router)


def _client_as(app: FastAPI, user_id: object) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=user_id, role="user")  # type: ignore[arg-type]
    return TestClient(app)


@pytest.mark.integration
def test_active_user_passes_disabled_and_unknown_are_blocked(engine: Engine) -> None:
    active_id = uuid4()
    disabled_id = uuid4()
    with Session(engine) as session:
        ensure_user(session, active_id, status="active")
        ensure_user(session, disabled_id, status="disabled")
        session.commit()

    app = create_app()
    _attach_active_only_route(app)

    # Active account passes the gate.
    response = _client_as(app, active_id).get("/__test/active-only")
    assert response.status_code == 200
    assert response.json() == {"userId": str(active_id)}

    # Disabled account is blocked even with an otherwise-valid principal (the stateless
    # token would still decode; the DB status re-check is what stops it).
    response = _client_as(app, disabled_id).get("/__test/active-only")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"

    # An unknown user id (e.g. deleted) is treated the same as disabled.
    response = _client_as(app, uuid4()).get("/__test/active-only")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"

    app.dependency_overrides.clear()
