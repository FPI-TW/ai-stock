"""Owner-agnostic reads on `users`. No method commits — the caller owns the tx."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models.auth import User
from app.domain.auth import UserData


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
        # email is citext, so the comparison is case-insensitive.
        row = self._db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def create_invited(self, *, email: str, role: str) -> UUID:
        """Create an invited (no-password) user. Caller must pre-check email uniqueness
        for a clean error; the DB unique index is the final guard."""
        user_id = uuid4()
        self._db.add(User(id=user_id, email=email, role=role, status="invited"))
        self._db.flush()
        return user_id

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
