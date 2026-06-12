import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commands.trigger_intent import TriggerIntentInput, persist_trigger
from app.db.models.core import TradeIntent as TradeIntentRow
from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trade_intent import MARKET_ORDER_STRATEGIES, TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import quote_snapshot_to_jsonb
from app.repositories.intent_repository import IntentRepository
from app.services.kill_switch import KillSwitchProvider
from app.services.quote.base import QuoteProvider, QuoteProviderError, QuoteSnapshot, QuoteUnavailableError
from app.services.quote.current_price import CurrentPriceProvider
from app.services.quote.intent_reconciler import reconcile_after_terminal_transition, reconcile_on_create
from app.services.symbol import SymbolService

logger = logging.getLogger(__name__)

# V0.5 fixed values
_EXECUTION_MODE = "notify_only"
_TIME_IN_FORCE = "day"

# trigger price reference by strategy
_TRIGGER_REF: dict[str, str] = {
    "buy_price_alert": "ask",
    "sell_price_alert": "bid",
    "limit_buy_order": "ask",
    "limit_sell_order": "bid",
    "market_order": "ask",
    "market_buy_order": "ask",
    "market_sell_order": "bid",
    "trailing_stop_alert": "bid",
}


@dataclass(frozen=True)
class CreateTradeIntentInput:
    symbol: str
    strategy: str
    quantity_lots: int
    owner_user_id: UUID
    target_price: str | None = None  # kept as str to preserve decimal precision
    transaction_mode: str = "single_notification"
    notification_mode: str = "single"
    trail_mode: str | None = None
    trail_value: Decimal | None = None


@dataclass(frozen=True)
class CancelTradeIntentInput:
    intent_id: UUID
    owner_user_id: UUID


