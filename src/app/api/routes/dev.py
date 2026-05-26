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

from fastapi import APIRouter

from app.api.deps import (
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
from app.domain.trade_intent import IntentNotFoundError
from app.domain.trigger_event import DuplicateTriggerError, quote_snapshot_to_jsonb
from app.schemas.dev import EvaluateQuotesData, EvaluateQuotesRequest, EvaluateQuotesResponse
from app.services.quote.base import QuoteUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/evaluate-quotes", response_model=EvaluateQuotesResponse)
def evaluate_quotes(
    request: EvaluateQuotesRequest,
    intent_repo: IntentRepoDep,
    quote_provider: QuoteProviderDep,
    evaluator: QuoteEvaluatorDep,
    trigger_cmd: TriggerIntentCommandDep,
    session_service: TradingSessionServiceDep,
) -> EvaluateQuotesResponse:
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

    now = session_service.now_taipei()
    triggered_ids: list[str] = []
    for intent in intents:
        quote = quotes_by_symbol.get(intent.symbol)
        if quote is None:
            continue
        result = evaluator.evaluate(quote, intent, now)
        if result.baseline_updated_at is not None:
            intent_repo.system_update_trailing_baseline(
                intent.id,
                result.baseline,
                result.dynamic_trigger_price,
                result.baseline_updated_at,
            )
        if not result.should_trigger:
            if result.baseline_updated_at is not None:
                intent_repo.commit()
            continue
        if result.trigger_price is None or result.trigger_reference_price_type is None:
            raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")
        try:
            trigger_cmd.execute(
                TriggerIntentInput(
                    intent_id=intent.id,
                    trigger_price=result.trigger_price,
                    trigger_reference_price_type=result.trigger_reference_price_type,
                    fallback_used=result.fallback_used,
                    quote_snapshot=quote_snapshot_to_jsonb(quote),
                    quote_time=quote.quote_time,
                )
            )
            triggered_ids.append(str(intent.id))
        except (IntentNotActiveError, IntentNotFoundError, DuplicateTriggerError) as exc:
            # Race between listing actives and acquiring FOR UPDATE; skip silently.
            logger.warning("trigger skipped for intent %s: %s", intent.id, exc)

    return EvaluateQuotesResponse(
        data=EvaluateQuotesData(
            evaluated_symbols=symbols,
            triggered_intent_ids=triggered_ids,
        )
    )
