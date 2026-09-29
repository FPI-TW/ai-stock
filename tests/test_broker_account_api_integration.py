"""HTTP integration tests for admin broker-account binding (PR2 of
docs/orders/per-user-broker-sessions.md). Against real PostgreSQL; the broker
session is a recording fake injected through a per-user `BrokerSessionPool`.
"""

import base64
import logging
import threading
from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_active_user, get_current_user
from app.core.config import get_settings
from app.core.security import RequestUser
from app.db.models.auth import AuditEvent, User
from app.db.models.broker_account import BrokerAccount
from app.db.models.core import Symbol, TradeIntent
from app.db.models.trade_intent_core import TradeIntentCore, TradeIntentPriceParams
from app.domain.broker_account import FubonCredentials
from app.main import create_app
from app.repositories.broker_account_repository import BrokerAccountRepository
from app.services.broker_session_pool import BrokerSessionPool, BrokerStopClaim
from app.services.quote.base import QuoteProviderUnavailableError
from tests.pfx_helpers import DEFAULT_NOT_AFTER, build_test_pfx
from tests.unit.test_broker_session_pool import FakeProvider, _SdkLoginError

CERT_PASSWORD = "cert-pw"
# The bind path refuses the repo's dev fallback key even in LOCAL_MODE (a real
# identity number + password would otherwise sit in the DB under a public key),
# so these tests run with a real key like a deployment would.
TEST_CREDENTIAL_KEY = Fernet.generate_key().decode()
PFX = build_test_pfx(password=CERT_PASSWORD)
# Sentinel that a leaky path would echo: looks like an identity number.
SECRET_SENTINEL = "Z987654321"


@pytest.fixture(autouse=True)
def credential_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", TEST_CREDENTIAL_KEY)
    get_settings.cache_clear()
    return TEST_CREDENTIAL_KEY


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url or "")
    return config


@pytest.fixture(scope="module")
def engine() -> Generator[Engine]:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(get_settings().database_url or "")
    with Session(engine) as session:
        session.add(
            Symbol(
                id=uuid4(),
                symbol="2330",
                display_name="台積電",
                market="TWSE",
                instrument_type="stock",
                tradable_status="tradable",
            )
        )
        session.commit()
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE trade_intents, trade_intent_core CASCADE"))
        engine.dispose()
        command.downgrade(config, "base")


def _seed_user(engine: Engine, *, role: str = "user", status: str = "active", mfa: bool = False) -> UUID:
    user_id = uuid4()
    with Session(engine) as session:
        session.add(User(id=user_id, email=f"{role}-{user_id}@example.com", role=role, status=status, mfa_enabled=mfa))
        session.commit()
    return user_id


def _seed_legacy_intent(engine: Engine, owner_id: UUID) -> UUID:
    """舊軌委託（TWAP 仍寫這張表）：解綁必須連這邊一起取消，否則 scheduler 照樣切片發通知。"""

    intent_id = uuid4()
    with Session(engine) as session:
        session.add(
            TradeIntent(
                id=intent_id,
                owner_user_id=owner_id,
                symbol="2330",
                strategy="buy_price_alert",
                execution_mode="notify_only",
                quantity_lots=1,
                target_price_original=Decimal("100.0000"),
                target_price_effective=Decimal("100.0000"),
                trigger_reference_price_type="ask",
                trading_date=date(2026, 1, 1),
                time_in_force="day",
                status="active",
            )
        )
        session.commit()
    return intent_id


def _seed_core_intent(engine: Engine, owner_id: UUID) -> UUID:
    intent_id = uuid4()
    with Session(engine) as session:
        session.add(
            TradeIntentCore(
                id=intent_id,
                owner_user_id=owner_id,
                symbol="2330",
                strategy="buy_price_alert",
                execution_mode="notify_only",
                quantity_lots=1,
                trigger_reference_price_type="ask",
                trading_date=date(2026, 1, 1),
                time_in_force="day",
                status="active",
                dedup_key=f"100.0000:{intent_id}",
            )
        )
        session.add(
            TradeIntentPriceParams(
                trade_intent_id=intent_id,
                target_price_original=Decimal("100.0000"),
                target_price_effective=Decimal("100.0000"),
            )
        )
        session.commit()
    return intent_id