class CreateTradeIntentCommand:
    """Create a trade intent, reconcile its quote subscription, and optionally trigger.

    Single-transaction shape (review #2 follow-up):
    1. validate → flush intent (no commit). The flushed row is held by this
       session only — invisible to dispatcher / other readers until commit.
    2. `reconcile_on_create` — subscribe broker. Failure here rolls back the
       intent (still inside the same tx).
    3. If the intent landed `active` and the current quote already meets the
       condition (spec §15), inline `persist_trigger` writes
       `trigger_events` / updates `trade_intents.status` / writes
       `notifications`. No `SELECT ... FOR UPDATE` here: the row was just
       flushed by this session, so no other session can race us.
    4. `commit` — intent (+ optional trigger_event + notification) land in
       one atomic write. Any failure between flush and commit rolls back
       the entire create.
    5. `find_by_id` after commit to materialise server-side timestamps and
       the final status (active / triggered / scheduled).

    Quote unavailable on the immediate-trigger path is non-blocking
    (spec §15): the intent commits as `active`, a later quote will drive
    the trigger via the dispatcher or `/dev/evaluate-quotes`.
    """

    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: IntentRepository,
        quote_provider: QuoteProvider,
        evaluator: QuoteEvaluator,
        db: Session,
        kill_switch: KillSwitchProvider | None = None,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._evaluator = evaluator
        self._db = db
        self._kill_switch = kill_switch

    def execute(self, inp: CreateTradeIntentInput) -> TradeIntentData:
        try:
            # 1. validate symbol existence and tradability
            symbol_obj = self._symbol_service.get_tradable_symbol(inp.symbol)

            # 2. validate price / trailing settings via PriceService
            try:
                security_type = SecurityType(symbol_obj.instrument_type)
            except ValueError:
                raise InvalidTypeError(symbol_obj.instrument_type, "must be stock or etf") from None
            effective_price: Decimal | None = None
            trail_value: Decimal | None = None
            if inp.strategy == "trailing_stop_alert":
                if inp.trail_mode is None or inp.trail_value is None:
                    raise ValueError("trailing_stop_alert requires trail_mode and trail_value")
                trail_value = inp.trail_value
                if inp.trail_mode == "fixed_amount":
                    PriceService.validate_fixed_amount_tick(security_type, inp.trail_value)
            elif inp.strategy in MARKET_ORDER_STRATEGIES:
                effective_price = None
            else:
                if inp.target_price is None:
                    raise ValueError(f"{inp.strategy} requires target_price")
                effective_price = PriceService.validate(
                    PriceRequest(
                        type=security_type,
                        price=inp.target_price,
                        amount=inp.quantity_lots,
                    )
                )

            # 3. determine trading_date and initial status
            now = self._session_service.now_taipei()
            trading_date = self._session_service.get_day_intent_trading_date(now)
            initial_status = self._session_service.get_initial_day_intent_status(now)

            # 4. persist (flush only — no refresh; the domain object is
            #    materialised after the final commit via `find_by_id`)
            trigger_ref = _TRIGGER_REF.get(inp.strategy)
            if trigger_ref is None:
                raise ValueError(f"Unsupported strategy: {inp.strategy}")

            intent_id = self._intent_repo.create(
                owner_user_id=inp.owner_user_id,
                symbol=inp.symbol,
                strategy=inp.strategy,
                quantity_lots=inp.quantity_lots,
                target_price_original=effective_price,
                target_price_effective=effective_price,
                trigger_reference_price_type=trigger_ref,
                trading_date=trading_date,
                time_in_force=_TIME_IN_FORCE,
                execution_mode=_EXECUTION_MODE,
                status=initial_status,
                transaction_mode=inp.transaction_mode,
                notification_mode=inp.notification_mode,
                trail_mode=inp.trail_mode,
                trail_value=trail_value,
            )

            # 5. reconcile subscription before commit — fail here rolls back the intent
            reconcile_on_create(self._quote_provider, inp.symbol)

            # 6. immediate trigger if the intent is active and the current quote
            #    already meets the strategy condition (spec §15). Scheduled
            #    intents skip — they wait for the next-day activation path.
            #    Folded into this transaction so dispatcher can never observe
            #    an active-but-pre-trigger window.
            #
            #    Kill switch: when global trigger-halt is on, skip the inline
            #    trigger too (not just the dispatcher) — the intent still commits
            #    as active, but no TriggerEvent / notification is produced. Without
            #    this gate a create whose condition is already met would fire
            #    straight through the halt (spec §18: "不產 TriggerEvent / 不發通知").
            halted = self._kill_switch is not None and self._kill_switch.is_halted()
            if halted:
                pass
            elif inp.strategy in MARKET_ORDER_STRATEGIES and initial_status == "active":
                self._apply_inline_market_order_trigger(intent_id, inp.symbol)
            elif initial_status == "active":
                self._apply_inline_trigger_if_quote_met(intent_id, inp.symbol, now, security_type)

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # 7. materialise server-side values (created_at / updated_at, plus
        #    triggered_at if step 6 fired). Done after commit so a SELECT
        #    hiccup here can't corrupt a half-written write.
        return self._intent_repo.find_by_id(intent_id, inp.owner_user_id)

    def _apply_inline_trigger_if_quote_met(
        self,
        intent_id: UUID,
        symbol: str,
        now: datetime,
        security_type: SecurityType,
    ) -> None:
        """Evaluate current quote and, if condition met, write trigger rows in this tx.

        Runs before commit, so the just-flushed intent is still invisible to
        other sessions — no `SELECT ... FOR UPDATE` is needed. Quote
        unavailable is non-blocking: the create commits as `active` and a
        later quote drives the trigger.
        """

        try:
            quotes = self._quote_provider.get_quotes([symbol])
        except QuoteUnavailableError as exc:
            logger.warning(
                "create: quote unavailable for %s, intent %s stays active: %s",
                symbol,
                intent_id,
                exc,
            )
            return
        if not quotes:
            logger.warning(
                "create: quote unavailable for %s, intent %s stays active",
                symbol,
                intent_id,
            )
            return

        # Reload the ORM row from the session — the repo returned only the id,
        # so we re-select to drive `persist_trigger`. The row sits in the
        # session's identity map after the earlier flush; this SELECT hits the
        # local cache, no extra round-trip.
        intent_row = self._db.execute(select(TradeIntentRow).where(TradeIntentRow.id == intent_id)).scalar_one()
        intent_domain = TradeIntentData(
            id=intent_row.id,
            owner_user_id=intent_row.owner_user_id,
            symbol=intent_row.symbol,
            strategy=intent_row.strategy,
            execution_mode=intent_row.execution_mode,
            quantity_lots=intent_row.quantity_lots,
            target_price_original=intent_row.target_price_original,
            target_price_effective=intent_row.target_price_effective,
            trigger_reference_price_type=intent_row.trigger_reference_price_type,
            trading_date=intent_row.trading_date,
            time_in_force=intent_row.time_in_force,
            status=intent_row.status,
            # created_at / updated_at populated post-flush by server_default; not
            # consulted by the evaluator so we leave them as-is.
            created_at=intent_row.created_at,
            updated_at=intent_row.updated_at,
            transaction_mode=intent_row.transaction_mode,
            notification_mode=intent_row.notification_mode,
            filled_quantity_lots=intent_row.filled_quantity_lots,
            last_fill_at=intent_row.last_fill_at,
            trail_mode=intent_row.trail_mode,
            trail_value=intent_row.trail_value,
            baseline=intent_row.baseline,
            dynamic_trigger_price=intent_row.dynamic_trigger_price,
            baseline_updated_at=intent_row.baseline_updated_at,
            security_type=security_type,
        )

        quote = quotes[0]
        result = self._evaluator.evaluate(quote, intent_domain, now)
        if result.baseline_updated_at is not None:
            intent_row.baseline = result.baseline
            intent_row.dynamic_trigger_price = result.dynamic_trigger_price
            intent_row.baseline_updated_at = result.baseline_updated_at
        if not result.should_trigger:
            return

        # evaluator guarantees these are populated when should_trigger is True;
        # explicit check (not assert) so the invariant holds under `python -O`.
        if result.trigger_price is None or result.trigger_reference_price_type is None:
            raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")

        persist_trigger(
            self._db,
            intent_row,
            TriggerIntentInput(
                intent_id=intent_row.id,
                trigger_price=result.trigger_price,
                trigger_reference_price_type=result.trigger_reference_price_type,
                fallback_used=result.fallback_used,
                quote_snapshot=quote_snapshot_to_jsonb(quote),
                quote_time=quote.quote_time,
            ),
        )

    def _apply_inline_market_order_trigger(self, intent_id: UUID, symbol: str) -> None:
        try:
            quote = self._get_market_order_quote(symbol)
        except QuoteProviderError as exc:
            logger.warning(
                "create: market order quote unavailable for %s, intent %s stays active: %s",
                symbol,
                intent_id,
                exc,
            )
            return

        if quote.bid_price is None and quote.ask_price is None and quote.last_price is None:
            logger.warning(
                "create: market order quote has no usable price for %s, intent %s stays active",
                symbol,
                intent_id,
            )
            return

        intent_row = self._db.execute(select(TradeIntentRow).where(TradeIntentRow.id == intent_id)).scalar_one()
        is_sell_side = intent_row.strategy == "market_sell_order"
        trigger_price = quote.bid_price if is_sell_side else quote.ask_price
        trigger_reference_price_type = "bid" if is_sell_side else "ask"
        fallback_used = False
        if trigger_price is None:
            trigger_price = quote.last_price
            trigger_reference_price_type = "last_fallback"
            fallback_used = True
        if trigger_price is None or trigger_price <= 0:
            logger.warning(
                "create: market order quote has no usable price for %s, intent %s stays active",
                symbol,
                intent_id,
            )
            return

        persist_trigger(
            self._db,
            intent_row,
            TriggerIntentInput(
                intent_id=intent_row.id,
                trigger_price=trigger_price,
                trigger_reference_price_type=trigger_reference_price_type,
                fallback_used=fallback_used,
                quote_snapshot=quote_snapshot_to_jsonb(quote),
                quote_time=quote.quote_time,
            ),
        )

    def _get_market_order_quote(self, symbol: str) -> QuoteSnapshot:
        if isinstance(self._quote_provider, CurrentPriceProvider):
            try:
                return self._quote_provider.get_current_price(symbol)
            except QuoteProviderError:
                pass

        quotes = self._quote_provider.get_quotes([symbol])
        if not quotes:
            raise QuoteUnavailableError(symbol)
        return quotes[0]


