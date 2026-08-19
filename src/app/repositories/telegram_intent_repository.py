"""Postgres persistence for Telegram's one shared group draft."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.db.models.core import TelegramIntentInteraction
from app.domain.telegram_intent import (
    CapabilityId,
    InteractionKind,
    InteractionStatus,
    TelegramIntentInteractionData,
)


def _to_data(row: TelegramIntentInteraction) -> TelegramIntentInteractionData:
    return TelegramIntentInteractionData(
        id=row.id,
        owner_user_id=row.owner_user_id,
        telegram_chat_id=row.telegram_chat_id,
        bot_message_id=row.bot_message_id,
        kind=cast(InteractionKind, row.kind),
        capability_id=cast(CapabilityId, row.capability_id),
        strategy=row.strategy,
        payload=dict(row.payload),
        missing_fields=list(row.missing_fields),
        clarification_attempt_count=row.clarification_attempt_count,
        status=cast(InteractionStatus, row.status),
        expires_at=row.expires_at,
        confirmed_at=row.confirmed_at,
        created_trade_intent_id=row.created_trade_intent_id,
    )


class TelegramIntentInteractionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def find_pending_for_chat(self, chat_id: str, *, for_update: bool = False) -> TelegramIntentInteractionData | None:
        statement = select(TelegramIntentInteraction).where(
            TelegramIntentInteraction.telegram_chat_id == chat_id,
            TelegramIntentInteraction.status.in_(["pending", "confirming"]),
        )
        if for_update:
            statement = statement.with_for_update()
        row = self._db.execute(statement).scalar_one_or_none()
        return _to_data(row) if row is not None else None

    def find_by_id(
        self,
        interaction_id: UUID,
        *,
        chat_id: str | None = None,
        for_update: bool = False,
    ) -> TelegramIntentInteractionData | None:
        conditions = [TelegramIntentInteraction.id == interaction_id]
        if chat_id is not None:
            conditions.append(TelegramIntentInteraction.telegram_chat_id == chat_id)
        statement = select(TelegramIntentInteraction).where(*conditions)
        if for_update:
            statement = statement.with_for_update()
        row = self._db.execute(statement).scalar_one_or_none()
        return _to_data(row) if row is not None else None

    def create(
        self,
        *,
        owner_user_id: UUID,
        telegram_chat_id: str,
        kind: InteractionKind,
        capability_id: CapabilityId,
        strategy: str,
        payload: dict[str, object],
        missing_fields: list[str],
        clarification_attempt_count: int,
        expires_at: datetime,
    ) -> TelegramIntentInteractionData:
        row = TelegramIntentInteraction(
            id=uuid4(),
            owner_user_id=owner_user_id,
            telegram_chat_id=telegram_chat_id,
            kind=kind,
            capability_id=capability_id,
            strategy=strategy,
            payload=payload,
            missing_fields=missing_fields,
            clarification_attempt_count=clarification_attempt_count,
            status="pending",
            expires_at=expires_at,
        )
        self._db.add(row)
        self._db.flush()
        return _to_data(row)

    def update_pending(
        self,
        interaction_id: UUID,
        *,
        chat_id: str,
        kind: InteractionKind,
        capability_id: CapabilityId,
        strategy: str,
        payload: dict[str, object],
        missing_fields: list[str],
        clarification_attempt_count: int,
        expires_at: datetime,
    ) -> TelegramIntentInteractionData | None:
        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TelegramIntentInteraction)
                .where(
                    TelegramIntentInteraction.id == interaction_id,
                    TelegramIntentInteraction.telegram_chat_id == chat_id,
                    TelegramIntentInteraction.status == "pending",
                )
                .values(
                    kind=kind,
                    capability_id=capability_id,
                    strategy=strategy,
                    payload=payload,
                    missing_fields=missing_fields,
                    clarification_attempt_count=clarification_attempt_count,
                    expires_at=expires_at,
                )
            ),
        )
        if result.rowcount != 1:
            return None
        return self.find_by_id(interaction_id, chat_id=chat_id)

    def attach_bot_message_id(self, interaction_id: UUID, *, chat_id: str, message_id: int) -> bool:
        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TelegramIntentInteraction)
                .where(
                    TelegramIntentInteraction.id == interaction_id,
                    TelegramIntentInteraction.telegram_chat_id == chat_id,
                )
                .values(bot_message_id=message_id)
            ),
        )
        return result.rowcount == 1

    def mark_status(self, interaction_id: UUID, status: InteractionStatus, *, chat_id: str | None = None) -> bool:
        conditions = [
            TelegramIntentInteraction.id == interaction_id,
            TelegramIntentInteraction.status == "pending",
        ]
        if chat_id is not None:
            conditions.append(TelegramIntentInteraction.telegram_chat_id == chat_id)
        result = cast(
            CursorResult[Any],
            self._db.execute(update(TelegramIntentInteraction).where(*conditions).values(status=status)),
        )
        return result.rowcount == 1

    def claim_confirming(self, interaction_id: UUID, *, chat_id: str) -> bool:
        """Atomically claim a confirmation before the core command commits.

        ``CreateTradeIntentCommand`` owns its transaction and commits internally.
        A plain row lock would therefore be released while the interaction still
        says ``pending``; this conditional transition closes that duplicate
        callback window.  A failed create rolls the claim back to ``pending``.
        """

        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TelegramIntentInteraction)
                .where(
                    TelegramIntentInteraction.id == interaction_id,
                    TelegramIntentInteraction.telegram_chat_id == chat_id,
                    TelegramIntentInteraction.status == "pending",
                )
                .values(status="confirming")
            ),
        )
        return result.rowcount == 1

    def mark_confirmed(
        self,
        interaction_id: UUID,
        *,
        chat_id: str,
        confirmed_at: datetime,
        trade_intent_id: UUID,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TelegramIntentInteraction)
                .where(
                    TelegramIntentInteraction.id == interaction_id,
                    TelegramIntentInteraction.telegram_chat_id == chat_id,
                    TelegramIntentInteraction.status == "confirming",
                )
                .values(
                    status="confirmed",
                    confirmed_at=confirmed_at,
                    created_trade_intent_id=trade_intent_id,
                )
            ),
        )
        return result.rowcount == 1
