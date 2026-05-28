from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.db.models.core import Notification, TradeIntent, TwapSlice
from app.domain.price import InvalidTypeError, SecurityType
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import TradingDayPhase, TradingSessionService
from app.domain.twap import (
    TWAP_EXECUTION_MODE,
    TWAP_PRICE_FOLLOWUP_DELAY_SECONDS,
    TWAP_PRICE_FOLLOWUP_MAX_ATTEMPTS,
    TWAP_STRATEGY,
    TWAP_TIME_IN_FORCE,
    TWAP_TRIGGER_REFERENCE_PRICE_TYPE,
    TwapPlan,
    build_twap_plan,
)
from app.repositories.intent_repository import IntentRepository
from app.services.notification_template import render_twap_price_followup, render_twap_slice
from app.services.quote.base import QuoteProvider, QuoteProviderError, QuoteSnapshot
from app.services.quote.intent_reconciler import reconcile_on_create
from app.services.symbol import SymbolService


@dataclass(frozen=True)
class TwapPlanInput:
    symbol: str
    position_side: str
    quantity_lots: int
    interval_seconds: int
    start_time: time | None
    end_time: time
    owner_user_id: UUID


@dataclass(frozen=True)
class TwapConfirmOutput:
    intent: TradeIntentData


@dataclass(frozen=True)
class TwapWorkerOutput:
    processed_count: int


class TwapPlanCommand:
    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service

    def preview(self, inp: TwapPlanInput) -> TwapPlan:
        self._validate_symbol(inp.symbol)
        return self._build_plan(inp)

    def _build_plan(self, inp: TwapPlanInput) -> TwapPlan:
        return build_twap_plan(
            position_side=inp.position_side,
            quantity_lots=inp.quantity_lots,
            interval_seconds=inp.interval_seconds,
            start_time=inp.start_time,
            end_time=inp.end_time,
            now=self._session_service.now_taipei(),
            session_service=self._session_service,
        )

    def _validate_symbol(self, symbol: str) -> None:
        symbol_obj = self._symbol_service.get_tradable_symbol(symbol)
        try:
            SecurityType(symbol_obj.instrument_type)
        except ValueError:
            raise InvalidTypeError(symbol_obj.instrument_type, "must be stock or etf") from None


class TwapConfirmCommand(TwapPlanCommand):
    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: IntentRepository,
        quote_provider: QuoteProvider,
        db: Session,
    ) -> None:
        super().__init__(symbol_service, session_service)
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._db = db

    def execute(self, inp: TwapPlanInput) -> TwapConfirmOutput:
        try:
            self._validate_symbol(inp.symbol)
            plan = self._build_plan(inp)
            initial_status = "active" if plan.trading_phase == TradingDayPhase.REGULAR_SESSION else "scheduled"
            intent_id = self._intent_repo.create_twap(
                owner_user_id=inp.owner_user_id,
                symbol=inp.symbol,
                twap_plan=plan,
                execution_mode=TWAP_EXECUTION_MODE,
                time_in_force=TWAP_TIME_IN_FORCE,
                status=initial_status,
                trigger_reference_price_type=TWAP_TRIGGER_REFERENCE_PRICE_TYPE,
            )
            reconcile_on_create(self._quote_provider, inp.symbol)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return TwapConfirmOutput(intent=self._intent_repo.find_by_id(intent_id, inp.owner_user_id))


