"""Persistence for the refresh-token rotation chain. No method commits — the
caller (LoginCommand / RefreshCommand / LogoutCommand) owns the transaction so
rotation + revocation land atomically.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models.auth import RefreshToken
from app.domain.auth import RefreshTokenData


def _to_domain(row: RefreshToken) -> RefreshTokenData:
    return RefreshTokenData(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        parent_token_id=row.parent_token_id,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        revoked_reason=row.revoked_reason,
        mfa_verified=row.mfa_verified,
    )


class RefreshTokenRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
        parent_token_id: UUID | None = None,
        user_agent: str | None = None,
        ip: str | None = None,
        mfa_verified: bool = False,
    ) -> UUID:
        token_id = uuid4()
        self._db.add(
            RefreshToken(
                id=token_id,
                user_id=user_id,
                token_hash=token_hash,
                parent_token_id=parent_token_id,
                expires_at=expires_at,
                user_agent=user_agent,
                ip=ip,
                mfa_verified=mfa_verified,
            )
        )
        self._db.flush()
        return token_id

    def mark_mfa_verified(self, token_id: UUID) -> None:
        """Flag a session's refresh token as 2FA-cleared (admin verify step-up)."""
        self._db.execute(
            update(RefreshToken).where(RefreshToken.id == token_id).values(mfa_verified=True),
            execution_options={"synchronize_session": False},
        )

    def find_by_hash(self, token_hash: str) -> RefreshTokenData | None:
        row = self._db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def get_by_id(self, token_id: UUID) -> RefreshTokenData | None:
        """Look up a session's refresh token by id. The access token carries this id as
        its `session_id`, so the auth layer can re-check whether the session is still
        live (not logged out / disabled / password-reset) on each request."""
        row = self._db.execute(select(RefreshToken).where(RefreshToken.id == token_id)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def revoke(self, token_id: UUID, *, reason: str, now: datetime) -> None:
        """Revoke a single token if not already revoked (keeps the original reason)."""
        self._db.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason),
            execution_options={"synchronize_session": False},
        )

    def revoke_all_active_for_user(self, user_id: UUID, *, reason: str, now: datetime) -> int:
        """Revoke every still-active token for a user (logout-all / reuse / disable).
        Returns the number of tokens revoked (via RETURNING, so it is mypy-clean)."""
        result = self._db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
            .returning(RefreshToken.id),
            execution_options={"synchronize_session": False},
        )
        return len(result.all())
