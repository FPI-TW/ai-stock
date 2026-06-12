"""Integration test for the `ActiveUserDep` session-revocation gate (deps.get_active_user).

Access tokens are stateless JWTs valid until TTL, so logout / password reset / account
disable cannot revoke an already-issued token on their own. Each of those flows revokes
the session's refresh token, so `ActiveUserDep` re-checks the session — looked up by the
access token's `session_id` — against the DB on every request. This verifies that gate
directly against a real DB, using a throwaway route so the assertion doesn't depend on
the create-intent stack.

Revoked-for-rotation (`rotated`) is benign — the successor token supersedes it — so a
rotated session still passes. Disabled accounts answer 403 ACCOUNT_DISABLED; every other
revocation (and a missing session) answers 401 SESSION_REVOKED.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

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
from app.db.models.auth import RefreshToken
from app.main import create_app
from tests.db_helpers import ensure_user

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


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


def _seed_session(engine: Engine, *, revoked_reason: str | None) -> tuple[UUID, UUID]:
    """Seed a user + one refresh token (the session). When `revoked_reason` is given the
    token is marked revoked. Returns (user_id, session_id)."""
    user_id = uuid4()
    token_id = uuid4()
    with Session(engine) as session:
        ensure_user(session, user_id, status="active")
        session.add(
            RefreshToken(
                id=token_id,
                user_id=user_id,
                token_hash=f"hash-{token_id}",
                expires_at=_NOW + timedelta(days=30),
                revoked_at=_NOW if revoked_reason is not None else None,
                revoked_reason=revoked_reason,
            )
        )
        session.commit()
    return user_id, token_id


def _attach_active_only_route(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/__test/active-only")
    def active_only(user: ActiveUserDep) -> dict[str, str]:
        return {"userId": str(user.user_id)}

    app.include_router(router)


def _client_for(app: FastAPI, user_id: UUID, session_id: UUID) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: RequestUser(
        user_id=user_id, role="user", session_id=session_id
    )
    return TestClient(app)


@pytest.mark.integration
def test_live_session_passes_revoked_session_is_blocked(engine: Engine) -> None:
    app = create_app()
    _attach_active_only_route(app)

    # A live session passes the gate.
    user_id, session_id = _seed_session(engine, revoked_reason=None)
    response = _client_for(app, user_id, session_id).get("/__test/active-only")
    assert response.status_code == 200
    assert response.json() == {"userId": str(user_id)}

    # A rotated session is benign (the successor token supersedes it) -> still passes.
    user_id, session_id = _seed_session(engine, revoked_reason="rotated")
    response = _client_for(app, user_id, session_id).get("/__test/active-only")
    assert response.status_code == 200

    # A disabled account answers 403 ACCOUNT_DISABLED (revoked_reason "account_disabled").
    user_id, session_id = _seed_session(engine, revoked_reason="account_disabled")
    response = _client_for(app, user_id, session_id).get("/__test/active-only")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"

    # Logout and password reset both end the session -> 401 SESSION_REVOKED.
    for reason in ("logout", "password_reset"):
        user_id, session_id = _seed_session(engine, revoked_reason=reason)
        response = _client_for(app, user_id, session_id).get("/__test/active-only")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "SESSION_REVOKED"

    # An unknown session id (e.g. never issued / pruned) -> 401 SESSION_REVOKED. The gate
    # queries refresh_tokens by session_id only, so no user row is needed here.
    response = _client_for(app, uuid4(), uuid4()).get("/__test/active-only")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SESSION_REVOKED"

    app.dependency_overrides.clear()
