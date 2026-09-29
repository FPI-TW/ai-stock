"""Transaction boundary of PUT /admin/users/{user_id}/broker-account.

Three destructive cases against real PostgreSQL and a recording fake broker
session. "Old session keeps watching" is asserted for real: a frame pushed
through the old provider still reaches the pool's owner-scoped listener after
the failed re-bind. Async cases use the anyio pytest plugin (already installed
via httpx / starlette); the sync routes run in the threadpool, so two requests
issued with `asyncio.gather` really do execute in parallel.
"""

# ruff: noqa: F811 - `engine` is the module-scoped fixture imported below; pytest injects it by parameter name.
import asyncio
import threading
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.db.models.auth import AuditEvent
from app.db.models.broker_account import BrokerAccount
from app.services.quote.base import QuoteSnapshot
from tests.test_broker_account_api_integration import (
    _bind_body,
    _Harness,
    _seed_core_intent,
    _seed_user,
    credential_key,  # noqa: F401 - autouse fixture: the bind path refuses the dev fallback key
    engine,  # noqa: F401 - module-scoped fixture re-exported for this file
)
from tests.unit.test_broker_session_pool import FakeProvider, _login_error

pytestmark = pytest.mark.integration


def _frame(symbol: str = "2330", last: str = "568") -> QuoteSnapshot:
    now = datetime.now(tz=UTC)
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=Decimal("567"),
        ask_price=Decimal("568"),
        last_price=Decimal(last),
        quote_time=now,
        last_trade_time=now,
        received_at=now,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _row_state(engine: Engine, user_id: UUID) -> tuple[object, ...]:
    with Session(engine) as session:
        row = session.execute(select(BrokerAccount).where(BrokerAccount.user_id == user_id)).scalar_one()
        return (
            row.id,
            row.status,
            row.broker_account_no,
            row.credentials_encrypted,
            row.cert_expires_at,
            row.last_login_at,
            row.last_error,
            row.updated_at,
        )


def _audit_count(engine: Engine) -> int:
    with Session(engine) as session:
        return int(session.execute(select(func.count()).select_from(AuditEvent)).scalar_one())


