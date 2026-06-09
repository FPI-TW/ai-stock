from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.core import Symbol, TradeIntent, TwapSlice
from app.domain.price import SecurityType
from app.domain.trade_intent import (
    CANCELLABLE_STATUSES,
    TERMINAL_STATUSES,
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
    TradeIntentData,
    TwapSliceData,
)
from app.domain.twap import TWAP_STRATEGY, TwapDuplicateActivePlanError, TwapPlan


def _to_domain(row: TradeIntent, security_type: SecurityType) -> TradeIntentData:
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
        transaction_mode=row.transaction_mode,
        notification_mode=row.notification_mode,
        filled_quantity_lots=row.filled_quantity_lots,
        last_fill_at=row.last_fill_at,
        trail_mode=row.trail_mode,
        trail_value=row.trail_value,
        baseline=row.baseline,
        dynamic_trigger_price=row.dynamic_trigger_price,
        baseline_updated_at=row.baseline_updated_at,
        security_type=security_type,
        position_side=row.position_side,
        twap_interval_seconds=row.twap_interval_seconds,
        twap_end_time=row.twap_end_time,
        twap_start_at=row.twap_start_at,
        twap_end_at=row.twap_end_at,
        twap_available_slice_count=row.twap_available_slice_count,
        twap_materialized_slice_count=row.twap_materialized_slice_count,
    )