class _Harness:
    """One app + one per-user pool whose provider factory we control per call."""

    def __init__(self, engine: Engine, *, max_sessions: int = 2) -> None:
        self.built: list[FakeProvider] = []
        self.fail_with: Exception | None = None
        self.subscribe_fail_with: Exception | None = None
        self.shutdown_fail_with: Exception | None = None
        self.startup_gate: threading.Event | None = None
        settings = get_settings().model_copy(update={"broker_max_sessions": max_sessions})
        self.pool = BrokerSessionPool(settings, shared=None, provider_factory=self._factory)
        self.app = create_app()
        self.app.state.broker_sessions = self.pool
        self.admin_id = _seed_user(engine, role="admin", mfa=True)

    def _factory(self, credentials: FubonCredentials) -> FakeProvider:
        provider = FakeProvider(
            fail_with=self.fail_with,
            subscribe_fail_with=self.subscribe_fail_with,
            shutdown_fail_with=self.shutdown_fail_with,
            startup_gate=self.startup_gate,
        )
        self.built.append(provider)
        return provider

    def client_as(self, principal: RequestUser) -> TestClient:
        self.app.dependency_overrides[get_current_user] = lambda: principal
        self.app.dependency_overrides[get_active_user] = lambda: principal
        return TestClient(self.app)

    def admin_client(self) -> TestClient:
        return self.client_as(RequestUser(user_id=self.admin_id, role="admin", mfa_verified=True))

    def user_client(self, user_id: UUID) -> TestClient:
        return self.client_as(RequestUser(user_id=user_id, role="user", mfa_verified=False))


def _bind_body(personal_id: str = "A123456789", cert_password: str = CERT_PASSWORD) -> dict[str, str]:
    return {
        "broker": "fubon",
        "personalId": personal_id,
        "password": "login-pw",
        "certPfxBase64": base64.b64encode(PFX).decode("ascii"),
        "certPassword": cert_password,
    }


def _row(engine: Engine, user_id: UUID) -> BrokerAccount | None:
    with Session(engine) as session:
        return session.execute(select(BrokerAccount).where(BrokerAccount.user_id == user_id)).scalar_one_or_none()


def _capture_app_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Alembic's `fileConfig(alembic.ini)` (run by the engine fixture) disables every
    logger that already existed, including the app's, so caplog would stay empty and
    a "secret not in log" assertion would be vacuous. Re-enable what we assert on."""
    for name in ("app.services.broker_session_pool", "app.commands.broker_account", "app.api.errors"):
        logging.getLogger(name).disabled = False
    caplog.set_level(logging.DEBUG)


def _audit_types(engine: Engine, actor_id: UUID) -> list[str]:
    with Session(engine) as session:
        rows = session.execute(
            select(AuditEvent.event_type).where(AuditEvent.actor_id == actor_id).order_by(AuditEvent.occurred_at)
        ).all()
    return [row for (row,) in rows]


@pytest.mark.integration
def test_bind_logs_in_subscribes_open_intents_and_persists_encrypted(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    _seed_core_intent(engine, user_id)

    response = harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["broker"] == "fubon"
    assert body["brokerAccountNo"] == "***6543"
    assert body["status"] == "active"
    assert body["lastError"] is None
    assert datetime.fromisoformat(body["certExpiresAt"]) == DEFAULT_NOT_AFTER
    for secret in ("A123456789", "login-pw", CERT_PASSWORD, "certPfxBase64"):
        assert secret not in response.text

    provider = harness.built[0]
    assert harness.pool.get(user_id) is provider
    assert provider.subscribed == {"2330"}

    row = _row(engine, user_id)
    assert row is not None
    assert row.status == "active"
    assert row.broker_account_no == "9876543"
    assert row.last_login_at is not None
    # Stored encrypted, and only the repository (with the key) can get it back.
    assert b"A123456789" not in row.credentials_encrypted
    with Session(engine) as session:
        repo = BrokerAccountRepository(session, encryption_key=TEST_CREDENTIAL_KEY)
        creds = repo.get_credentials(user_id)
    assert creds == FubonCredentials(
        personal_id="A123456789", password="login-pw", cert_pfx=PFX, cert_password=CERT_PASSWORD
    )
    assert _audit_types(engine, harness.admin_id) == ["broker_account_bound"]


@pytest.mark.integration
def test_first_bind_login_failure_is_422_without_a_row_and_without_secrets(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    harness = _Harness(engine)
    harness.fail_with = _SdkLoginError("login_rejected")
    harness.fail_with.args = (f"broker said no for {SECRET_SENTINEL}",)
    user_id = _seed_user(engine)
    _capture_app_logs(caplog)

    response = harness.admin_client().put(
        f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id=SECRET_SENTINEL)
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "BROKER_LOGIN_FAILED"
    assert response.json()["error"]["details"] == {"reason": "login_rejected"}
    assert SECRET_SENTINEL not in response.text
    assert "code=login_rejected" in caplog.text  # the log line exists...
    assert SECRET_SENTINEL not in caplog.text  # ...and carries no login material
    assert _row(engine, user_id) is None
    assert harness.pool.get(user_id) is None
    assert _audit_types(engine, harness.admin_id) == []


@pytest.mark.integration
def test_non_broker_failure_is_500_with_type_only_and_no_secrets(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing wheel / SDK drift must not read as bad credentials: 5xx with the
    exception class, message kept out of the response and the log."""
    harness = _Harness(engine)
    harness.fail_with = AttributeError(f"'FubonSDK' object has no attribute 'login' {SECRET_SENTINEL}")
    user_id = _seed_user(engine)
    _capture_app_logs(caplog)

    response = harness.admin_client().put(
        f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id=SECRET_SENTINEL)
    )

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "BROKER_SESSION_SETUP_FAILED"
    assert response.json()["error"]["details"] == {"exceptionType": "AttributeError"}
    assert SECRET_SENTINEL not in response.text
    assert SECRET_SENTINEL not in caplog.text
    assert "exception=AttributeError" in caplog.text
    assert _row(engine, user_id) is None
    assert harness.pool.get(user_id) is None
    assert not harness.pool.is_binding(user_id)


