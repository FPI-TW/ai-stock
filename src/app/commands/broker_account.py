"""Admin binds / unbinds a user's broker account.

Bind is "login first, persist second": the candidate session must log in and
subscribe the user's open intents before anything is written, and the live
session (on re-bind) is only replaced after the row and audit are committed.
Unbind cancels the user's open intents (no session, no quotes, they would never
fire), deletes the row, commits, and only then logs the session out.

Nothing here ever logs or stores `str(exc)` from the broker path: DB, API and
logs see only `BrokerLoginFailureCode` and its whitelisted message.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cryptography.hazmat.primitives.serialization import pkcs12
from sqlalchemy.orm import Session

from app.core.config import BrokerName
from app.domain.auth import AccountError, AccountNotActiveError, UserNotFoundError
from app.domain.broker_account import (
    BrokerAccountData,
    BrokerAccountError,
    BrokerLoginFailedError,
    FubonCredentials,
)
from app.repositories.broker_account_repository import BrokerAccountRepository
from app.repositories.intent_repository import IntentRepository
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter
from app.services.broker_session_pool import BrokerSessionPool
from app.services.quote.base import QuoteProvider

logger = logging.getLogger(__name__)

# Unbound users lose their quote source; the intents are cancelled as if by the user.
UNBOUND_INTENT_STATUS = "cancelled"


@dataclass(frozen=True, slots=True)
class BindBrokerAccountInput:
    target_user_id: UUID
    broker: BrokerName
    credentials: FubonCredentials
    actor_admin_id: UUID
    now: datetime
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class BoundBrokerAccount:
    account: BrokerAccountData
    # The session this bind replaced, still logged in. The caller retires it
    # *after* responding (unsubscribe + logout can take seconds; the bind is
    # already committed and live, so the admin should not wait on the old one).
    replaced_session: QuoteProvider | None


def retire_broker_session(provider: QuoteProvider, user_id: UUID) -> None:
    """Best-effort logout of a session that is no longer live. Never raises: the
    new binding is committed and serving; a stale logout is not a rollback reason."""
    try:
        provider.shutdown()
    except Exception as exc:
        logger.warning("previous broker session shutdown raised %s user_id=%s", type(exc).__name__, user_id)


@dataclass(frozen=True, slots=True)
class UnbindBrokerAccountInput:
    target_user_id: UUID
    actor_admin_id: UUID
    now: datetime
    request_id: str | None = None


def _cert_expires_at(cert_pfx: bytes, cert_password: str) -> datetime:
    """Open the pfx with its password (a wrong one fails here, before any broker call)
    and read the certificate's expiry. Nothing from the bundle is kept."""
    try:
        _key, cert, _extra = pkcs12.load_key_and_certificates(cert_pfx, cert_password.encode("utf-8"))
    except Exception as exc:
        raise BrokerLoginFailedError("cert_invalid") from exc
    if cert is None:
        raise BrokerLoginFailedError("cert_invalid")
    return cert.not_valid_after_utc


