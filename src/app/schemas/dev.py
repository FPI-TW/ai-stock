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