def _twap_slice_to_domain(row: TwapSlice) -> TwapSliceData:
    return TwapSliceData(
        id=row.id,
        trade_intent_id=row.trade_intent_id,
        owner_user_id=row.owner_user_id,
        symbol=row.symbol,
        sequence_no=row.sequence_no,
        scheduled_at=row.scheduled_at,
        planned_quantity_lots=row.planned_quantity_lots,
        status=row.status,
        primary_notification_id=row.primary_notification_id,
        notified_at=row.notified_at,
        primary_price_available=row.primary_price_available,
        primary_reference_price=row.primary_reference_price,
        primary_reference_price_type=row.primary_reference_price_type,
        primary_quote_time=row.primary_quote_time,
        price_followup_required=row.price_followup_required,
        price_followup_attempts=row.price_followup_attempts,
        next_price_followup_at=row.next_price_followup_at,
        price_followup_notification_id=row.price_followup_notification_id,
        price_followup_sent_at=row.price_followup_sent_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _nullable_equals(column: object, value: object) -> ColumnElement[bool]:
    comparable = cast(ColumnElement[object], column)
    if value is None:
        return comparable.is_(None)
    return comparable == value


def _select_intent_with_symbol_type() -> Select[tuple[TradeIntent, str]]:
    return select(TradeIntent, Symbol.instrument_type).join(Symbol, TradeIntent.symbol == Symbol.symbol)


class IntentRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def commit(self) -> None:
        """Commit pending repository writes.

        Most command paths own their transaction boundaries directly. This is
        for system/dev evaluator paths that batch baseline updates and trigger
        writes from the same quote snapshot into one transaction.
        """

        self._db.commit()

    def begin_nested(self) -> SessionTransaction:
        """Open a savepoint on the repository session."""

        return self._db.begin_nested()

    def create(
        self,
        owner_user_id: UUID,
        symbol: str,
        strategy: str,
        quantity_lots: int,
        target_price_original: Decimal | None,
        target_price_effective: Decimal | None,
        trigger_reference_price_type: str,
        trading_date: date,
        time_in_force: str,
        execution_mode: str,
        status: str,
        transaction_mode: str = "single_notification",
        notification_mode: str = "single",
        trail_mode: str | None = None,
        trail_value: Decimal | None = None,
        baseline: Decimal | None = None,
        dynamic_trigger_price: Decimal | None = None,
        baseline_updated_at: datetime | None = None,
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
                    _nullable_equals(TradeIntent.target_price_effective, target_price_effective),
                    _nullable_equals(TradeIntent.trail_mode, trail_mode),
                    _nullable_equals(TradeIntent.trail_value, trail_value),
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
            transaction_mode=transaction_mode,
            notification_mode=notification_mode,
            trail_mode=trail_mode,
            trail_value=trail_value,
            baseline=baseline,
            dynamic_trigger_price=dynamic_trigger_price,
            baseline_updated_at=baseline_updated_at,
        )
        self._db.add(row)
        try:
            self._db.flush()
        except IntegrityError as exc:
            raise DuplicateIntentError(owner_user_id, symbol, strategy) from exc
        return row.id

    def create_twap(
        self,
        *,
        owner_user_id: UUID,
        symbol: str,
        twap_plan: TwapPlan,
        execution_mode: str,
        time_in_force: str,
        status: str,
        trigger_reference_price_type: str,
    ) -> UUID:
        duplicate = self._db.execute(
            select(TradeIntent).where(
                TradeIntent.owner_user_id == owner_user_id,
                TradeIntent.symbol == symbol,
                TradeIntent.strategy == TWAP_STRATEGY,
                TradeIntent.position_side == twap_plan.position_side,
                TradeIntent.trading_date == twap_plan.trading_date,
                TradeIntent.status.in_(CANCELLABLE_STATUSES),
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise TwapDuplicateActivePlanError(owner_user_id, symbol, twap_plan.position_side)

        intent_id = uuid4()
        row = TradeIntent(
            id=intent_id,
            owner_user_id=owner_user_id,
            symbol=symbol,
            strategy=TWAP_STRATEGY,
            execution_mode=execution_mode,
            quantity_lots=twap_plan.target_quantity_lots,
            target_price_original=None,
            target_price_effective=None,
            trigger_reference_price_type=trigger_reference_price_type,
            trading_date=twap_plan.trading_date,
            time_in_force=time_in_force,
            status=status,
            transaction_mode="single_notification",
            notification_mode="single",
            position_side=twap_plan.position_side,
            twap_interval_seconds=twap_plan.interval_seconds,
            twap_end_time=twap_plan.requested_end_time,
            twap_start_at=twap_plan.start_at,
            twap_end_at=twap_plan.end_at,
            twap_available_slice_count=twap_plan.available_slice_count,
            twap_materialized_slice_count=twap_plan.materialized_slice_count,
        )
        self._db.add(row)
        for slice_plan in twap_plan.slices:
            self._db.add(
                TwapSlice(
                    id=uuid4(),
                    trade_intent_id=intent_id,
                    owner_user_id=owner_user_id,
                    symbol=symbol,
                    sequence_no=slice_plan.sequence_no,
                    scheduled_at=slice_plan.scheduled_at,
                    planned_quantity_lots=slice_plan.planned_quantity_lots,
                    status="pending",
                )
            )
        try:
            self._db.flush()
        except IntegrityError as exc:
            raise TwapDuplicateActivePlanError(owner_user_id, symbol, twap_plan.position_side) from exc
        return intent_id

    def system_update_trailing_baseline(
        self,
        intent_id: UUID,
        baseline: Decimal | None,
        dynamic_trigger_price: Decimal | None,
        baseline_updated_at: datetime | None,
    ) -> None:
        values: dict[str, object | None] = {
            "baseline": baseline,
            "dynamic_trigger_price": dynamic_trigger_price,
            "updated_at": func.now(),
        }
        if baseline_updated_at is not None:
            values["baseline_updated_at"] = baseline_updated_at
        self._db.execute(update(TradeIntent).where(TradeIntent.id == intent_id).values(**values))

    def system_activate_scheduled_day_intents(self, trading_date: date, now: datetime) -> int:
        """Move scheduled day intents for `trading_date` into active monitoring."""

        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TradeIntent)
                .where(
                    TradeIntent.status == "scheduled",
                    TradeIntent.trading_date == trading_date,
                )
                .values(status="active", updated_at=now)
            ),
        )
        return int(result.rowcount or 0)

    def system_expire_day_intents_through(self, cutoff_date: date, now: datetime) -> int:
        """Expire open day intents whose trading date can no longer trigger."""

        expired_ids = list(
            self._db.execute(
                update(TradeIntent)
                .where(
                    TradeIntent.status.in_(CANCELLABLE_STATUSES),
                    TradeIntent.trading_date <= cutoff_date,
                )
                .values(status="expired", updated_at=now)
                .returning(TradeIntent.id)
            )
            .scalars()
            .all()
        )
        if expired_ids:
            self._db.execute(
                update(TwapSlice)
                .where(
                    TwapSlice.trade_intent_id.in_(expired_ids),
                    TwapSlice.status == "pending",
                )
                .values(
                    status="cancelled",
                    price_followup_required=False,
                    next_price_followup_at=None,
                    updated_at=now,
                ),
                execution_options={"synchronize_session": False},
            )
        return len(expired_ids)

    def find_by_id(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        result = self._db.execute(_select_intent_with_symbol_type().where(TradeIntent.id == intent_id)).one_or_none()
        if result is None:
            raise IntentNotFoundError(intent_id)
        row, instrument_type = result
        if row is None or row.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        return _to_domain(row, SecurityType(instrument_type))

    def list_twap_slices(self, intent_id: UUID, owner_user_id: UUID) -> list[TwapSliceData]:
        intent = self._db.execute(select(TradeIntent).where(TradeIntent.id == intent_id)).scalar_one_or_none()
        if intent is None or intent.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        rows = (
            self._db.execute(
                select(TwapSlice)
                .where(TwapSlice.trade_intent_id == intent_id, TwapSlice.owner_user_id == owner_user_id)
                .order_by(TwapSlice.sequence_no.asc())
            )
            .scalars()
            .all()
        )
        return [_twap_slice_to_domain(row) for row in rows]

    def list_by_owner(
        self,
        owner_user_id: UUID,
        statuses: list[str] | None,
        trading_date: date | None,
        cursor: str | None,
        page_size: int,
    ) -> tuple[list[TradeIntentData], str | None]:
        effective_statuses = set(statuses) if statuses else None
        is_terminal_only = effective_statuses is not None and effective_statuses.issubset(TERMINAL_STATUSES)

        stmt = _select_intent_with_symbol_type().where(TradeIntent.owner_user_id == owner_user_id)
        if statuses:
            stmt = stmt.where(TradeIntent.status.in_(statuses))
        if trading_date is not None:
            stmt = stmt.where(TradeIntent.trading_date == trading_date)

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

        rows = list(self._db.execute(stmt.limit(page_size + 1)).all())
        has_more = len(rows) > page_size
        page = rows[:page_size]

        next_cursor = str(page[-1][0].id) if has_more and page else None
        return [_to_domain(row, SecurityType(instrument_type)) for row, instrument_type in page], next_cursor

    def system_list_active_symbols(self) -> list[str]:
        """Return distinct symbols with at least one active intent (owner-agnostic).

        `system_` prefix marks this as an evaluator / dispatcher path that
        deliberately skips owner scoping. Never expose through user-facing API
        — owner authz is the caller's responsibility.
        """
        rows = (
            self._db.execute(select(TradeIntent.symbol).where(TradeIntent.status == "active").distinct())
            .scalars()
            .all()
        )
        return list(rows)

    def system_list_active_by_symbols(self, symbols: list[str]) -> list[TradeIntentData]:
        """Return all active intents whose symbol is in the given list (owner-agnostic).

        `system_` prefix — see `system_list_active_symbols` for the scoping caveat.
        """
        if not symbols:
            return []
        rows = self._db.execute(
            _select_intent_with_symbol_type().where(
                TradeIntent.status == "active",
                TradeIntent.symbol.in_(symbols),
            )
        ).all()
        return [_to_domain(row, SecurityType(instrument_type)) for row, instrument_type in rows]

    def cancel(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        """Set status='cancelled' and flush.

        Like `create`, no commit happens here — the caller owns the transaction so
        that subscription cleanup can run inside the same atomic unit.
        """

        result = self._db.execute(_select_intent_with_symbol_type().where(TradeIntent.id == intent_id)).one_or_none()
        if result is None:
            raise IntentNotFoundError(intent_id)
        row, instrument_type = result
        if row is None or row.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        # already cancelled: idempotent — return current state without error
        if row.status == "cancelled":
            return _to_domain(row, SecurityType(instrument_type))
        if row.status not in CANCELLABLE_STATUSES:
            raise CancelNotAllowedError(intent_id, row.status)

        self._db.execute(
            update(TradeIntent)
            .where(TradeIntent.id == intent_id)
            .values(status="cancelled", updated_at=func.now(), cancelled_at=func.now()),
            execution_options={"synchronize_session": False},
        )
        if row.strategy == TWAP_STRATEGY:
            self._db.execute(
                update(TwapSlice)
                .where(TwapSlice.trade_intent_id == intent_id, TwapSlice.status == "pending")
                .values(
                    status="cancelled",
                    price_followup_required=False,
                    next_price_followup_at=None,
                    updated_at=func.now(),
                ),
                execution_options={"synchronize_session": False},
            )
            self._db.execute(
                update(TwapSlice)
                .where(
                    TwapSlice.trade_intent_id == intent_id,
                    TwapSlice.price_followup_required.is_(True),
                    TwapSlice.price_followup_notification_id.is_(None),
                )
                .values(
                    price_followup_required=False,
                    next_price_followup_at=None,
                    updated_at=func.now(),
                ),
                execution_options={"synchronize_session": False},
            )
        # refresh within the same transaction so func.now() values are read back
        # without a separate roundtrip after commit
        self._db.refresh(row)
        return _to_domain(row, SecurityType(instrument_type))

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

    def cancel_active_for_owner(self, owner_user_id: UUID, *, status: str, now: datetime) -> int:
        """Bulk-cancel every active/scheduled intent for an owner (account-disable
        cascade). Returns the number of intents transitioned. No commit."""
        result = self._db.execute(
            update(TradeIntent)
            .where(
                TradeIntent.owner_user_id == owner_user_id,
                TradeIntent.status.in_(CANCELLABLE_STATUSES),
            )
            .values(status=status, cancelled_at=now, updated_at=now)
            .returning(TradeIntent.id),
            execution_options={"synchronize_session": False},
        )
        return len(result.all())
