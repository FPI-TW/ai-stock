import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.commands.trigger_intent import (
    IntentNotActiveError,
    TriggerIntentCommand,
    TriggerIntentInput,
)
from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trade_intent import IntentNotFoundError, TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import DuplicateTriggerError, quote_snapshot_to_jsonb
from app.repositories.intent_repository import IntentRepository
from app.services.quote.base import QuoteProvider
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
}


@dataclass(frozen=True)
class CreateTradeIntentInput:
    symbol: str
    strategy: str
    quantity_lots: int
    target_price: str  # kept as str to preserve decimal precision
    owner_user_id: UUID


@dataclass(frozen=True)
class CancelTradeIntentInput:
    intent_id: UUID
    owner_user_id: UUID


class CreateTradeIntentCommand:
    """Create a trade intent, reconcile its quote subscription, and optionally trigger.

    Transaction shape:
    1. validate → flush intent (no commit)
    2. `reconcile_on_create` — subscribe broker. Failure here rolls back the intent.
    3. commit the create transaction.
    4. `find_by_id` to materialise server-side timestamps.
    5. If the intent landed `active` (spec §15: 盤中 create + 條件成立 → 立即觸發),
       attempt an immediate trigger via `TriggerIntentCommand` in a separate
       transaction. The brief "active but pre-trigger" window is the V0.5
       trade-off; V1 outbox will collapse this to one tx.

    Immediate-trigger failures (no quote, race on status guard, duplicate
    backstop) are downgraded to warning logs; the intent stays in whatever
    state it ended up in, and callers can re-evaluate via
    `/dev/evaluate-quotes` or a future scheduler.
    """

    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: IntentRepository,
        quote_provider: QuoteProvider,
        evaluator: QuoteEvaluator,
        trigger_cmd: TriggerIntentCommand,
        db: Session,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._evaluator = evaluator
        self._trigger_cmd = trigger_cmd
        self._db = db

    def execute(self, inp: CreateTradeIntentInput) -> TradeIntentData:
        try:
            # 1. validate symbol existence and tradability
            symbol_obj = self._symbol_service.get_tradable_symbol(inp.symbol)

            # 2. validate price and tick size via PriceService
            try:
                security_type = SecurityType(symbol_obj.instrument_type)
            except ValueError:
                raise InvalidTypeError(symbol_obj.instrument_type, "must be stock or etf") from None
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
            )

            # 5. reconcile subscription before commit — fail here rolls back the intent
            reconcile_on_create(self._quote_provider, inp.symbol)

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # 6. materialise server-side values (created_at / updated_at). Done
        #    after commit so a SELECT hiccup here can't corrupt a half-written
        #    write — the row is already durable.
        intent = self._intent_repo.find_by_id(intent_id, inp.owner_user_id)

        # 7. immediate trigger if the intent is active and the current quote
        #    already meets the strategy condition (spec §15). Scheduled
        #    intents skip — they wait for the next-day activation path.
        if intent.status == "active":
            return self._maybe_immediate_trigger(intent, now)
        return intent

    def _maybe_immediate_trigger(self, intent: TradeIntentData, now: datetime) -> TradeIntentData:
        # Quote unavailable is non-blocking per spec §15: intent stays active,
        # next /dev/evaluate-quotes (or a scheduler in V1) will re-attempt.
        try:
            quotes = self._quote_provider.get_quotes([intent.symbol])
        except Exception as exc:
            logger.warning(
                "create: quote provider lookup failed for %s, intent %s stays active: %s",
                intent.symbol,
                intent.id,
                exc,
            )
            return intent
        if not quotes:
            logger.warning(
                "create: quote unavailable for %s, intent %s stays active",
                intent.symbol,
                intent.id,
            )
            return intent

        quote = quotes[0]
        result = self._evaluator.evaluate(quote, intent, now)
        if not result.should_trigger:
            return intent

        # evaluator guarantees these are populated when should_trigger is True
        assert result.trigger_price is not None
        assert result.trigger_reference_price_type is not None
        try:
            self._trigger_cmd.execute(
                TriggerIntentInput(
                    intent_id=intent.id,
                    trigger_price=result.trigger_price,
                    trigger_reference_price_type=result.trigger_reference_price_type,
                    fallback_used=result.fallback_used,
                    quote_snapshot=quote_snapshot_to_jsonb(quote),
                    quote_time=quote.quote_time,
                )
            )
        except (IntentNotActiveError, IntentNotFoundError, DuplicateTriggerError) as exc:
            # Race between create-commit and trigger-lock; intent stays active
            # in the response. Caller can re-evaluate via /dev/evaluate-quotes
            # or a future scheduler. Logged for ops investigation.
            logger.warning("create: immediate trigger race for intent %s: %s", intent.id, exc)
            return intent

        return self._intent_repo.find_by_id(intent.id, intent.owner_user_id)


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
