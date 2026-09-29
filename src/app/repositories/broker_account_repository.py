"""Reads / writes on `broker_accounts`. No method commits — the caller owns the tx.

This is the only module that touches `credentials_encrypted`: it encrypts on
`upsert` and decrypts in `get_credentials`, so the Fernet key and the JSON layout
of the blob never leak into commands or the pool.
"""

import base64
import json
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.orm import Session

from app.core.mfa_crypto import decrypt_secret, encrypt_secret
from app.db.models.broker_account import BrokerAccount
from app.domain.broker_account import (
    LOGIN_FAILURE_MESSAGES,
    BrokerAccountData,
    BrokerCredentialKeyError,
    BrokerLoginFailureCode,
    FubonCredentials,
)


def _to_domain(row: BrokerAccount) -> BrokerAccountData:
    return BrokerAccountData(
        id=row.id,
        user_id=row.user_id,
        broker=row.broker,
        broker_account_no=row.broker_account_no,
        cert_expires_at=row.cert_expires_at,
        status=row.status,
        last_login_at=row.last_login_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class BrokerAccountRepository:
    def __init__(self, db: Session, *, encryption_key: str | None) -> None:
        self._db = db
        # None = not configured. Reads of non-secret columns still work; anything
        # that touches `credentials_encrypted` refuses instead of using a dev key.
        self._key = encryption_key

    def _require_key(self) -> str:
        if not self._key:
            raise BrokerCredentialKeyError("missing")
        return self._key

    def get_by_user_id(self, user_id: UUID) -> BrokerAccountData | None:
        row = self._db.execute(select(BrokerAccount).where(BrokerAccount.user_id == user_id)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def list_all(self) -> list[BrokerAccountData]:
        rows = self._db.execute(select(BrokerAccount).order_by(BrokerAccount.created_at)).scalars().all()
        return [_to_domain(row) for row in rows]

    def get_credentials(self, user_id: UUID) -> FubonCredentials | None:
        blob = self._db.execute(
            select(BrokerAccount.credentials_encrypted).where(BrokerAccount.user_id == user_id)
        ).scalar_one_or_none()
        if blob is None:
            return None
        key = self._require_key()
        try:
            payload = json.loads(decrypt_secret(key, blob))
            return FubonCredentials(
                personal_id=payload["personal_id"],
                password=payload["password"],
                cert_pfx=base64.b64decode(payload["cert_pfx_base64"]),
                cert_password=payload["cert_password"],
            )
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            # Rotated key, or a blob this code never wrote. Never let the raw
            # crypto error escape: callers (PR3 startup loop) handle it per user.
            raise BrokerCredentialKeyError("undecryptable") from exc

    def upsert(
        self,
        user_id: UUID,
        *,
        broker: str,
        credentials: FubonCredentials,
        broker_account_no: str,
        cert_expires_at: datetime,
        now: datetime,
    ) -> tuple[BrokerAccountData, bool]:
        """Replace the whole binding (re-binding is how a yearly cert renewal lands).
        Only called after a successful login, so the row starts / returns to `active`.

        Returns the projection and whether a row already existed. Every column the
        projection reads is set here on the client side (`TimestampMixin` has
        server defaults but no `onupdate`), so the caller needs no post-commit
        re-read to work around `expire_on_commit`."""
        blob = encrypt_secret(
            self._require_key(),
            json.dumps(
                {
                    "personal_id": credentials.personal_id,
                    "password": credentials.password,
                    "cert_pfx_base64": base64.b64encode(credentials.cert_pfx).decode("ascii"),
                    "cert_password": credentials.cert_password,
                }
            ),
        )
        existing = self._db.execute(select(BrokerAccount).where(BrokerAccount.user_id == user_id)).scalar_one_or_none()
        if existing is None:
            row = BrokerAccount(
                id=uuid4(),
                user_id=user_id,
                broker=broker,
                credentials_encrypted=blob,
                broker_account_no=broker_account_no,
                cert_expires_at=cert_expires_at,
                status="active",
                last_login_at=now,
                last_error=None,
                created_at=now,
                updated_at=now,
            )
            self._db.add(row)
        else:
            row = existing
            row.broker = broker
            row.credentials_encrypted = blob
            row.broker_account_no = broker_account_no
            row.cert_expires_at = cert_expires_at
            row.status = "active"
            row.last_login_at = now
            row.last_error = None
            row.updated_at = now
        self._db.flush()
        return _to_domain(row), existing is not None

    def delete(self, user_id: UUID) -> bool:
        result = cast(
            CursorResult[Any],
            self._db.execute(
                delete(BrokerAccount).where(BrokerAccount.user_id == user_id),
                execution_options={"synchronize_session": False},
            ),
        )
        return bool(result.rowcount)

    def mark_login_ok(self, user_id: UUID, *, now: datetime) -> None:
        self._db.execute(
            update(BrokerAccount)
            .where(BrokerAccount.user_id == user_id)
            .values(status="active", last_login_at=now, last_error=None, updated_at=now),
            execution_options={"synchronize_session": False},
        )

    def mark_login_failed(self, user_id: UUID, *, code: BrokerLoginFailureCode, now: datetime) -> None:
        """Only the whitelisted message for `code` is stored — never exception text."""
        self._db.execute(
            update(BrokerAccount)
            .where(BrokerAccount.user_id == user_id)
            .values(status="login_failed", last_error=LOGIN_FAILURE_MESSAGES[code], updated_at=now),
            execution_options={"synchronize_session": False},
        )
