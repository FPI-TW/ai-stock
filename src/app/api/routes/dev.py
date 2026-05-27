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
import os
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.api.deps import (
    IntentRepoDep,
    QuoteEvaluatorDep,
    QuoteProviderDep,
    SettingsDep,
    TradingSessionServiceDep,
    TriggerIntentCommandDep,
)
from app.api.errors import ApiError, ErrorCode
from app.commands.trigger_intent import (
    IntentNotActiveError,
    TriggerIntentInput,
)
from app.core.security import build_local_user
from app.domain.trade_intent import IntentNotFoundError
from app.domain.trigger_event import DuplicateTriggerError, quote_snapshot_to_jsonb
from app.schemas.dev import (
    ClockState,
    CurrentUserState,
    EvaluateQuotesData,
    EvaluateQuotesRequest,
    EvaluateQuotesResponse,
    PushQuoteData,
    PushQuoteRequest,
    PushQuoteResponse,
    ServerStateData,
    ServerStateResponse,
    SetClockRequest,
    SetClockResponse,
)
from app.services.quote.base import QuoteSnapshot, QuoteUnavailableError
from app.services.quote.in_memory import InMemoryQuoteProvider

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
        if not result.should_trigger:
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


@router.post("/push-quote", response_model=PushQuoteResponse)
def push_quote(
    request: PushQuoteRequest,
    quote_provider: QuoteProviderDep,
    session_service: TradingSessionServiceDep,
) -> PushQuoteResponse:
    """Push a synthetic quote into the in-memory provider.

    Fires every registered listener (most importantly the
    ``QuoteEvaluationDispatcher`` installed by ``main.lifespan``), so an
    active intent that matches will end up triggered + notified inside the
    same listener callback — no need to also call ``/dev/evaluate-quotes``.

    Only supported on ``InMemoryQuoteProvider``; any other provider gets
    ``UNSUPPORTED_PROVIDER`` 400 (broker-backed providers don't accept
    synthetic ticks).
    """

    if not isinstance(quote_provider, InMemoryQuoteProvider):
        raise ApiError(
            code="UNSUPPORTED_PROVIDER",
            status_code=400,
            message="push-quote only works with QUOTE_PROVIDER=in_memory",
            details={"providerType": type(quote_provider).__name__},
        )
    if request.ask_price is None and request.bid_price is None and request.last_price is None:
        raise ApiError(
            code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            message="at least one of askPrice / bidPrice / lastPrice is required",
            details={"loc": ["body"]},
        )

    now = session_service.now_taipei()
    snapshot = QuoteSnapshot(
        symbol=request.symbol,
        ask_price=request.ask_price,
        bid_price=request.bid_price,
        last_price=request.last_price,
        quote_time=request.quote_time or now,
        received_at=request.received_at or now,
    )
    # push_quote dispatches to listeners synchronously; any DB writes (the
    # evaluator → trigger → notification chain) have committed by the time
    # this returns. UI polls /notifications afterwards to confirm outcome.
    quote_provider.push_quote(snapshot)
    return PushQuoteResponse(data=PushQuoteData(symbol=request.symbol, quote_time=snapshot.quote_time))


def _clock_state(session_service: TradingSessionServiceDep) -> ClockState:
    now = session_service.now_taipei()
    return ClockState(
        current_taipei=now,
        is_frozen=session_service.is_frozen(),
        within_regular_session=session_service.is_within_regular_session(now),
    )


@router.post("/set-clock", response_model=SetClockResponse)
def set_clock(
    request: SetClockRequest,
    session_service: TradingSessionServiceDep,
) -> SetClockResponse:
    """Freeze, advance, or reset the process-wide trading-session clock.

    - ``{}`` (both fields null) resets to system clock.
    - ``{"fakeNow": "..."}`` freezes at that instant.
    - ``{"advanceSeconds": N}`` advances from current (frozen or system).
    - Both: ``fakeNow`` is the base, ``advanceSeconds`` offsets it.

    Single-worker LOCAL_MODE only; multi-worker uvicorn would not see this
    override propagated across workers — call ``GET /dev/server-state`` to
    confirm which worker handled the request via ``workerPid``.
    """
    if request.fake_now is None and request.advance_seconds is None:
        session_service.set_clock(None)
    else:
        base = request.fake_now or session_service.now_taipei()
        target = base + timedelta(seconds=request.advance_seconds or 0)
        session_service.set_clock(lambda: target)
    return SetClockResponse(data=_clock_state(session_service))


@router.get("/server-state", response_model=ServerStateResponse)
def server_state(
    request: Request,
    settings: SettingsDep,
    session_service: TradingSessionServiceDep,
    quote_provider: QuoteProviderDep,
    x_local_user_id: Annotated[UUID | None, Header(alias="X-Local-User-Id")] = None,
) -> ServerStateResponse:
    """Snapshot what the /test page needs to render its top banner.

    Reports the effective request user (so the UI shows whether the
    header override took effect), the active quote provider type, the
    current clock state, the worker pid (for the multi-worker caveat
    noted on /dev/set-clock), and the set of broker subscriptions held
    by the provider.
    """
    if settings.local_mode and x_local_user_id is not None:
        user_id = str(x_local_user_id)
        source = "header"
    else:
        user_id = str(build_local_user(settings).user_id)
        source = "default"

    return ServerStateResponse(
        data=ServerStateData(
            app_env=settings.app_env,
            local_mode=settings.local_mode,
            quote_provider=type(quote_provider).__name__,
            current_user=CurrentUserState(user_id=user_id, source=source),
            clock=_clock_state(session_service),
            worker_pid=os.getpid(),
            active_subscriptions=sorted(quote_provider.active_subscriptions()),
        )
    )
