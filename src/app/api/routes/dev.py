"""Local-mode dev endpoints — BE-V0.5-09.

Registered conditionally from `main.py` when `LOCAL_MODE=true`. The endpoints
here are operational tooling for demo / integration tests, not part of the
user-facing API surface.

Deployment assumption: the FastAPI app is bound to 127.0.0.1 (developer
machine or Docker compose loopback). No auth gate sits in front of these
routes — anyone able to reach the port can trigger active intents. If
LOCAL_MODE is ever exposed beyond loopback (internal demo host, shared
staging, etc.) this router must be gated behind an auth header before
deployment. See PR #13 review issue #4.
"""

import logging
from decimal import Decimal

from fastapi import APIRouter

from app.api.deps import (
    DatabaseDep,
    IntentRepoDep,
    QuoteEvaluatorDep,
    QuoteProviderDep,
    TradingSessionServiceDep,
    TriggerIntentCommandDep,
)
from app.commands.trigger_intent import (
    IntentNotActiveError,
    TriggerIntentInput,
)
from app.domain.price import SecurityType
from app.domain.trade_intent import IntentNotFoundError
from app.domain.trigger_event import DuplicateTriggerError, quote_snapshot_to_jsonb
from app.repositories.symbol_repository import SymbolRepository
from app.schemas.dev import EvaluateQuotesData, EvaluateQuotesRequest, EvaluateQuotesResponse
from app.services.quote.base import QuoteUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/evaluate-quotes", response_model=EvaluateQuotesResponse)
def evaluate_quotes(
    request: EvaluateQuotesRequest,
    db: DatabaseDep,
    intent_repo: IntentRepoDep,
    quote_provider: QuoteProviderDep,
    evaluator: QuoteEvaluatorDep,
    trigger_cmd: TriggerIntentCommandDep,
    session_service: TradingSessionServiceDep,
) -> EvaluateQuotesResponse:
    # TODO(refactor): the per-intent evaluate → mutate → trigger loop here is
    # near-duplicated in `app.services.quote_dispatcher.QuoteEvaluationDispatcher`.
    # Differences are commit / exception / return-shape only. After BE-V0.5-16
    # ships, extract a shared `process_quote_for_intents(...)` helper so a
    # future strategy doesn't have to be added in two places again.
    if request.symbols:
        symbols = sorted(set(request.symbols))
    else:
        symbols = sorted(intent_repo.system_list_active_symbols())

    # InMemoryQuoteProvider.get_quotes raises QuoteUnavailableError on the first
    # missing symbol (PR #12 contract); for dev-evaluate we want partial-set
    # behaviour — symbols with no snapshot are simply skipped without aborting
    # the rest of the batch. Loop per-symbol so a single missing quote doesn't
    # short-circuit the whole evaluation.
    quotes_by_symbol = {}
    for symbol in symbols:
        try:
            quotes_by_symbol.update({q.symbol: q for q in quote_provider.get_quotes([symbol])})
        except QuoteUnavailableError:
            continue
    intents = intent_repo.system_list_active_by_symbols(symbols)

    # Trailing intents need security_type for tick rounding. Look up once per
    # symbol that has any trailing intent (mirrors dispatcher's lazy lookup).
    symbol_repo = SymbolRepository(db)
    security_types: dict[str, SecurityType] = {}
    for intent in intents:
        if intent.strategy != "trailing_stop_alert" or intent.symbol in security_types:
            continue
        symbol_obj = symbol_repo.find_by_symbol(intent.symbol)
        if symbol_obj is not None:
            security_types[intent.symbol] = SecurityType(symbol_obj.instrument_type)

    now = session_service.now_taipei()
    triggered_ids: list[str] = []
    pending_mutation = False
    for intent in intents:
        quote = quotes_by_symbol.get(intent.symbol)
        if quote is None:
            continue
        result = evaluator.evaluate(quote, intent, now, security_type=security_types.get(intent.symbol))

        if result.watermark_mutation is not None:
            mutation = result.watermark_mutation
            intent_repo.update_trailing_state(
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

        trailing_extra: dict[str, Decimal | None] = {}
        if intent.strategy == "trailing_stop_alert":
            if result.watermark_mutation is not None:
                trailing_extra["watermark_at_trigger"] = result.watermark_mutation.watermark
                trailing_extra["dynamic_trigger_price_at_trigger"] = result.watermark_mutation.dynamic_trigger_price
            else:
                trailing_extra["watermark_at_trigger"] = (
                    intent.watermark_high if intent.position_side == "long" else intent.watermark_low
                )
                trailing_extra["dynamic_trigger_price_at_trigger"] = intent.dynamic_trigger_price

        try:
            trigger_cmd.execute(
                TriggerIntentInput(
                    intent_id=intent.id,
                    trigger_price=result.trigger_price,
                    trigger_reference_price_type=result.trigger_reference_price_type,
                    fallback_used=result.fallback_used,
                    quote_snapshot=quote_snapshot_to_jsonb(quote),
                    quote_time=quote.quote_time,
                    **trailing_extra,
                )
            )
            triggered_ids.append(str(intent.id))
            # trigger_cmd commits; earlier staged mutations land too.
            pending_mutation = False
        except (IntentNotActiveError, IntentNotFoundError, DuplicateTriggerError) as exc:
            # Race between listing actives and acquiring FOR UPDATE; skip
            # silently. trigger_cmd rolled back any pending mutations too.
            pending_mutation = False
            logger.warning("trigger skipped for intent %s: %s", intent.id, exc)

    # Persist mutations that weren't committed by a trigger (or all when no
    # trigger fired) — request-scoped session has no implicit commit.
    if pending_mutation:
        db.commit()

    return EvaluateQuotesResponse(
        data=EvaluateQuotesData(
            evaluated_symbols=symbols,
            triggered_intent_ids=triggered_ids,
        )
    )
