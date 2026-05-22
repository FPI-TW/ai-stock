import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commands.trigger_intent import TriggerIntentInput, persist_trigger
from app.db.models.core import TradeIntent as TradeIntentRow
from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
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

    Two entry points share the same in-transaction work:

    - `execute(input) -> TradeIntentData` — owns the transaction boundary.
      Single-create callers (the `POST /trade-intents` route) use this. It
      wraps `execute_within_tx` with `commit` / `rollback` and resolves the
      flushed intent via `find_by_id` after commit.
    - `execute_within_tx(input) -> UUID` — runs validation / flush /
      reconcile / inline-trigger only. Does **not** commit, does **not**
      rollback, does **not** call `find_by_id`. Callers (e.g. the batch
      endpoint introduced in BE-V0.5-14) loop this method inside their own
      `try` block and own the final `commit` / `rollback`.

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
       the entire create. (Only `execute()` runs this step.)
    5. `find_by_id` after commit to materialise server-side timestamps and
       the final status (active / triggered / scheduled). (Only `execute()`
       runs this step.)

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
            intent_id = self.execute_within_tx(inp)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # materialise server-side values (created_at / updated_at, plus
        # triggered_at if the inline-trigger fired). Done after commit so a
        # SELECT hiccup here can't corrupt a half-written write.
        return self._intent_repo.find_by_id(intent_id, inp.owner_user_id)

    def execute_within_tx(self, inp: CreateTradeIntentInput) -> UUID:
        """Validate, flush, reconcile, and inline-trigger — without owning the tx.

        Caller owns `commit` / `rollback`. Any failure raises; caller must
        roll back the session before the next operation. Returns the
        flushed intent id; the caller is responsible for materialising the
        domain object via `find_by_id` after commit if needed.
        """

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

        # 6. immediate trigger if the intent is active and the current quote
        #    already meets the strategy condition (spec §15). Scheduled
        #    intents skip — they wait for the next-day activation path.
        #    Folded into this transaction so dispatcher can never observe
        #    an active-but-pre-trigger window.
        if initial_status == "active":
            self._apply_inline_trigger_if_quote_met(intent_id, inp.symbol, now)

        return intent_id

    def _apply_inline_trigger_if_quote_met(
        self,
        intent_id: UUID,
        symbol: str,
        now: datetime,
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
        )

        quote = quotes[0]
        result = self._evaluator.evaluate(quote, intent_domain, now)
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
