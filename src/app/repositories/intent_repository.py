from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.core import TradeIntent
from app.domain.trade_intent import (
    CANCELLABLE_STATUSES,
    TERMINAL_STATUSES,
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
    TradeIntentData,
)


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
    ) -> UUID:
        """Add a new TradeIntent and flush; return its id.

        Transactional boundary: this repo neither commits nor refreshes — the
        caller owns the transaction and is responsible for materialising
        server-managed values (`created_at` / `updated_at`) via `find_by_id`
        after the final commit. Keeping refresh out of this method means the
        failure shape is simple: any error between flush and final commit
        (e.g. quote provider reconcile rejecting on allowlist / quota) rolls
        back cleanly without ever exposing a half-materialised domain object.

        On `IntegrityError`, raise `DuplicateIntentError` without rolling
        back; the caller's exception handler issues `rollback()` on the
        session before the next operation.
        """

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
        )
        self._db.add(row)
        try:
            self._db.flush()
        except IntegrityError as exc:
            raise DuplicateIntentError(owner_user_id, symbol, strategy) from exc
        return row.id

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
            cursor_id = UUID(cursor)  # format already validated by route layer
            anchor = self._db.execute(
                select(TradeIntent).where(
                    TradeIntent.id == cursor_id,
                    TradeIntent.owner_user_id == owner_user_id,
                )
            ).scalar_one_or_none()
            if anchor is None:
                raise InvalidCursorError(cursor_id)
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

    def list_active_symbols(self) -> list[str]:
        """Return distinct symbols with at least one active intent (owner-agnostic).

        Intended for system evaluator paths (e.g. `/dev/evaluate-quotes`),
        not for user-facing API.
        """
        rows = (
            self._db.execute(select(TradeIntent.symbol).where(TradeIntent.status == "active").distinct())
            .scalars()
            .all()
        )
        return list(rows)

    def list_active_by_symbols(self, symbols: list[str]) -> list[TradeIntentData]:
        """Return all active intents whose symbol is in the given list (owner-agnostic)."""
        if not symbols:
            return []
        rows = (
            self._db.execute(
                select(TradeIntent).where(
                    TradeIntent.status == "active",
                    TradeIntent.symbol.in_(symbols),
                )
            )
            .scalars()
            .all()
        )
        return [_to_domain(r) for r in rows]

    def cancel(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        """Set status='cancelled' and flush.

        Like `create`, no commit happens here — the caller owns the transaction so
        that subscription cleanup can run inside the same atomic unit.
        """

        row = self._db.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one_or_none()
        if row is None or row.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        # already cancelled: idempotent — return current state without error
        if row.status == "cancelled":
            return _to_domain(row)
        if row.status not in CANCELLABLE_STATUSES:
            raise CancelNotAllowedError(intent_id, row.status)

        self._db.execute(
            update(TradeIntent)
            .where(TradeIntent.id == intent_id)
            .values(status="cancelled", updated_at=func.now(), cancelled_at=func.now()),
            execution_options={"synchronize_session": False},
        )
        # refresh within the same transaction so func.now() values are read back
        # without a separate roundtrip after commit
        self._db.refresh(row)
        return _to_domain(row)

    # ------------------------------------------------------------------
    # quote subscription reconciliation helpers
    # ------------------------------------------------------------------

    def active_or_scheduled_symbols(self) -> set[str]:
        """Distinct set of symbols across every non-terminal intent.

        Called at app startup to seed the quote provider's subscription set.
        """

        stmt = select(TradeIntent.symbol).where(TradeIntent.status.in_(CANCELLABLE_STATUSES)).distinct()
        return {row for (row,) in self._db.execute(stmt).all()}

    def count_active_or_scheduled_for_symbol(self, symbol: str) -> int:
        """Count non-terminal intents on `symbol` across all owners.

        Subscriptions are broker-wide, not per-owner, so an unsubscribe must only
        fire when **no** active intent (from any owner) still wants the symbol.
        """

        stmt = (
            select(func.count())
            .select_from(TradeIntent)
            .where(
                TradeIntent.symbol == symbol,
                TradeIntent.status.in_(CANCELLABLE_STATUSES),
            )
        )
        return int(self._db.execute(stmt).scalar_one())