class TwapSliceWorkerCommand:
    def __init__(
        self,
        db: Session,
        quote_provider: QuoteProvider,
        session_service: TradingSessionService,
    ) -> None:
        self._db = db
        self._quote_provider = quote_provider
        self._session_service = session_service

    def process_due_slices(self, *, limit: int = 100) -> TwapWorkerOutput:
        now = self._session_service.now_taipei()
        IntentLifecycleCommand(IntentRepository(self._db), self._session_service).run()
        if self._session_service.get_trading_day_phase(now) != TradingDayPhase.REGULAR_SESSION:
            return TwapWorkerOutput(processed_count=0)
        try:
            rows = self._load_due_slices(now, limit)
            for slice_row, intent_row in rows:
                self._process_due_slice(slice_row, intent_row, now)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return TwapWorkerOutput(processed_count=len(rows))

    def process_price_followups(self, *, limit: int = 100) -> TwapWorkerOutput:
        now = self._session_service.now_taipei()
        IntentLifecycleCommand(IntentRepository(self._db), self._session_service).run()
        if self._session_service.get_trading_day_phase(now) != TradingDayPhase.REGULAR_SESSION:
            return TwapWorkerOutput(processed_count=0)
        try:
            rows = self._load_followup_slices(now, limit)
            for slice_row, intent_row in rows:
                self._process_followup(slice_row, intent_row, now)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return TwapWorkerOutput(processed_count=len(rows))

    def _load_due_slices(self, now: datetime, limit: int) -> list[tuple[TwapSlice, TradeIntent]]:
        stmt = (
            select(TwapSlice, TradeIntent)
            .join(TradeIntent, TwapSlice.trade_intent_id == TradeIntent.id)
            .where(
                TwapSlice.status == "pending",
                TwapSlice.scheduled_at <= now,
                TradeIntent.strategy == TWAP_STRATEGY,
                TradeIntent.status.in_(("scheduled", "active")),
            )
            .order_by(TwapSlice.scheduled_at.asc(), TwapSlice.sequence_no.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self._db.execute(stmt).tuples().all())

    def _load_followup_slices(self, now: datetime, limit: int) -> list[tuple[TwapSlice, TradeIntent]]:
        stmt = (
            select(TwapSlice, TradeIntent)
            .join(TradeIntent, TwapSlice.trade_intent_id == TradeIntent.id)
            .where(
                TwapSlice.price_followup_required.is_(True),
                TwapSlice.price_followup_notification_id.is_(None),
                TwapSlice.price_followup_attempts < TWAP_PRICE_FOLLOWUP_MAX_ATTEMPTS,
                TwapSlice.next_price_followup_at <= now,
                TradeIntent.strategy == TWAP_STRATEGY,
                TradeIntent.status != "cancelled",
            )
            .order_by(TwapSlice.next_price_followup_at.asc(), TwapSlice.sequence_no.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self._db.execute(stmt).tuples().all())

    def _process_due_slice(self, slice_row: TwapSlice, intent_row: TradeIntent, now: datetime) -> None:
        price, price_type, quote_time = self._get_reference_price(intent_row.symbol, intent_row.position_side)
        title, body = render_twap_slice(
            symbol=intent_row.symbol,
            position_side=_required_position_side(intent_row),
            sequence_no=slice_row.sequence_no,
            total_slices=_required_materialized_slice_count(intent_row),
            planned_quantity_lots=slice_row.planned_quantity_lots,
            reference_price=price,
            reference_price_type=price_type,
            quote_time=quote_time,
        )
        notification = Notification(
            id=uuid4(),
            owner_user_id=slice_row.owner_user_id,
            trade_intent_id=slice_row.trade_intent_id,
            type="twap_slice",
            rendered_title=title,
            rendered_body=body,
        )
        self._db.add(notification)
        self._db.flush()

        followup_required = price is None
        slice_row.status = "notified"
        slice_row.primary_notification_id = notification.id
        slice_row.notified_at = now
        slice_row.primary_price_available = price is not None
        slice_row.primary_reference_price = price
        slice_row.primary_reference_price_type = price_type
        slice_row.primary_quote_time = quote_time
        slice_row.price_followup_required = followup_required
        slice_row.next_price_followup_at = (
            now + timedelta(seconds=TWAP_PRICE_FOLLOWUP_DELAY_SECONDS) if followup_required else None
        )
        slice_row.updated_at = now
        if intent_row.status == "scheduled":
            intent_row.status = "active"
            intent_row.updated_at = now
        self._complete_intent_if_all_slices_done(intent_row, now)

    def _process_followup(self, slice_row: TwapSlice, intent_row: TradeIntent, now: datetime) -> None:
        attempts = slice_row.price_followup_attempts + 1
        price, price_type, _quote_time = self._get_reference_price(intent_row.symbol, intent_row.position_side)
        slice_row.price_followup_attempts = attempts
        slice_row.updated_at = now
        if price is None or price_type is None:
            if attempts >= TWAP_PRICE_FOLLOWUP_MAX_ATTEMPTS:
                slice_row.price_followup_required = False
                slice_row.next_price_followup_at = None
            else:
                slice_row.next_price_followup_at = now + timedelta(seconds=TWAP_PRICE_FOLLOWUP_DELAY_SECONDS)
            return

        title, body = render_twap_price_followup(
            symbol=intent_row.symbol,
            sequence_no=slice_row.sequence_no,
            total_slices=_required_materialized_slice_count(intent_row),
            reference_price=price,
            reference_price_type=price_type,
            sent_at=now,
        )
        notification = Notification(
            id=uuid4(),
            owner_user_id=slice_row.owner_user_id,
            trade_intent_id=slice_row.trade_intent_id,
            type="twap_price_followup",
            rendered_title=title,
            rendered_body=body,
        )
        self._db.add(notification)
        self._db.flush()
        slice_row.price_followup_required = False
        slice_row.next_price_followup_at = None
        slice_row.price_followup_notification_id = notification.id
        slice_row.price_followup_sent_at = now

    def _complete_intent_if_all_slices_done(self, intent_row: TradeIntent, now: datetime) -> None:
        pending_count = self._db.execute(
            select(func.count())
            .select_from(TwapSlice)
            .where(
                TwapSlice.trade_intent_id == intent_row.id,
                TwapSlice.status == "pending",
            )
        ).scalar_one()
        if int(pending_count) == 0 and intent_row.status in {"scheduled", "active"}:
            intent_row.status = "triggered"
            intent_row.triggered_at = now
            intent_row.updated_at = now

    def _get_reference_price(
        self,
        symbol: str,
        position_side: str | None,
    ) -> tuple[Decimal | None, str | None, datetime | None]:
        try:
            snapshots = self._quote_provider.get_quotes([symbol])
        except QuoteProviderError:
            return None, None, None
        if not snapshots:
            return None, None, None
        return _select_reference_price(snapshots[0], position_side)


def _select_reference_price(
    snapshot: QuoteSnapshot,
    position_side: str | None,
) -> tuple[Decimal | None, str | None, datetime | None]:
    if position_side == "long" and snapshot.ask_price is not None:
        return snapshot.ask_price, "ask", snapshot.quote_time
    if position_side == "short" and snapshot.bid_price is not None:
        return snapshot.bid_price, "bid", snapshot.quote_time
    if snapshot.last_price is not None:
        return snapshot.last_price, "last_fallback", snapshot.quote_time
    return None, None, None


def _required_position_side(intent_row: TradeIntent) -> str:
    if intent_row.position_side is None:
        raise RuntimeError(f"TWAP intent missing position_side: {intent_row.id}")
    return intent_row.position_side


def _required_materialized_slice_count(intent_row: TradeIntent) -> int:
    if intent_row.twap_materialized_slice_count is None:
        raise RuntimeError(f"TWAP intent missing slice count: {intent_row.id}")
    return intent_row.twap_materialized_slice_count
