"""Bind command without Postgres: pfx parsing, the runtime import boundary, and
the cleanup contract when something fails between broker login and commit."""

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError
from tests.pfx_helpers import build_test_pfx
from tests.unit.test_broker_session_pool import FakeProvider, live_session, token_free

from app.commands.broker_account import (
    BindBrokerAccountCommand,
    BindBrokerAccountInput,
    UnbindBrokerAccountCommand,
    UnbindBrokerAccountInput,
    _cert_expires_at,
)
from app.core.config import get_settings
from app.domain.auth import UserData
from app.domain.broker_account import BrokerAccountData, BrokerLoginFailedError, FubonCredentials
from app.services.broker_session_pool import BrokerSessionPool, retire_broker_session
from app.services.quote.base import QuoteProviderUnavailableError


def test_bind_request_repr_masks_every_secret() -> None:
    from app.api.schemas.broker_account import BindBrokerAccountRequest

    body = BindBrokerAccountRequest.model_validate(
        {
            "broker": "fubon",
            "personalId": "A123456789",
            "password": "login-pw",
            "certPfxBase64": "QUJD",
            "certPassword": "cert-pw",
        }
    )

    text = f"{body!r} {body}"
    for secret in ("A123456789", "login-pw", "cert-pw", "ABC", "QUJD"):
        assert secret not in text
    assert body.personal_id.get_secret_value() == "A123456789"
    assert bytes(body.cert_pfx) == b"ABC"


@pytest.mark.parametrize(
    ("decoded_size", "ok"),
    [
        (50 * 1024, True),  # > 48 KB: the old encoded-length cap rejected this
        (64 * 1024, True),  # exactly the documented limit
        (64 * 1024 + 1, False),
        (0, False),
    ],
)
def test_cert_pfx_limit_is_measured_on_decoded_bytes(decoded_size: int, ok: bool) -> None:
    import base64

    from pydantic import ValidationError

    from app.api.schemas.broker_account import BindBrokerAccountRequest

    body = {
        "broker": "fubon",
        "personalId": "A123456789",
        "password": "pw",
        "certPfxBase64": base64.b64encode(b"\x01" * decoded_size).decode("ascii"),
        "certPassword": "cpw",
    }
    if ok:
        assert len(bytes(BindBrokerAccountRequest.model_validate(body).cert_pfx)) == decoded_size
        return
    with pytest.raises(ValidationError) as info:
        BindBrokerAccountRequest.model_validate(body)
    [error] = info.value.errors()
    assert error["loc"] == ("certPfxBase64",)
    assert error["type"] in {"bytes_too_long", "bytes_too_short"}
    import json

    json.dumps(error.get("ctx"))  # the 422 envelope must stay serialisable


def test_cert_expiry_is_read_from_the_pfx() -> None:
    expires = datetime(2027, 6, 30, 12, 0, tzinfo=UTC)
    pfx = build_test_pfx(password="secret", not_valid_after=expires)

    assert _cert_expires_at(pfx, "secret") == expires


def test_wrong_cert_password_is_a_safe_login_failure() -> None:
    pfx = build_test_pfx(password="secret")

    with pytest.raises(BrokerLoginFailedError) as info:
        _cert_expires_at(pfx, "wrong")
    assert info.value.code == "cert_invalid"


def test_garbage_pfx_is_a_safe_login_failure() -> None:
    with pytest.raises(BrokerLoginFailedError) as info:
        _cert_expires_at(b"not a pfx", "x")
    assert info.value.code == "cert_invalid"


