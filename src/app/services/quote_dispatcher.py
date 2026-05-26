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

from sqlalchemy.orm import Session

from app.commands.trigger_intent import (
    DuplicateTriggerError,
    IntentNotActiveError,
    TriggerIntentCommand,
    TriggerIntentInput,
)
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trade_intent import IntentNotFoundError
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import quote_snapshot_to_jsonb
from app.repositories.intent_repository import IntentRepository
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

            trigger_cmd = TriggerIntentCommand(db)
            has_pending_writes = False
            for intent in intents:
                result = self._evaluator.evaluate(snapshot, intent, now)
                if result.baseline_updated_at is not None:
                    repo.system_update_trailing_baseline(
                        intent.id,
                        result.baseline,
                        result.dynamic_trigger_price,
                        result.baseline_updated_at,
                    )
                    has_pending_writes = True
                if not result.should_trigger:
                    continue
                if result.trigger_price is None or result.trigger_reference_price_type is None:
                    raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")
                try:
                    with repo.begin_nested():
                        trigger_cmd.stage(
                            TriggerIntentInput(
                                intent_id=intent.id,
                                trigger_price=result.trigger_price,
                                trigger_reference_price_type=result.trigger_reference_price_type,
                                fallback_used=result.fallback_used,
                                quote_snapshot=quote_snapshot_to_jsonb(snapshot),
                                quote_time=snapshot.quote_time,
                            )
                        )
                    has_pending_writes = True
                except (IntentNotActiveError, IntentNotFoundError, DuplicateTriggerError) as exc:
                    # Race between listing actives and acquiring FOR UPDATE,
                    # or another callback already triggered this intent —
                    # safe to skip silently.
                    logger.warning("dispatch trigger skipped for intent %s: %s", intent.id, exc)
            if has_pending_writes:
                repo.commit()