class _Watch:
    """Bound before the first activate: records (symbol, owner) per delivered frame."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, UUID | None]] = []

    def __call__(self, snapshot: QuoteSnapshot, *, owner_user_id: UUID | None) -> None:
        self.frames.append((snapshot.symbol, owner_user_id))


def _bound_with_watch(engine: Engine, harness: _Harness) -> tuple[UUID, FakeProvider, _Watch]:
    user_id = _seed_user(engine)
    _seed_core_intent(engine, user_id)
    watch = _Watch()
    harness.pool.set_quote_listener(watch)
    assert harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200
    old = harness.built[0]
    old.listeners[0](_frame("2330"))  # prove the wiring before we try to break it
    assert watch.frames == [("2330", user_id)]
    return user_id, old, watch


# --- Case A: broker-side hard limit -------------------------------------------------


def test_case_a_broker_hard_limit_leaves_db_and_old_session_untouched(engine: Engine) -> None:
    """Our own slot accounting says there is room (re-binding needs no extra
    slot), but Fubon answers the candidate login with its connection cap. The
    422 must carry only the safe code; the row must be byte-identical; the old
    session must still be live, still subscribed, and still delivering frames."""
    harness = _Harness(engine, max_sessions=2)
    user_id, old, watch = _bound_with_watch(engine, harness)
    row_before = _row_state(engine, user_id)
    audits_before = _audit_count(engine)

    harness.fail_with = _login_error("session_limit")
    response = harness.admin_client().put(
        f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222")
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"] == {
        "code": "BROKER_LOGIN_FAILED",
        "message": "券商連線數已達上限，請稍後再試",
        "details": {"reason": "session_limit"},
        "requestId": response.json()["error"]["requestId"],
    }
    assert "SDK text" not in response.text
    # DB: nothing moved — no update, no audit row.
    assert _row_state(engine, user_id) == row_before
    assert _audit_count(engine) == audits_before
    # Pool: the old session is the live one, never stopped, subscriptions intact.
    assert harness.pool.get(user_id) is old
    assert not old.stopped
    assert old.subscribed == {"2330"}
    assert not harness.pool.is_binding(user_id)
    # The candidate never got past login, so there is nothing to have leaked.
    assert len(harness.built) == 2 and not harness.built[1].started
    # ...and the old session still watches the market for this owner.
    old.listeners[0](_frame("2330", last="570"))
    assert watch.frames == [("2330", user_id), ("2330", user_id)]


# --- Case B: candidate cannot subscribe the owner's symbols -----------------------------


def test_case_b_candidate_subscribe_failure_rolls_back_and_discards(engine: Engine) -> None:
    """Login succeeded, so the candidate holds a real broker session; then
    `candidate.provider.subscribe(symbol)` raises (socket gone, or the 300-symbol
    cap). Contract: rollback (row byte-identical, no audit), candidate logged
    out, old session untouched and still delivering, and the next bind still works."""
    harness = _Harness(engine)
    user_id, old, watch = _bound_with_watch(engine, harness)
    row_before = _row_state(engine, user_id)
    audits_before = _audit_count(engine)

    from app.services.quote.fubon.provider import FubonSubscriptionLimitExceeded

    harness.subscribe_fail_with = FubonSubscriptionLimitExceeded(symbol="2330", current=300, limit=300)
    response = harness.admin_client().put(
        f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222")
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED"
    assert _row_state(engine, user_id) == row_before
    assert _audit_count(engine) == audits_before
    candidate = harness.built[1]
    assert candidate.started and candidate.stopped  # logged in, then logged out by discard
    assert candidate.subscribed == set()
    assert harness.pool.get(user_id) is old
    assert not old.stopped and old.subscribed == {"2330"}
    old.listeners[0](_frame("2330"))
    assert watch.frames[-1] == ("2330", user_id)

    # The request-scoped transaction was rolled back cleanly: a healthy re-bind succeeds.
    harness.subscribe_fail_with = None
    ok = harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="C333"))
    assert ok.status_code == 200, ok.text
    assert harness.pool.get(user_id) is harness.built[2]
    assert old.stopped
    assert _audit_count(engine) == audits_before + 1


# --- Case C: two concurrent binds for one user ----------------------------------------


@pytest.mark.anyio
async def test_case_c_concurrent_prepare_for_one_user_second_gets_409(engine: Engine) -> None:
    """Two requests for the same user_id in flight at once. The first to reach
    `prepare` takes the token and blocks in the broker login; the other must
    come back 409 BROKER_BIND_IN_PROGRESS while the first is still logging in,
    and must not have built (let alone logged in) a provider of its own."""
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    gate = threading.Event()
    harness.startup_gate = gate
    harness.admin_client()  # installs the admin principal overrides on the app

    async with AsyncClient(transport=ASGITransport(app=harness.app), base_url="http://test") as client:
        first = asyncio.create_task(client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()))
        second = asyncio.create_task(
            client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))
        )
        try:
            # Whichever request won the token is now parked inside startup().
            for _ in range(100):
                if harness.built and harness.built[0].in_startup.is_set():
                    break
                await asyncio.sleep(0.05)
            assert harness.built and harness.built[0].in_startup.is_set()
            # The loser must finish *before* the gate opens, or it was not concurrent.
            done, _pending = await asyncio.wait({first, second}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            assert len(done) == 1
            loser = done.pop()
            assert loser.result().status_code == 409
            assert loser.result().json()["error"]["code"] == "BROKER_BIND_IN_PROGRESS"
            assert len(harness.built) == 1  # the loser never built a provider
            assert harness.pool.is_binding(user_id)
        finally:
            gate.set()
        winner = first if loser is second else second
        assert (await winner).status_code == 200

    assert harness.pool.get(user_id) is harness.built[0]
    assert not harness.pool.is_binding(user_id)
    with Session(engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(BrokerAccount).where(BrokerAccount.user_id == user_id)
            ).scalar_one()
            == 1
        )