def test_in_memory_runtime_never_imports_the_fubon_package() -> None:
    """Fresh interpreter: other tests in this process import the Fubon modules on
    purpose, so `sys.modules` here would prove nothing."""
    script = "import sys, app.main; print(sorted(n for n in sys.modules if n.startswith('app.services.quote.fubon')))"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "QUOTE_PROVIDER": "in_memory",
        "LOCAL_MODE": "true",
        "LOCAL_USER_ID": "00000000-0000-0000-0000-000000000001",
    }
    env.pop("DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env, check=True)

    assert result.stdout.strip() == "[]"


# --- failures between login and commit ------------------------------------------
# Real Postgres is not needed to prove the cleanup contract: whatever fails after
# the candidate logged in, the candidate must be shut down and the live session
# (if any) left alone. Stubs stand in for the repositories.

_PFX = build_test_pfx(password="secret")
_CREDS = FubonCredentials(personal_id="A123456789", password="pw", cert_pfx=_PFX, cert_password="secret")


@dataclass
class _Db:
    commit_fail_with: Exception | None = None
    rollback_fail_with: Exception | None = None
    commits: int = 0
    rollbacks: int = 0

    def commit(self) -> None:
        if self.commit_fail_with is not None:
            raise self.commit_fail_with
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_fail_with is not None:
            raise self.rollback_fail_with


class _Users:
    def __init__(self, user_id: UUID) -> None:
        now = datetime.now(UTC)
        self._user = UserData(
            id=user_id,
            email="u@example.com",
            role="user",
            status="active",
            mfa_enabled=False,
            password_hash=None,
            created_at=now,
            updated_at=now,
        )

    def get_by_id(self, user_id: UUID) -> UserData | None:
        return self._user if user_id == self._user.id else None


@dataclass
class _Accounts:
    upserts: int = 0
    bound: object | None = None  # unbind path: truthy = "a row exists"

    def get_by_user_id(self, user_id: UUID) -> object | None:
        return self.bound

    def upsert(self, user_id: UUID, **_: object) -> tuple[BrokerAccountData, bool]:
        self.upserts += 1
        now = datetime.now(UTC)
        data = BrokerAccountData(
            id=uuid4(),
            user_id=user_id,
            broker="fubon",
            broker_account_no="9876543",
            cert_expires_at=now,
            status="active",
            last_login_at=now,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        return data, self.upserts > 1

    def delete(self, user_id: UUID) -> bool:
        return True


@dataclass
class _CoreIntents:
    symbols: set[str] = field(default_factory=lambda: {"2330", "2317"})

    def active_or_scheduled_symbols_by_owner(self, owner_user_id: UUID) -> set[str]:
        return set(self.symbols)

    def cancel_active_for_owner(self, owner_user_id: UUID, *, status: str, now: datetime) -> int:
        return 1


class _Audit:
    def write(self, **_: object) -> None:
        return None


def _command(
    db: _Db, *, provider_kwargs: dict[str, object] | None = None
) -> tuple[BindBrokerAccountCommand, BrokerSessionPool, list[FakeProvider], UUID]:
    built: list[FakeProvider] = []

    def factory(_creds: FubonCredentials) -> FakeProvider:
        provider = FakeProvider(**(provider_kwargs or {}))  # type: ignore[arg-type]
        built.append(provider)
        return provider

    pool = BrokerSessionPool(get_settings(), shared=None, provider_factory=factory)
    user_id = uuid4()
    command = BindBrokerAccountCommand(db, _Users(user_id), _Accounts(), _CoreIntents(), _Audit(), pool)  # type: ignore[arg-type]
    return command, pool, built, user_id


def _input(user_id: UUID) -> BindBrokerAccountInput:
    return BindBrokerAccountInput(
        target_user_id=user_id, broker="fubon", credentials=_CREDS, actor_admin_id=uuid4(), now=datetime.now(UTC)
    )


def test_commit_failure_rolls_back_and_logs_the_candidate_out() -> None:
    """DB connection dropped at commit (OperationalError): the candidate already
    holds a broker login, so it must be shut down, and the pool must hold nothing."""
    db = _Db(commit_fail_with=OperationalError("COMMIT", {}, Exception("server closed the connection")))
    command, pool, built, user_id = _command(db)

    with pytest.raises(OperationalError):
        command.execute(_input(user_id))

    assert db.rollbacks == 1
    assert built[0].started and built[0].stopped
    assert live_session(pool, user_id) is None
    assert token_free(pool, user_id)


def test_subscribe_failure_after_login_is_cleaned_up_and_surfaces_as_provider_error() -> None:
    """Socket dropped between login and subscribe (the broker closes it ~14:05):
    nothing is written, the candidate logs out, the error keeps its 503 mapping."""
    db = _Db()
    command, pool, built, user_id = _command(
        db, provider_kwargs={"subscribe_fail_with": QuoteProviderUnavailableError("fubon", "subscribe_failed")}
    )

    with pytest.raises(QuoteProviderUnavailableError):
        command.execute(_input(user_id))

    assert db.commits == 0 and db.rollbacks == 1
    assert built[0].stopped
    assert live_session(pool, user_id) is None


def test_rebind_failure_before_commit_leaves_the_live_session_untouched() -> None:
    db = _Db()
    command, pool, built, user_id = _command(db)
    pool.activate(pool.prepare(user_id, _CREDS))  # existing live session
    live = built[0]

    db.commit_fail_with = OperationalError("COMMIT", {}, Exception("deadlock detected"))
    with pytest.raises(OperationalError):
        command.execute(_input(user_id))

    assert live_session(pool, user_id) is live
    assert not live.stopped
    assert built[1].stopped


def test_rollback_raising_on_a_dead_connection_still_logs_the_candidate_out() -> None:
    """The review finding: commit fails because the DB connection died, then
    rollback() raises against the same dead connection. The broker logout must
    not depend on rollback succeeding, and the user's token must be released."""
    dead = OperationalError("COMMIT", {}, Exception("connection lost"))
    db = _Db(commit_fail_with=dead, rollback_fail_with=OperationalError("ROLLBACK", {}, Exception("connection lost")))
    command, pool, built, user_id = _command(db)

    with pytest.raises(OperationalError) as info:
        command.execute(_input(user_id))

    assert info.value is dead  # the original failure surfaces, not the rollback's
    assert db.rollbacks == 1
    assert built[0].stopped
    assert live_session(pool, user_id) is None
    assert token_free(pool, user_id)
    # The user is not wedged: the token is free for the next bind.
    pool.activate(pool.prepare(user_id, _CREDS))
    assert live_session(pool, user_id) is built[1]


def test_activate_failure_after_commit_logs_the_candidate_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the swap itself fails after the row is committed, the state must degrade
    to "bound, no session" rather than a live login nobody tracks."""
    db = _Db()
    command, pool, built, user_id = _command(db)

    def stolen(_candidate: object) -> None:
        raise RuntimeError("token vanished")

    monkeypatch.setattr(pool, "activate", stolen)
    with pytest.raises(RuntimeError):
        command.execute(_input(user_id))

    assert db.commits == 1
    assert built[0].stopped
    assert live_session(pool, user_id) is None


def test_unbind_rollback_raising_on_a_dead_connection_still_releases_the_claim() -> None:
    """Review finding: unbind released the claim only after rollback(), so a
    rollback that raises (dead connection) wedged the user behind a token nobody
    held — every later bind / unbind answered 409 until restart."""
    dead = OperationalError("COMMIT", {}, Exception("connection lost"))
    db = _Db(commit_fail_with=dead, rollback_fail_with=OperationalError("ROLLBACK", {}, Exception("connection lost")))
    built: list[FakeProvider] = []

    def factory(_creds: FubonCredentials) -> FakeProvider:
        built.append(FakeProvider())
        return built[-1]

    pool = BrokerSessionPool(get_settings(), shared=None, provider_factory=factory)
    user_id = uuid4()
    pool.activate(pool.prepare(user_id, _CREDS))
    live = built[0]
    stubs = (db, _Users(user_id), _Accounts(bound=object()), _CoreIntents(), _CoreIntents(), _Audit())
    command = UnbindBrokerAccountCommand(*stubs, pool)  # type: ignore[arg-type]

    with pytest.raises(OperationalError) as info:
        command.execute(UnbindBrokerAccountInput(target_user_id=user_id, actor_admin_id=uuid4(), now=datetime.now(UTC)))

    assert info.value is dead  # the commit failure surfaces, not the rollback's
    assert db.rollbacks == 1
    assert token_free(pool, user_id)  # claim released even though rollback raised
    assert live_session(pool, user_id) is live and not live.stopped  # nothing committed, session stays
    # Not wedged: a bind and an unbind both proceed afterwards.
    pool.discard(pool.prepare(user_id, _CREDS))
    pool.stop(pool.claim(user_id))
    assert live.stopped


def test_rebind_hands_the_replaced_session_back_instead_of_stopping_it_inline() -> None:
    """Review finding: unsubscribe + logout of the old session ran inside the
    request after the token was already released; a slow logout could push a
    *successful* bind past the proxy timeout. The command now returns it and
    the route retires it after the response."""
    db = _Db()
    command, pool, built, user_id = _command(db)
    pool.activate(pool.prepare(user_id, _CREDS))
    old = built[0]

    result = command.execute(_input(user_id))

    assert result.replaced_session is old
    assert not old.stopped  # nothing blocking happened after activate
    assert live_session(pool, user_id) is built[1]
    assert token_free(pool, user_id)
    retire_broker_session(old, user_id)
    assert old.stopped


def test_retire_broker_session_never_raises() -> None:
    provider = FakeProvider(shutdown_fail_with=RuntimeError("socket already closed"))
    retire_broker_session(provider, uuid4())  # must not propagate
    assert provider.stopped
