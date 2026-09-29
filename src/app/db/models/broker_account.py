"""`broker_accounts`: one bound broker login per user (per-user session ticket).

`credentials_encrypted` is the Fernet-encrypted JSON of the login material; only
`BrokerAccountRepository` reads or writes it. `last_error` may only hold the
whitelisted safe messages from `app.domain.broker_account`.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.core import TimestampMixin


class BrokerAccount(TimestampMixin, Base):
    __tablename__ = "broker_accounts"
    __table_args__ = (
        CheckConstraint("broker IN ('fubon')", name="broker"),
        CheckConstraint("status IN ('active', 'login_failed')", name="status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    # One account per user for now; lifting to many-per-user only drops this unique.
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    broker: Mapped[str] = mapped_column(Text, nullable=False)
    credentials_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The securities account number the broker returned at login; display only.
    broker_account_no: Mapped[str] = mapped_column(Text, nullable=False)
    cert_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
