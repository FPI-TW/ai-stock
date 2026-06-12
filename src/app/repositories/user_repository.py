"""Owner-agnostic reads on `users`. No method commits — the caller owns the tx."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models.auth import User
from app.domain.auth import UserData, normalize_email


def _to_domain(row: User) -> UserData:
    return UserData(
        id=row.id,
        email=row.email,
        role=row.role,
        status=row.status,
        mfa_enabled=row.mfa_enabled,
        password_hash=row.password_hash,
        terms_version_accepted=row.terms_version_accepted,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class UserRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, user_id: UUID) -> UserData | None:
        row = self._db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def get_by_email(self, email: str) -> UserData | None:
        # email is citext (case-insensitive), but does not strip whitespace; normalize
        # so the lookup key matches what create_invited stored and the bucket keys use.
        row = self._db.execute(select(User).where(User.email == normalize_email(email))).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def list_all(self) -> list[UserData]:
        """Aggregate user list for the admin dashboard (newest first)."""
        rows = self._db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
        return [_to_domain(row) for row in rows]

    def disable(self, user_id: UUID, *, now: datetime) -> None:
        self._db.execute(
            update(User).where(User.id == user_id).values(status="disabled", disabled_at=now, updated_at=now),
            execution_options={"synchronize_session": False},
        )

    def reactivate(self, user_id: UUID, *, now: datetime) -> None:
        """Flip a disabled user back to active and clear disabled_at. Old intents are
        NOT restored (spec §13) — the caller only changes status."""
        self._db.execute(
            update(User).where(User.id == user_id).values(status="active", disabled_at=None, updated_at=now),
            execution_options={"synchronize_session": False},
        )

    def get_mfa_secret_encrypted(self, user_id: UUID) -> bytes | None:
        return self._db.execute(select(User.mfa_secret_encrypted).where(User.id == user_id)).scalar_one_or_none()

    def set_mfa_secret(self, user_id: UUID, *, encrypted_secret: bytes, now: datetime) -> None:
        """Store a (re)generated TOTP secret. Does not enable MFA — that happens on
        the first successful verify."""
        self._db.execute(
            update(User).where(User.id == user_id).values(mfa_secret_encrypted=encrypted_secret, updated_at=now),
            execution_options={"synchronize_session": False},
        )

    def enable_mfa(self, user_id: UUID, *, now: datetime) -> None:
        self._db.execute(
            update(User).where(User.id == user_id).values(mfa_enabled=True, updated_at=now),
            execution_options={"synchronize_session": False},
        )

    def create_invited(self, *, email: str, role: str) -> UUID:
        """Create an invited (no-password) user. Caller must pre-check email uniqueness
        for a clean error; the DB unique index is the final guard."""
        user_id = uuid4()
        self._db.add(User(id=user_id, email=normalize_email(email), role=role, status="invited"))
        self._db.flush()
        return user_id

    def set_password(self, user_id: UUID, *, password_hash: str, now: datetime) -> None:
        """Replace the password hash (password-reset confirm)."""
        self._db.execute(
            update(User).where(User.id == user_id).values(password_hash=password_hash, updated_at=now),
            execution_options={"synchronize_session": False},
        )

    def activate(self, user_id: UUID, *, password_hash: str, terms_version: str, now: datetime) -> None:
        """Move an invited user to active, setting their first password + accepted terms."""
        self._db.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                password_hash=password_hash,
                status="active",
                terms_version_accepted=terms_version,
                terms_accepted_at=now,
                updated_at=now,
            ),
            execution_options={"synchronize_session": False},
        )