@pytest.mark.integration
def test_wrong_cert_password_fails_before_any_broker_call(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)

    response = harness.admin_client().put(
        f"/admin/users/{user_id}/broker-account", json=_bind_body(cert_password="nope")
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "cert_invalid"}
    assert harness.built == []
    assert _row(engine, user_id) is None


@pytest.mark.integration
def test_rebind_failure_keeps_the_old_session_and_row(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    client = harness.admin_client()
    assert client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200
    first = harness.built[0]
    before = _row(engine, user_id)
    assert before is not None

    harness.fail_with = _SdkLoginError("login_rejected")
    response = client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))

    assert response.status_code == 422
    assert harness.pool.get(user_id) is first
    assert not first.stopped
    after = _row(engine, user_id)
    assert after is not None
    assert after.updated_at == before.updated_at
    assert after.credentials_encrypted == before.credentials_encrypted


@pytest.mark.integration
def test_rebind_success_swaps_session_and_shuts_the_old_one_down(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    client = harness.admin_client()
    assert client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200

    response = client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))

    assert response.status_code == 200, response.text
    first, second = harness.built
    assert first.stopped
    assert harness.pool.get(user_id) is second
    with Session(engine) as session:
        assert (
            session.execute(select(BrokerAccount).where(BrokerAccount.user_id == user_id)).scalars().all().__len__()
            == 1
        )
        repo = BrokerAccountRepository(session, encryption_key=TEST_CREDENTIAL_KEY)
        creds = repo.get_credentials(user_id)
    assert creds is not None
    assert creds.personal_id == "B222222222"
    assert _audit_types(engine, harness.admin_id) == ["broker_account_bound", "broker_account_bound"]


@pytest.mark.integration
def test_session_cap_refuses_the_next_user_with_409(engine: Engine) -> None:
    harness = _Harness(engine, max_sessions=1)
    first, second = _seed_user(engine), _seed_user(engine)
    client = harness.admin_client()
    assert client.put(f"/admin/users/{first}/broker-account", json=_bind_body()).status_code == 200

    response = client.put(f"/admin/users/{second}/broker-account", json=_bind_body())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BROKER_SESSION_LIMIT_REACHED"
    assert response.json()["error"]["details"] == {"limit": 1}
    assert _row(engine, second) is None


