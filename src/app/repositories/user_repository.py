"""Owner-agnostic reads on `users`. No method commits — the caller owns the tx."""

from uuid import UUID

from sqlalchemy import select
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
