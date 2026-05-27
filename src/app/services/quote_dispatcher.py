"""Quote → evaluation dispatcher.

Bridges a `QuoteProvider`'s listener callback (which fires inline on whatever
thread delivered the snapshot — broker-backed providers may invoke listeners
on an SDK worker thread) to the synchronous evaluator + trigger-transaction
pipeline.

Why this lives in `services/` and not in a provider package: it is provider-
agnostic by design. A provider's quote-arrival callback knows nothing about
evaluators; the dispatcher is registered as an opaque
`Callable[[QuoteSnapshot], None]` via `provider.add_quote_listener(...)` so
the provider stays decoupled from domain logic. The same wiring works for
`InMemoryQuoteProvider` in integration tests.

Threading model: every `dispatch` call opens its own short-lived SQLAlchemy
session — never reuse a request-scoped session here, because broker threads
have no notion of FastAPI's request lifecycle. Top-level exceptions are
swallowed and logged so a single dispatch failure can never tear down the
broker worker thread.
"""

import logging
from collections.abc import Callable
from decimal import Decimal

from sqlalchemy.orm import Session

from app.commands.trigger_intent import (
    DuplicateTriggerError,
    IntentNotActiveError,
    TriggerIntentCommand,
    TriggerIntentInput,
)
from app.domain.price import SecurityType
from app.domain.quote_evaluation import EvaluationResult, QuoteEvaluator
from app.domain.trade_intent import IntentNotFoundError, TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import quote_snapshot_to_jsonb
from app.repositories.intent_repository import IntentRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.quote.base import QuoteSnapshot

logger = logging.getLogger(__name__)


SessionFactory = Callable[[], Session]


class QuoteEvaluationDispatcher:
    """Stateless dispatch object suitable for registration as a quote listener.

    Holds only thread-safe singletons (the evaluator, the session service, and
    a session factory). Per-call state (the session, repo, command) is built
    inside `dispatch` so the dispatcher itself does not become a shared mutable
    object across broker threads.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        evaluator: QuoteEvaluator,
        session_service: TradingSessionService,
    ) -> None:
        self._session_factory = session_factory
        self._evaluator = evaluator
        self._session_service = session_service

    def dispatch(self, snapshot: QuoteSnapshot) -> None:
        """Evaluate the snapshot against active intents on `snapshot.symbol`.

        Triggers any intent whose price condition is met. Designed to be the
        callback target of `QuoteProvider.add_quote_listener`. Catches every
        exception path so the broker worker thread never sees a failure.
        """
        try:
            self._dispatch_inner(snapshot)
        except Exception:
            logger.exception("quote dispatch failed on %s", snapshot.symbol)

    def _dispatch_inner(self, snapshot: QuoteSnapshot) -> None:
        now = self._session_service.now_taipei()
        with self._session_factory() as db:
            repo = IntentRepository(db)
            intents = repo.system_list_active_by_symbols([snapshot.symbol])
            if not intents:
                return

            # Trailing intents need security_type for tick rounding. All intents
            # in this dispatch share the same symbol → one lookup covers them.
            security_type = self._lookup_security_type(db, snapshot.symbol, intents)

            trigger_cmd = TriggerIntentCommand(db)
            pending_mutation = False
            for intent in intents:
                result = self._evaluator.evaluate(snapshot, intent, now, security_type=security_type)

                # Persist watermark mutation before trigger so the trigger
                # snapshot (and restart recovery) sees the same values the
                # evaluator used for the comparison.
                if result.watermark_mutation is not None:
                    mutation = result.watermark_mutation
                    repo.update_trailing_state(
                        intent.id,
                        position_side=mutation.position_side,
                        watermark=mutation.watermark,
                        dynamic_trigger_price=mutation.dynamic_trigger_price,
                        updated_at=mutation.updated_at,
                    )
                    pending_mutation = True

                if not result.should_trigger:
                    continue
                if result.trigger_price is None or result.trigger_reference_price_type is None:
                    raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")

                trigger_input = self._build_trigger_input(snapshot, intent, result)
                try:
                    trigger_cmd.execute(trigger_input)
                    # trigger_cmd.execute commits; any earlier staged mutations
                    # are now on disk too.
                    pending_mutation = False
                except (IntentNotActiveError, IntentNotFoundError, DuplicateTriggerError) as exc:
                    # Race between listing actives and acquiring FOR UPDATE,
                    # or another callback already triggered this intent —
                    # safe to skip silently. trigger_cmd rolled the session
                    # back, dropping any pending mutations too; reset the flag.
                    pending_mutation = False
                    logger.warning("dispatch trigger skipped for intent %s: %s", intent.id, exc)

            # Mutations after the last trigger (or all when none triggered)
            # need an explicit commit — session __exit__ otherwise rolls back.
            if pending_mutation:
                db.commit()

    @staticmethod
    def _lookup_security_type(
        db: Session,
        symbol: str,
        intents: list[TradeIntentData],
    ) -> SecurityType | None:
        """Resolve the symbol's instrument_type if any trailing intent needs it.

        Returns None when no trailing intent is present — avoids an extra
        symbols-table read on the hot path for buy/sell-only dispatches.
        """
        if not any(i.strategy == "trailing_stop_alert" for i in intents):
            return None
        symbol_obj = SymbolRepository(db).find_by_symbol(symbol)
        if symbol_obj is None:
            # FK guarantees this shouldn't happen for an active intent.
            logger.warning("symbol %s not found while dispatching trailing intents", symbol)
            return None
        return SecurityType(symbol_obj.instrument_type)

    @staticmethod
    def _build_trigger_input(
        snapshot: QuoteSnapshot,
        intent: TradeIntentData,
        result: EvaluationResult,
    ) -> TriggerIntentInput:
        # Caller has already checked should_trigger=True implies these are set;
        # narrow explicitly so the constructor call typechecks.
        assert result.trigger_price is not None
        assert result.trigger_reference_price_type is not None
        watermark_at: Decimal | None = None
        dynamic_at: Decimal | None = None
        if intent.strategy == "trailing_stop_alert":
            if result.watermark_mutation is not None:
                watermark_at = result.watermark_mutation.watermark
                dynamic_at = result.watermark_mutation.dynamic_trigger_price
            else:
                watermark_at = intent.watermark_high if intent.position_side == "long" else intent.watermark_low
                dynamic_at = intent.dynamic_trigger_price
        return TriggerIntentInput(
            intent_id=intent.id,
            trigger_price=result.trigger_price,
            trigger_reference_price_type=result.trigger_reference_price_type,
            fallback_used=result.fallback_used,
            quote_snapshot=quote_snapshot_to_jsonb(snapshot),
            quote_time=snapshot.quote_time,
            watermark_at_trigger=watermark_at,
            dynamic_trigger_price_at_trigger=dynamic_at,
        )
