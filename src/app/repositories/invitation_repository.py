"""Persistence for invitations. No method commits — the caller owns the tx so the
user row + invitation row land together."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models.auth import Invitation
from app.domain.auth import InvitationData


def _to_domain(row: Invitation) -> InvitationData:
    return InvitationData(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        revoked_at=row.revoked_at,
    )


class InvitationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
        created_by_admin_id: UUID,
    ) -> UUID:
        invitation_id = uuid4()
        self._db.add(
            Invitation(
                id=invitation_id,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                created_by_admin_id=created_by_admin_id,
            )
        )
        self._db.flush()
        return invitation_id

    def find_by_hash(self, token_hash: str) -> InvitationData | None:
        row = self._db.execute(select(Invitation).where(Invitation.token_hash == token_hash)).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    def consume(self, invitation_id: UUID, *, now: datetime) -> None:
        self._db.execute(
            update(Invitation)
            .where(Invitation.id == invitation_id, Invitation.consumed_at.is_(None))
            .values(consumed_at=now),
            execution_options={"synchronize_session": False},
        )

    def revoke_active_for_user(self, user_id: UUID, *, now: datetime) -> None:
        """Revoke the user's live invitation (if any) so a fresh one can be issued
        without violating the one-live-invitation partial unique index."""
        self._db.execute(
            update(Invitation)
            .where(
                Invitation.user_id == user_id,
                Invitation.consumed_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .values(revoked_at=now),
            execution_options={"synchronize_session": False},
        )