class BindBrokerAccountCommand:
    def __init__(
        self,
        db: Session,
        users: UserRepository,
        accounts: BrokerAccountRepository,
        core_intents: TradeIntentCoreRepository,
        audit: AuditEventWriter,
        pool: BrokerSessionPool,
    ) -> None:
        self._db = db
        self._users = users
        self._accounts = accounts
        self._core_intents = core_intents
        self._audit = audit
        self._pool = pool

    def execute(self, inp: BindBrokerAccountInput) -> BoundBrokerAccount:
        user = self._users.get_by_id(inp.target_user_id)
        if user is None:
            raise UserNotFoundError()
        if user.status != "active":
            raise AccountNotActiveError()
        cert_expires_at = _cert_expires_at(inp.credentials.cert_pfx, inp.credentials.cert_password)
        rebinding = self._accounts.get_by_user_id(user.id) is not None

        # Login happens here; failure leaves the live session and the DB untouched.
        candidate = self._pool.prepare(user.id, inp.credentials)
        committed = False
        try:
            for symbol in sorted(self._core_intents.active_or_scheduled_symbols_by_owner(user.id)):
                candidate.provider.subscribe(symbol)
            self._accounts.upsert(
                user.id,
                broker=inp.broker,
                credentials=inp.credentials,
                broker_account_no=candidate.broker_account_no,
                cert_expires_at=cert_expires_at,
                now=inp.now,
            )
            self._audit.write(
                event_type="broker_account_bound",
                actor_type="admin",
                actor_id=inp.actor_admin_id,
                metadata={"target_user_id": str(user.id), "broker": inp.broker, "rebinding": rebinding},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            committed = True
        except (AccountError, BrokerAccountError):
            raise
        except Exception as exc:
            logger.warning(
                "broker account bind failed before commit %s user_id=%s request_id=%s",
                type(exc).__name__,
                user.id,
                inp.request_id,
            )
            raise
        finally:
            if not committed:
                # The candidate holds a real broker login: release it first, and
                # unconditionally — a rollback on a dead DB connection raises too,
                # and must not stand between us and the logout.
                self._pool.discard(candidate)
                try:
                    self._db.rollback()
                except Exception as exc:
                    logger.warning("rollback after failed bind raised %s user_id=%s", type(exc).__name__, user.id)

        try:
            old = self._pool.activate(candidate)
        except Exception:
            # Row is committed but the candidate could not go live: log it out so
            # the state degrades to "bound, no session" (a restart re-logs in).
            self._pool.discard(candidate)
            raise
        bound = self._accounts.get_by_user_id(user.id)
        assert bound is not None  # just committed
        return BoundBrokerAccount(account=bound, replaced_session=old)


class UnbindBrokerAccountCommand:
    """Idempotent: a user without a binding still gets a clean 204 and no side effects."""

    def __init__(
        self,
        db: Session,
        users: UserRepository,
        accounts: BrokerAccountRepository,
        intents: IntentRepository,
        core_intents: TradeIntentCoreRepository,
        audit: AuditEventWriter,
        pool: BrokerSessionPool,
    ) -> None:
        self._db = db
        self._users = users
        self._accounts = accounts
        self._intents = intents
        self._core_intents = core_intents
        self._audit = audit
        self._pool = pool

    def execute(self, inp: UnbindBrokerAccountInput) -> None:
        user = self._users.get_by_id(inp.target_user_id)
        if user is None:
            raise UserNotFoundError()
        # Hold the user's operation token for the whole unbind: a concurrent bind
        # now fails at `prepare` instead of racing the row delete below.
        claim = self._pool.claim(user.id)
        committed = False
        try:
            if self._accounts.get_by_user_id(user.id) is None:
                return
            # Both tracks, same as DisableUserCommand: TWAP still lives on the legacy
            # table, and a TWAP left active here would keep slicing and notifying
            # for a user who no longer has a quote source.
            cancelled = self._intents.cancel_active_for_owner(
                user.id, status=UNBOUND_INTENT_STATUS, now=inp.now
            ) + self._core_intents.cancel_active_for_owner(user.id, status=UNBOUND_INTENT_STATUS, now=inp.now)
            self._accounts.delete(user.id)
            self._audit.write(
                event_type="broker_account_unbound",
                actor_type="admin",
                actor_id=inp.actor_admin_id,
                metadata={"target_user_id": str(user.id), "cancelled_intent_count": cancelled},
                request_id=inp.request_id,
                now=inp.now,
            )
            self._db.commit()
            committed = True
        finally:
            if not committed:
                # Same shape as the bind path: give the token back before touching
                # the DB again — a rollback on a dead connection raises too, and
                # must not wedge this user behind a claim nobody holds.
                self._pool.release(claim)
                try:
                    self._db.rollback()
                except Exception as exc:
                    logger.warning("rollback after failed unbind raised %s user_id=%s", type(exc).__name__, user.id)
        # Row is gone; the session goes last so a DB failure never leaves a rowless live session.
        self._pool.stop(claim)
