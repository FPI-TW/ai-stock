"""Admin binds / unbinds a user's broker account.

Bind is "login first, persist second": the candidate session must log in and
subscribe the user's open intents before anything is written, and the live
session (on re-bind) is only replaced after the row and audit are committed.
Unbind cancels the user's open intents (no session, no quotes, they would never
fire), deletes the row, commits, and only then logs the session out.

`restore_broker_session` is the same login-then-activate shape without an admin
in the loop: the lifespan runs it for every stored binding, account reactivation
for the one user, and the reconnect loop for a session whose login the broker
dropped. It records the outcome on the row (`mark_login_ok` / `mark_login_failed`)
and raises on failure so each caller decides what a failure means for it.

Nothing here ever logs or stores `str(exc)` from the broker path: DB, API and
logs see only `BrokerLoginFailureCode` and its whitelisted message.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.serialization import pkcs12
from sqlalchemy.orm import Session

from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.core.config import BrokerName
from app.domain.auth import AccountError, AccountNotActiveError, UserNotFoundError
from app.domain.broker_account import (
    BrokerAccountData,
    BrokerAccountError,
    BrokerAccountNotBoundError,
    BrokerCredentialKeyError,
    BrokerLoginFailedError,
    BrokerLoginFailureCode,
    BrokerSessionLimitReachedError,
    BrokerSessionSetupError,
    BrokerSessionUnavailableError,
    FubonCredentials,
)
from app.domain.trading_session import TradingSessionService
from app.repositories.broker_account_repository import BrokerAccountRepository
from app.repositories.intent_repository import IntentRepository
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter
from app.services.broker_session_pool import BrokerSessionPool, retire_broker_session
from app.services.quote.base import QuoteProvider, QuoteProviderError

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
    # The session this bind replaced, still logged in. The caller hands it to
    # `broker_session_pool.retire_broker_session` *after* responding (unsubscribe +
    # logout can take seconds; the bind is already committed and live).
    replaced_session: QuoteProvider | None


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

        # Login happens here; failure leaves the live session and the DB untouched.
        candidate = self._pool.prepare(user.id, inp.credentials)
        committed = False
        try:
            for symbol in sorted(self._core_intents.active_or_scheduled_symbols_by_owner(user.id)):
                candidate.provider.subscribe(symbol)
            bound, rebinding = self._accounts.upsert(
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


def require_broker_session(pool: BrokerSessionPool, accounts: BrokerAccountRepository, user_id: UUID) -> QuoteProvider:
    """The user's quote source, or the error that names the right fix: no binding
    row -> `BrokerAccountNotBoundError` (409, admin binds); a row but no live
    session -> `BrokerSessionUnavailableError` (503, wait for the repair loop or
    re-bind; `GET /me/broker-account` shows the failure). Shared mode always has
    a provider."""
    provider = pool.get(user_id)
    if provider is not None:
        return provider
    if accounts.get_by_user_id(user_id) is None:
        raise BrokerAccountNotBoundError()
    raise BrokerSessionUnavailableError()


def _record_login_failure(
    db: Session, accounts: BrokerAccountRepository, user_id: UUID, code: BrokerLoginFailureCode, now: datetime
) -> None:
    """Best effort: the row is what the admin sees, but a DB that cannot take the
    write must not hide the original failure from the caller. The session is
    rolled back so it stays usable — the reconnect loop shares one per tick."""
    try:
        accounts.mark_login_failed(user_id, code, now=now)
        db.commit()
    except Exception as exc:
        logger.warning("could not record broker login failure %s user_id=%s", type(exc).__name__, user_id)
        try:
            db.rollback()
        except Exception as rollback_exc:
            logger.warning("rollback after failed record raised %s user_id=%s", type(rollback_exc).__name__, user_id)


def _restorable(users: UserRepository, accounts: BrokerAccountRepository, user_id: UUID) -> bool:
    """Active user with a binding row. Disable keeps the row (reactivate logs back
    in), so the user's status is part of the answer, not just the row."""
    user = users.get_by_id(user_id)
    return user is not None and user.status == "active" and accounts.get_by_user_id(user_id) is not None