@pytest.mark.integration
def test_bind_requires_an_active_target_user(engine: Engine) -> None:
    harness = _Harness(engine)
    client = harness.admin_client()
    invited = _seed_user(engine, status="invited")

    response = client.put(f"/admin/users/{invited}/broker-account", json=_bind_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_ACTIVE"

    assert client.put(f"/admin/users/{uuid4()}/broker-account", json=_bind_body()).status_code == 404


@pytest.mark.integration
def test_bind_request_validation(engine: Engine) -> None:
    harness = _Harness(engine)
    client = harness.admin_client()
    user_id = _seed_user(engine)

    # pydantic's Base64Bytes is lenient (drops non-alphabet chars), so garbage
    # decodes to nothing; the decoded-size check turns that into a plain 422.
    not_base64 = client.put(f"/admin/users/{user_id}/broker-account", json={**_bind_body(), "certPfxBase64": "!!"})
    assert not_base64.status_code == 422
    assert not_base64.json()["error"]["code"] == "VALIDATION_ERROR"
    [error] = not_base64.json()["error"]["details"]["errors"]
    assert error["loc"] == ["body", "certPfxBase64"] and error["type"] == "bytes_too_short"

    extra = client.put(f"/admin/users/{user_id}/broker-account", json={**_bind_body(), "userId": "x"})
    assert extra.status_code == 422

    too_big = base64.b64encode(b"\0" * (64 * 1024 + 1)).decode("ascii")
    oversized = client.put(f"/admin/users/{user_id}/broker-account", json={**_bind_body(), "certPfxBase64": too_big})
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "VALIDATION_ERROR"
    assert harness.built == []


@pytest.mark.integration
def test_me_broker_account_is_read_only_and_secret_free(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)

    assert harness.user_client(user_id).get("/me/broker-account").status_code == 404

    assert harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200
    # Overrides live on the shared app, so the principal is re-set after the admin call.
    response = harness.user_client(user_id).get("/me/broker-account")

    assert response.status_code == 200
    assert set(response.json()) == {
        "broker",
        "brokerAccountNo",
        "status",
        "certExpiresAt",
        "lastLoginAt",
        "lastError",
        "updatedAt",
    }
    assert response.json()["brokerAccountNo"] == "***6543"


@pytest.mark.integration
def test_non_admin_cannot_bind_or_unbind(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    me = harness.user_client(user_id)

    assert me.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 403
    assert me.delete(f"/admin/users/{user_id}/broker-account").status_code == 403
    assert _row(engine, user_id) is None


@pytest.mark.integration
def test_unbind_cancels_intents_deletes_row_and_logs_out(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    intent_id = _seed_core_intent(engine, user_id)
    legacy_intent_id = _seed_legacy_intent(engine, user_id)
    client = harness.admin_client()
    assert client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200
    provider = harness.built[0]

    response = client.delete(f"/admin/users/{user_id}/broker-account")

    assert response.status_code == 204
    assert provider.stopped
    assert harness.pool.get(user_id) is None
    assert _row(engine, user_id) is None
    with Session(engine) as session:
        intent = session.execute(select(TradeIntentCore).where(TradeIntentCore.id == intent_id)).scalar_one()
        legacy = session.execute(select(TradeIntent).where(TradeIntent.id == legacy_intent_id)).scalar_one()
        unbound_audit = session.execute(
            select(AuditEvent).where(
                AuditEvent.event_type == "broker_account_unbound", AuditEvent.actor_id == harness.admin_id
            )
        ).scalar_one()
    assert intent.status == "cancelled"
    assert intent.cancelled_at is not None
    # 兩軌都要取消：TWAP 仍寫舊表，漏掉這邊的話 scheduler 會替沒有行情來源的人繼續切片發通知。
    assert legacy.status == "cancelled"
    assert unbound_audit.event_metadata["cancelled_intent_count"] == 2
    assert _audit_types(engine, harness.admin_id) == ["broker_account_bound", "broker_account_unbound"]

    # Idempotent: nothing left to do, no second audit row.
    assert client.delete(f"/admin/users/{user_id}/broker-account").status_code == 204
    assert _audit_types(engine, harness.admin_id) == ["broker_account_bound", "broker_account_unbound"]
    assert client.delete(f"/admin/users/{uuid4()}/broker-account").status_code == 404


@pytest.mark.integration
def test_mark_login_failed_stores_only_the_whitelisted_message(engine: Engine) -> None:
    user_id = _seed_user(engine)
    key = TEST_CREDENTIAL_KEY
    now = datetime.now(UTC)
    with Session(engine) as session:
        repo = BrokerAccountRepository(session, encryption_key=key)
        repo.upsert(
            user_id,
            broker="fubon",
            credentials=FubonCredentials(personal_id=SECRET_SENTINEL, password="p", cert_pfx=PFX, cert_password="c"),
            broker_account_no="1",
            cert_expires_at=DEFAULT_NOT_AFTER,
            now=now,
        )
        repo.mark_login_failed(user_id, code="login_rejected", now=now)
        session.commit()

    row = _row(engine, user_id)
    assert row is not None
    assert row.status == "login_failed"
    assert row.last_error == "券商拒絕登入，請確認身分證字號、密碼與憑證"

    with Session(engine) as session:
        repo = BrokerAccountRepository(session, encryption_key=key)
        repo.mark_login_ok(user_id, now=now)
        session.commit()
        assert [a.user_id for a in repo.list_all() if a.user_id == user_id] == [user_id]
    row = _row(engine, user_id)
    assert row is not None
    assert row.status == "active"
    assert row.last_error is None


# --- extreme paths --------------------------------------------------------------


@pytest.mark.integration
def test_rebind_whose_subscribe_fails_keeps_old_session_and_returns_503(engine: Engine) -> None:
    """Login succeeded but the market-data socket is gone (broker closes it after
    hours): the candidate is logged out, nothing is written, the old session stays."""
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    _seed_core_intent(engine, user_id)
    client = harness.admin_client()
    assert client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200
    before = _row(engine, user_id)
    assert before is not None

    harness.subscribe_fail_with = QuoteProviderUnavailableError("fubon", "subscribe_failed")
    response = client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "QUOTE_PROVIDER_UNAVAILABLE"
    first, candidate = harness.built
    assert candidate.stopped and not first.stopped
    assert harness.pool.get(user_id) is first
    after = _row(engine, user_id)
    assert after is not None
    assert after.credentials_encrypted == before.credentials_encrypted
    assert _audit_types(engine, harness.admin_id) == ["broker_account_bound"]


@pytest.mark.integration
def test_old_session_logout_raising_does_not_undo_a_committed_rebind(engine: Engine) -> None:
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    client = harness.admin_client()
    harness.shutdown_fail_with = RuntimeError("socket already closed by broker")
    assert client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200
    harness.shutdown_fail_with = None

    response = client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))

    assert response.status_code == 200, response.text
    first, second = harness.built
    assert first.stopped  # attempted, raised, swallowed
    assert harness.pool.get(user_id) is second
    with Session(engine) as session:
        repo = BrokerAccountRepository(session, encryption_key=TEST_CREDENTIAL_KEY)
        creds = repo.get_credentials(user_id)
    assert creds is not None and creds.personal_id == "B222222222"


@pytest.mark.integration
def test_second_bind_and_unbind_are_refused_while_the_first_login_is_in_flight(engine: Engine) -> None:
    """Two admins act on the same user while its broker login is still busy-spinning:
    the later bind and an unbind both get 409, and the first bind completes normally."""
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    gate = threading.Event()
    harness.startup_gate = gate
    first_result: list[int] = []

    def first_bind() -> None:
        first_result.append(
            harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code
        )

    worker = threading.Thread(target=first_bind)
    worker.start()
    try:
        deadline = threading.Event()
        for _ in range(50):
            if harness.built and harness.built[0].in_startup.is_set():
                break
            deadline.wait(0.1)
        assert harness.built and harness.built[0].in_startup.is_set()
        harness.startup_gate = None
        client = harness.admin_client()

        second = client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))
        unbind = client.delete(f"/admin/users/{user_id}/broker-account")
    finally:
        gate.set()
        worker.join(timeout=10)

    assert second.status_code == 409 and second.json()["error"]["code"] == "BROKER_BIND_IN_PROGRESS"
    assert unbind.status_code == 409 and unbind.json()["error"]["code"] == "BROKER_BIND_IN_PROGRESS"
    assert first_result == [200]
    assert len(harness.built) == 1  # the refused bind never built (or logged in) a provider
    assert harness.pool.get(user_id) is harness.built[0]
    assert _row(engine, user_id) is not None


