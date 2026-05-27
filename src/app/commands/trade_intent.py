import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commands.trigger_intent import TriggerIntentInput, persist_trigger
from app.db.models.core import TradeIntent as TradeIntentRow
from app.domain.price import (
    InvalidTickSizeError,
    InvalidTypeError,
    PriceRequest,
    PriceService,
    SecurityType,
)
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import quote_snapshot_to_jsonb
from app.repositories.intent_repository import IntentRepository
from app.services.quote.base import QuoteProvider, QuoteUnavailableError
from app.services.quote.intent_reconciler import reconcile_after_terminal_transition, reconcile_on_create
from app.services.symbol import SymbolService

logger = logging.getLogger(__name__)

# V0.5 fixed values
_EXECUTION_MODE = "notify_only"
_TIME_IN_FORCE = "day"

# trigger price reference by strategy. For trailing it depends on position_side
# (long → bid, short → ask) so it's resolved at create time rather than via
# this dict.
_TRIGGER_REF: dict[str, str] = {
    "buy_price_alert": "ask",
    "sell_price_alert": "bid",
}
_TRAILING_TRIGGER_REF: dict[str, str] = {
    "long": "bid",  # long trailing exits via sell → bid is the reference
    "short": "ask",  # short trailing exits via buy → ask is the reference
}