def restore_broker_session(
    *,
    db: Session,
    accounts: BrokerAccountRepository,
    core_intents: TradeIntentCoreRepository,
    users: UserRepository,
    pool: BrokerSessionPool,
    user_id: UUID,
    credentials: FubonCredentials,
    now: datetime,
) -> QuoteProvider | None:
    """Log `user_id` in from stored credentials and make that session live.

    prepare -> subscribe the user's open symbols -> `mark_login_ok` + commit ->
    `activate`. Returns the session it replaced (retire it outside any lock) or
    None. On failure the candidate is logged out, the row is marked
    `login_failed` with a safe code, and the exception propagates. A bind in
    progress for this user is not a failure: nothing is recorded.

    Once the user's token is ours (after prepare) the user is re-checked: an
    admin may have disabled or unbound them between the caller's decision and
    now (disable / unbind take the same token, then commit, then stop the
    session — so whoever holds the token after that sees the committed state).
    Then the candidate is discarded and `BrokerAccountNotBoundError` raised
    without touching the row.
    """
    try:
        candidate = pool.prepare(user_id, credentials)
    except BrokerLoginFailedError as exc:
        _record_login_failure(db, accounts, user_id, exc.code, now)
        raise
    except BrokerSessionLimitReachedError:
        _record_login_failure(db, accounts, user_id, "session_pool_full", now)
        raise
    except BrokerSessionSetupError as exc:
        _record_login_failure(db, accounts, user_id, "unknown", now)
        raise BrokerLoginFailedError("unknown") from exc

    if not _restorable(users, accounts, user_id):
        pool.discard(candidate)
        raise BrokerAccountNotBoundError()

    try:
        for symbol in sorted(core_intents.active_or_scheduled_symbols_by_owner(user_id)):
            candidate.provider.subscribe(symbol)
        accounts.mark_login_ok(user_id, now=now)
        db.commit()
    except Exception as exc:
        pool.discard(candidate)
        try:
            db.rollback()
        except Exception as rollback_exc:
            logger.warning("rollback after failed restore raised %s user_id=%s", type(rollback_exc).__name__, user_id)
        code: BrokerLoginFailureCode = "provider_unavailable" if isinstance(exc, QuoteProviderError) else "unknown"
        logger.warning("broker session restore failed %s user_id=%s code=%s", type(exc).__name__, user_id, code)
        _record_login_failure(db, accounts, user_id, code, now)
        raise

    try:
        return pool.activate(candidate)
    except Exception:
        pool.discard(candidate)
        raise


BoundUserRestoreOutcome = Literal["live", "failed", "unbound"]


def restore_bound_user(
    *,
    db: Session,
    accounts: BrokerAccountRepository,
    core_intents: TradeIntentCoreRepository,
    users: UserRepository,
    pool: BrokerSessionPool,
    user_id: UUID,
    now: datetime,
) -> BoundUserRestoreOutcome:
    """Stored binding -> live session, for callers that must carry on regardless
    (startup, reactivation, the reconnect loop). Broker and setup failures are
    recorded on the row and reported as `failed`; a blob the current key cannot
    open is `credentials_unreadable` (admin re-binds). A disabled user or a
    missing row is `unbound` — checked before the broker is touched and again
    under the token. A replaced session is retired here. Only a database that
    cannot be read at all propagates."""
    if not _restorable(users, accounts, user_id):
        return "unbound"
    try:
        credentials = accounts.get_credentials(user_id)
    except BrokerCredentialKeyError as exc:
        logger.warning("broker credentials unreadable reason=%s user_id=%s", exc.reason, user_id)
        _record_login_failure(db, accounts, user_id, "credentials_unreadable", now)
        return "failed"
    if credentials is None:
        return "unbound"
    try:
        replaced = restore_broker_session(
            db=db,
            accounts=accounts,
            core_intents=core_intents,
            users=users,
            pool=pool,
            user_id=user_id,
            credentials=credentials,
            now=now,
        )
    except BrokerAccountNotBoundError:
        return "unbound"
    except Exception as exc:
        logger.warning("broker session restore failed %s user_id=%s", type(exc).__name__, user_id)
        return "failed"
    if replaced is not None:
        retire_broker_session(replaced, user_id)
    return "live"


def restore_all_bound_users(
    *,
    session_factory: Callable[[], Session],
    accounts_for: Callable[[Session], BrokerAccountRepository],
    core_intents_for: Callable[[Session], TradeIntentCoreRepository],
    users_for: Callable[[Session], UserRepository],
    pool: BrokerSessionPool,
    session_service: TradingSessionService,
    now: datetime,
) -> None:
    """Startup: one login per restorable binding, all at once. Nobody bound is a
    valid state (the admin binds the first user after boot); one user failing
    never stops the rest.

    More restorable bindings than `BROKER_MAX_SESSIONS` (the limit was lowered, or
    users were bound while others were disabled): the oldest bindings get the
    slots, deterministically, and the overflow is marked `session_pool_full`
    without touching the broker — the reconnect loop logs them in as slots free up.

    Parallel because each login costs ~3 s at the broker and the cap goes up to
    10: sequential would hold the app off the network for the whole sum, parallel
    for one login. A SQLAlchemy Session is not thread-safe, so every worker opens
    its own through `session_factory`; the pool itself is thread-safe.
    """
    with session_factory() as db:
        # Expire day intents from past trading days first: each session subscribes
        # its owner's open symbols, and nothing unsubscribes an expired one later.
        IntentLifecycleCommand(core_intents_for(db), session_service).run()
        accounts, users = accounts_for(db), users_for(db)
        restorable = [a.user_id for a in accounts.list_all() if _restorable(users, accounts, a.user_id)]
        to_login, overflow = restorable[: pool.free_slots()], restorable[pool.free_slots() :]
        for user_id in overflow:
            logger.warning("broker session startup user_id=%s outcome=session_pool_full", user_id)
            _record_login_failure(db, accounts, user_id, "session_pool_full", now)
    if not to_login:
        return

    def restore_one(user_id: UUID) -> BoundUserRestoreOutcome:
        with session_factory() as db:
            return restore_bound_user(
                db=db,
                accounts=accounts_for(db),
                core_intents=core_intents_for(db),
                users=users_for(db),
                pool=pool,
                user_id=user_id,
                now=now,
            )

    with ThreadPoolExecutor(max_workers=len(to_login), thread_name_prefix="broker-login") as workers:
        for user_id, outcome in zip(to_login, workers.map(restore_one, to_login), strict=True):
            logger.info("broker session startup user_id=%s outcome=%s", user_id, outcome)
