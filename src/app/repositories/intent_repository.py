from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.core import TradeIntent
from app.domain.trade_intent import (
    CANCELLABLE_STATUSES,
    TERMINAL_STATUSES,
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    TradeIntentData,
)

# ForbiddenError is intentionally not used here: ownership mismatch is surfaced as
# IntentNotFoundError to avoid leaking whether the intent_id exists at all.


def _to_domain(row: TradeIntent) -> TradeIntentData:
    return TradeIntentData(
        id=row.id,
        owner_user_id=row.owner_user_id,
        symbol=row.symbol,
        strategy=row.strategy,
        execution_mode=row.execution_mode,
        quantity_lots=row.quantity_lots,
        target_price_original=row.target_price_original,
        target_price_effective=row.target_price_effective,
        trigger_reference_price_type=row.trigger_reference_price_type,
        trading_date=row.trading_date,
        time_in_force=row.time_in_force,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        cancelled_at=row.cancelled_at,
        triggered_at=row.triggered_at,
    )


class IntentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def create(
        self,
        owner_user_id: UUID,
        symbol: str,
        strategy: str,
        quantity_lots: int,
        target_price_original: Decimal,
        target_price_effective: Decimal,
        trigger_reference_price_type: str,
        trading_date: date,
        time_in_force: str,
        execution_mode: str,
        status: str,
    ) -> TradeIntentData:
        duplicate = self._db.execute(
            select(TradeIntent).where(
                and_(
                    TradeIntent.owner_user_id == owner_user_id,
                    TradeIntent.symbol == symbol,
                    TradeIntent.strategy == strategy,
                    TradeIntent.target_price_effective == target_price_effective,
                    TradeIntent.quantity_lots == quantity_lots,
                    TradeIntent.trading_date == trading_date,
                    TradeIntent.status.in_(CANCELLABLE_STATUSES),
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise DuplicateIntentError(owner_user_id, symbol, strategy)

        now = datetime.now(tz=UTC)
        row = TradeIntent(
            id=uuid4(),
            owner_user_id=owner_user_id,
            symbol=symbol,
            strategy=strategy,
            execution_mode=execution_mode,
            quantity_lots=quantity_lots,
            target_price_original=target_price_original,
            target_price_effective=target_price_effective,
            trigger_reference_price_type=trigger_reference_price_type,
            trading_date=trading_date,
            time_in_force=time_in_force,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self._db.add(row)
        try:
            self._db.commit()
            self._db.refresh(row)
        except IntegrityError as exc:
            self._db.rollback()
            raise DuplicateIntentError(owner_user_id, symbol, strategy) from exc
        return _to_domain(row)

    def find_by_id(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        row = self._db.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one_or_none()
        if row is None or row.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        return _to_domain(row)

    def list_by_owner(
        self,
        owner_user_id: UUID,
        statuses: list[str] | None,
        cursor: str | None,
        page_size: int,
    ) -> tuple[list[TradeIntentData], str | None]:
        effective_statuses = set(statuses) if statuses else None
        is_terminal_only = effective_statuses is not None and effective_statuses.issubset(TERMINAL_STATUSES)

        stmt = select(TradeIntent).where(TradeIntent.owner_user_id == owner_user_id)
        if statuses:
            stmt = stmt.where(TradeIntent.status.in_(statuses))

        if is_terminal_only:
            stmt = stmt.order_by(TradeIntent.updated_at.desc(), TradeIntent.id.asc())
        else:
            stmt = stmt.order_by(
                TradeIntent.trading_date.asc(),
                TradeIntent.created_at.desc(),
                TradeIntent.id.asc(),
            )

        if cursor:
            try:
                cursor_id = UUID(cursor)
            except ValueError:
                cursor_id = None

            if cursor_id is not None:
                anchor = self._db.execute(
                    select(TradeIntent).where(
                        TradeIntent.id == cursor_id,
                        TradeIntent.owner_user_id == owner_user_id,
                    )
                ).scalar_one_or_none()
                if anchor is not None:
                    if is_terminal_only:
                        stmt = stmt.where(
                            or_(
                                TradeIntent.updated_at < anchor.updated_at,
                                and_(TradeIntent.updated_at == anchor.updated_at, TradeIntent.id > anchor.id),
                            )
                        )
                    else:
                        stmt = stmt.where(
                            or_(
                                TradeIntent.trading_date > anchor.trading_date,
                                and_(
                                    TradeIntent.trading_date == anchor.trading_date,
                                    TradeIntent.created_at < anchor.created_at,
                                ),
                                and_(
                                    TradeIntent.trading_date == anchor.trading_date,
                                    TradeIntent.created_at == anchor.created_at,
                                    TradeIntent.id > anchor.id,
                                ),
                            )
                        )

        rows = list(self._db.execute(stmt.limit(page_size + 1)).scalars().all())
        has_more = len(rows) > page_size
        page = rows[:page_size]

        next_cursor = str(page[-1].id) if has_more and page else None
        return [_to_domain(r) for r in page], next_cursor

    def cancel(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        row = self._db.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one_or_none()
        if row is None or row.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        # already cancelled: idempotent — return current state without error
        if row.status == "cancelled":
            return _to_domain(row)
        if row.status not in CANCELLABLE_STATUSES:
            raise CancelNotAllowedError(intent_id, row.status)

        now = datetime.now(tz=UTC)
        row.status = "cancelled"
        row.updated_at = now
        row.cancelled_at = now
        self._db.commit()
        self._db.refresh(row)
        return _to_domain(row)