@dataclass(frozen=True)
class CreateTradeIntentInput:
    symbol: str
    strategy: str
    quantity_lots: int
    owner_user_id: UUID
    # `target_price` non-None for buy/sell, None for trailing. The trailing
    # triple (`position_side`, `trail_mode`, `trail_value`) is the inverse.
    # Pydantic discriminated union upstream guarantees exactly one shape
    # reaches here; the command still re-asserts for safety.
    target_price: str | None = None
    position_side: str | None = None
    trail_mode: str | None = None
    trail_value: str | None = None


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
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._evaluator = evaluator
        self._db = db

    def execute(self, inp: CreateTradeIntentInput) -> TradeIntentData:
        try:
            # 1. validate symbol existence and tradability
            symbol_obj = self._symbol_service.get_tradable_symbol(inp.symbol)

            try:
                security_type = SecurityType(symbol_obj.instrument_type)
            except ValueError:
                raise InvalidTypeError(symbol_obj.instrument_type, "must be stock or etf") from None

            # 2. strategy-specific: resolve target_price / trailing fields
            now = self._session_service.now_taipei()
            trading_date = self._session_service.get_day_intent_trading_date(now)
            initial_status = self._session_service.get_initial_day_intent_status(now)

            if inp.strategy == "trailing_stop_alert":
                intent_id = self._create_trailing(inp, security_type, trading_date, initial_status)
            else:
                intent_id = self._create_price_alert(inp, security_type, trading_date, initial_status)

            # 3. reconcile subscription before commit — fail here rolls back the intent
            reconcile_on_create(self._quote_provider, inp.symbol)

            # 4. immediate trigger / watermark init. For buy/sell this only
            #    fires when current quote already meets the target. For
            #    trailing the first valid quote also initialises watermark +
            #    dynamic_trigger_price even when the trigger condition isn't
            #    met yet (spec §183-193).
            if initial_status == "active":
                self._apply_inline_trigger_if_quote_met(intent_id, inp.symbol, security_type, now)

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # 5. materialise server-side values (created_at / updated_at, plus
        #    triggered_at if step 4 fired). Done after commit so a SELECT
        #    hiccup here can't corrupt a half-written write.
        return self._intent_repo.find_by_id(intent_id, inp.owner_user_id)

    def _create_price_alert(
        self,
        inp: CreateTradeIntentInput,
        security_type: SecurityType,
        trading_date: date,
        initial_status: str,
    ) -> UUID:
        if inp.target_price is None:
            raise ValueError(f"target_price required for strategy {inp.strategy!r}")
        effective_price = PriceService.validate(
            PriceRequest(
                type=security_type,
                price=inp.target_price,
                amount=inp.quantity_lots,
            )
        )
        trigger_ref = _TRIGGER_REF.get(inp.strategy)
        if trigger_ref is None:
            raise ValueError(f"Unsupported strategy: {inp.strategy}")
        return self._intent_repo.create(
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
        )

    def _create_trailing(
        self,
        inp: CreateTradeIntentInput,
        security_type: SecurityType,
        trading_date: date,
        initial_status: str,
    ) -> UUID:
        # Pydantic discriminated union upstream guarantees these are populated
        # for trailing rows; assert for safety + mypy narrowing.
        assert inp.position_side is not None
        assert inp.trail_mode is not None
        assert inp.trail_value is not None

        trail_value_decimal = PriceService.parse(inp.trail_value)
        if inp.trail_mode == "fixed_amount":
            # Tick check: reuse the symbol's tick table at the trail_value
            # level. Percentage mode skips this — the value isn't a price.
            if not PriceService.is_valid_tick(security_type, trail_value_decimal):
                tick = PriceService.lookup_tick_size(security_type, trail_value_decimal)
                raise InvalidTickSizeError(
                    inp.trail_value,
                    f"not a valid tick multiple (tick size for this range is {tick})",
                    nearest_lower=PriceService.nearest_lower(security_type, trail_value_decimal),
                    nearest_upper=PriceService.nearest_upper(security_type, trail_value_decimal),
                    field="trailValue",
                )

        trigger_ref = _TRAILING_TRIGGER_REF[inp.position_side]
        return self._intent_repo.create(
            owner_user_id=inp.owner_user_id,
            symbol=inp.symbol,
            strategy=inp.strategy,
            quantity_lots=inp.quantity_lots,
            target_price_original=None,
            target_price_effective=None,
            trigger_reference_price_type=trigger_ref,
            trading_date=trading_date,
            time_in_force=_TIME_IN_FORCE,
            execution_mode=_EXECUTION_MODE,
            status=initial_status,
            position_side=inp.position_side,
            trail_mode=inp.trail_mode,
            trail_value=trail_value_decimal,
        )

    def _apply_inline_trigger_if_quote_met(
        self,
        intent_id: UUID,
        symbol: str,
        security_type: SecurityType,
        now: datetime,
    ) -> None:
        """Evaluate current quote against the just-flushed intent in this tx.

        Runs before commit, so the intent is still invisible to other sessions
        — no `SELECT ... FOR UPDATE` is needed. Quote unavailable is
        non-blocking: the create commits as `active` and a later quote drives
        the trigger.

        For trailing intents the same hook also seeds the initial watermark +
        dynamic_trigger_price (spec §183-193) even when the trigger condition
        isn't met — the first valid quote always moves the watermark off NULL.
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
            position_side=intent_row.position_side,
            trail_mode=intent_row.trail_mode,
            trail_value=intent_row.trail_value,
            # watermark / dynamic columns are still NULL on a fresh trailing
            # intent — evaluator's first-quote branch initialises them.
        )

        quote = quotes[0]
        result = self._evaluator.evaluate(quote, intent_domain, now, security_type=security_type)

        # Persist watermark mutation (trailing only) before any trigger write
        # so the create transaction commits with a consistent trail state.
        if result.watermark_mutation is not None:
            mutation = result.watermark_mutation
            self._intent_repo.update_trailing_state(
                intent_row.id,
                position_side=mutation.position_side,
                watermark=mutation.watermark,
                dynamic_trigger_price=mutation.dynamic_trigger_price,
                updated_at=mutation.updated_at,
            )

        if not result.should_trigger:
            return

        # evaluator guarantees these are populated when should_trigger is True;
        # explicit check (not assert) so the invariant holds under `python -O`.
        if result.trigger_price is None or result.trigger_reference_price_type is None:
            raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")

        # Trailing trigger needs the watermark + dynamic snapshot. For a fresh
        # trailing intent the mutation is always present (first quote always
        # moves watermark off NULL), so we read it from there directly.
        trailing_snapshot: dict[str, Decimal | None] = {}
        if intent_row.strategy == "trailing_stop_alert":
            assert result.watermark_mutation is not None
            trailing_snapshot["watermark_at_trigger"] = result.watermark_mutation.watermark
            trailing_snapshot["dynamic_trigger_price_at_trigger"] = result.watermark_mutation.dynamic_trigger_price

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
                **trailing_snapshot,
            ),
        )


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