class CancelTradeIntentCommand:
    """Cancel an intent and release its quote subscription when nothing else needs it.

    Transaction shape: cancel-flush → commit (Phase A, required); then
    best-effort subscription cleanup (Phase B). Any failure in Phase B leaves a
    stale subscription that the next startup reconcile sweeps up — the API call
    must still report cancel success because the DB write has already landed.
    """

    def __init__(
        self,
        intent_repo: IntentRepository,
        quote_provider: QuoteProvider,
        db: Session,
    ) -> None:
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._db = db

    def execute(self, inp: CancelTradeIntentInput) -> TradeIntentData:
        # Phase A: cancel SQL — must commit even if reconcile later fails.
        try:
            intent = self._intent_repo.cancel(inp.intent_id, inp.owner_user_id)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # Phase B: best-effort subscription cleanup. Catch broad Exception
        # (covers SQLAlchemyError from the residual-count query *and* anything
        # the provider unsubscribe path could raise) so the cancel API never
        # 500s on cleanup failure — the next startup reconcile will sweep any
        # stale broker subscription.
        try:
            reconcile_after_terminal_transition(self._quote_provider, self._intent_repo, intent.symbol)
        except Exception:
            logger.warning(
                "post-cancel reconcile failed; leaving cleanup to startup reconciler",
                extra={"symbol": intent.symbol, "intent_id": str(intent.id)},
                exc_info=True,
            )
        return intent
