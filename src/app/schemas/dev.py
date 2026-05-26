from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class EvaluateQuotesRequest(BaseModel):
    symbols: list[str] | None = None


class EvaluateQuotesData(BaseModel):
    evaluated_symbols: list[str] = Field(serialization_alias="evaluatedSymbols")
    triggered_intent_ids: list[str] = Field(serialization_alias="triggeredIntentIds")


class EvaluateQuotesResponse(BaseModel):
    data: EvaluateQuotesData


class TwapWorkerData(BaseModel):
    processed_count: int = Field(serialization_alias="processedCount")


class TwapWorkerResponse(BaseModel):
    data: TwapWorkerData


# ---------------------------------------------------------------------------
# /dev/push-quote — thin HTTP wrapper over InMemoryQuoteProvider.push_quote
# ---------------------------------------------------------------------------


class PushQuoteRequest(BaseModel):
    symbol: str = Field(min_length=1)
    ask_price: Decimal | None = Field(default=None, validation_alias="askPrice")
    bid_price: Decimal | None = Field(default=None, validation_alias="bidPrice")
    last_price: Decimal | None = Field(default=None, validation_alias="lastPrice")
    quote_time: datetime | None = Field(default=None, validation_alias="quoteTime")
    received_at: datetime | None = Field(default=None, validation_alias="receivedAt")


class PushQuoteData(BaseModel):
    symbol: str
    quote_time: datetime = Field(serialization_alias="quoteTime")


class PushQuoteResponse(BaseModel):
    data: PushQuoteData


# ---------------------------------------------------------------------------
# /dev/set-clock + /dev/server-state — fake-clock control + state inspection
# ---------------------------------------------------------------------------


class SetClockRequest(BaseModel):
    fake_now: datetime | None = Field(default=None, validation_alias="fakeNow")
    advance_seconds: int | None = Field(default=None, validation_alias="advanceSeconds")


class ClockState(BaseModel):
    current_taipei: datetime = Field(serialization_alias="currentTaipei")
    is_frozen: bool = Field(serialization_alias="isFrozen")
    within_regular_session: bool = Field(serialization_alias="withinRegularSession")


class SetClockResponse(BaseModel):
    data: ClockState


class CurrentUserState(BaseModel):
    user_id: str = Field(serialization_alias="userId")
    source: str  # "header" | "default"


class ServerStateData(BaseModel):
    app_env: str = Field(serialization_alias="appEnv")
    local_mode: bool = Field(serialization_alias="localMode")
    quote_provider: str = Field(serialization_alias="quoteProvider")
    current_user: CurrentUserState = Field(serialization_alias="currentUser")
    clock: ClockState
    worker_pid: int = Field(serialization_alias="workerPid")
    active_subscriptions: list[str] = Field(serialization_alias="activeSubscriptions")


class ServerStateResponse(BaseModel):
    data: ServerStateData