@pytest.mark.integration
def test_credentials_encrypted_under_a_rotated_key_raise_invalid_token(engine: Engine) -> None:
    """If MFA_ENCRYPTION_KEY is rotated without re-encrypting broker_accounts, the
    repository cannot decrypt: it raises the domain error (never the raw
    cryptography one). PR3's startup loop handles it per user."""
    from app.domain.broker_account import BrokerCredentialKeyError

    user_id = _seed_user(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        BrokerAccountRepository(session, encryption_key=TEST_CREDENTIAL_KEY).upsert(
            user_id,
            broker="fubon",
            credentials=FubonCredentials(personal_id="A1", password="p", cert_pfx=PFX, cert_password="c"),
            broker_account_no="1",
            cert_expires_at=DEFAULT_NOT_AFTER,
            now=now,
        )
        session.commit()

    with Session(engine) as session, pytest.raises(BrokerCredentialKeyError) as info:
        BrokerAccountRepository(session, encryption_key=Fernet.generate_key().decode()).get_credentials(user_id)
    assert info.value.reason == "undecryptable"


@pytest.mark.integration
def test_two_transactions_inserting_the_same_user_hit_the_unique_index(engine: Engine) -> None:
    """The pool token serialises binds inside one process; across processes (or a
    future multi-worker mistake) the DB unique index is the last guard. The second
    insert blocks on the first's row lock and fails once it commits."""
    from sqlalchemy.exc import IntegrityError

    user_id = _seed_user(engine)
    key = TEST_CREDENTIAL_KEY
    now = datetime.now(UTC)
    creds = FubonCredentials(personal_id="A1", password="p", cert_pfx=PFX, cert_password="c")
    errors: list[Exception] = []
    second_flushed = threading.Event()

    def second_writer() -> None:
        with Session(engine) as session:
            try:
                BrokerAccountRepository(session, encryption_key=key).upsert(
                    user_id,
                    broker="fubon",
                    credentials=creds,
                    broker_account_no="2",
                    cert_expires_at=DEFAULT_NOT_AFTER,
                    now=now,
                )
                session.commit()
            except IntegrityError as exc:
                errors.append(exc)
                session.rollback()
            finally:
                second_flushed.set()

    with Session(engine) as first:
        BrokerAccountRepository(first, encryption_key=key).upsert(
            user_id,
            broker="fubon",
            credentials=creds,
            broker_account_no="1",
            cert_expires_at=DEFAULT_NOT_AFTER,
            now=now,
        )
        thread = threading.Thread(target=second_writer)
        thread.start()
        assert not second_flushed.wait(timeout=1.0)  # blocked on the uncommitted insert
        first.commit()
        thread.join(timeout=10)

    assert len(errors) == 1
    row = _row(engine, user_id)
    assert row is not None and row.broker_account_no == "1"


class _GatedClaimPool(BrokerSessionPool):
    """Pauses an unbind right after it took the user's token, so a bind can be
    fired while the DELETE is between its claim and its commit."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.claimed = threading.Event()
        self.gate = threading.Event()

    def claim(self, user_id: UUID) -> BrokerStopClaim:
        claim = super().claim(user_id)
        self.claimed.set()
        assert self.gate.wait(timeout=10)
        return claim


@pytest.mark.integration
def test_bind_during_an_unbind_is_refused_and_the_unbind_completes(engine: Engine) -> None:
    """The race found in review: DELETE past its pre-check, PUT logs in, DELETE
    deletes the row, PUT's UPDATE hits a vanished row and DELETE's final stop is
    refused — leaving a live session with no row. With the unbind holding the
    token for its whole duration the PUT is turned away at `prepare` instead."""
    harness = _Harness(engine)
    user_id = _seed_user(engine)
    _seed_core_intent(engine, user_id)
    client = harness.admin_client()
    assert client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body()).status_code == 200

    gated = _GatedClaimPool(get_settings(), shared=None, provider_factory=harness._factory)
    gated.activate(gated.prepare(user_id, FubonCredentials("x", "y", PFX, "z")))  # re-home the live session
    harness.pool = gated
    harness.app.state.broker_sessions = gated
    delete_result: list[int] = []

    def unbind() -> None:
        delete_result.append(harness.admin_client().delete(f"/admin/users/{user_id}/broker-account").status_code)

    worker = threading.Thread(target=unbind)
    worker.start()
    try:
        assert gated.claimed.wait(timeout=5)
        built_before = len(harness.built)
        response = client.put(f"/admin/users/{user_id}/broker-account", json=_bind_body(personal_id="B222222222"))
    finally:
        gated.gate.set()
        worker.join(timeout=10)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BROKER_BIND_IN_PROGRESS"
    assert len(harness.built) == built_before  # the refused bind never logged in
    assert delete_result == [204]
    assert _row(engine, user_id) is None
    assert gated.get(user_id) is None
    assert not gated.is_binding(user_id)
    assert harness.built[1].stopped  # the session that was live in the gated pool when the unbind ran


@pytest.mark.integration
def test_validation_failures_never_echo_the_submitted_secrets(engine: Engine) -> None:
    """pydantic puts the offending value in every error's `input`; the envelope
    must drop it or an oversized pfx / password comes straight back in the 422."""
    harness = _Harness(engine)
    client = harness.admin_client()
    user_id = _seed_user(engine)
    long_password = "P" * 300
    huge_pfx = base64.b64encode(b"\x01" * (64 * 1024 + 1)).decode("ascii")
    long_personal_id = "Q" * 40

    for field, value in (("password", long_password), ("certPfxBase64", huge_pfx), ("personalId", long_personal_id)):
        response = client.put(f"/admin/users/{user_id}/broker-account", json={**_bind_body(), field: value})
        assert response.status_code == 422, field
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert value not in response.text, field
        [error] = body["error"]["details"]["errors"]
        assert error["loc"] == ["body", field]
        assert set(error) == {"type", "loc", "msg", "ctx"}
        # Not even the size of the secret comes back (SecretStr `too_long` used to).
        assert "actual_length" not in error["ctx"]
        assert str(len(value)) not in error["msg"]
    assert harness.built == []


@pytest.mark.integration
def test_shared_mode_refuses_binding_with_a_non_retryable_409(engine: Engine) -> None:
    """Every configuration this PR can boot with is shared-mode (in_memory / demo).
    Binding there must not look like a transient broker outage: 409 with a code
    that names the configuration, no row, no audit."""
    harness = _Harness(engine)
    harness.app.state.broker_sessions = create_app().state.broker_sessions  # the real, shared-mode pool
    user_id = _seed_user(engine)

    response = harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body())

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "BROKER_BINDING_NOT_ENABLED"
    assert response.json()["error"]["details"] == {"quoteProvider": "in_memory"}
    assert _row(engine, user_id) is None
    assert _audit_types(engine, harness.admin_id) == []
    assert harness.admin_client().get("/me/broker-account").status_code == 404


@pytest.mark.integration
def test_bind_refuses_the_dev_fallback_key_even_in_local_mode(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review finding: with MFA_ENCRYPTION_KEY unset, LOCAL_MODE encrypted broker
    credentials under the key checked into the repo. The write path now refuses
    (500 naming the config), the candidate login is released, no row is written."""
    monkeypatch.delenv("MFA_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    assert get_settings().local_mode and get_settings().broker_credential_key is None
    harness = _Harness(engine)
    user_id = _seed_user(engine)

    response = harness.admin_client().put(f"/admin/users/{user_id}/broker-account", json=_bind_body())

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "BROKER_CREDENTIAL_KEY_INVALID"
    assert response.json()["error"]["details"] == {"reason": "missing"}
    assert _row(engine, user_id) is None
    assert harness.built[0].started and harness.built[0].stopped  # logged in, then discarded
    assert harness.pool.get(user_id) is None and not harness.pool.is_binding(user_id)
    # Non-secret reads still work without a key.
    assert harness.user_client(user_id).get("/me/broker-account").status_code == 404
