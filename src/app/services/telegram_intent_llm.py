"""DeepSeek classifier for Telegram text.

The classifier is intentionally narrow.  A response that is not exactly the
small Pydantic schema below is rejected and never reaches the intent command.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.config import Settings
from app.domain.telegram_intent import (
    CapabilityId,
    TelegramIntentDecision,
    TelegramIntentLlmError,
)

MissingField = Literal["symbol", "quantityLots", "targetPrice"]


class TelegramIntentDecider(Protocol):
    def decide(
        self,
        *,
        message: str,
        previous_capability_id: str | None = None,
        previous_payload: dict[str, object] | None = None,
    ) -> TelegramIntentDecision: ...


class _DecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    decision: Literal["unsupported", "needs_clarification", "draft_intent", "ambiguous"]
    capability_id: CapabilityId | None = Field(default=None, alias="capabilityId")
    symbol: str | None = None
    quantity_lots: int | None = Field(default=None, alias="quantityLots")
    target_price: str | None = Field(default=None, alias="targetPrice")
    missing_fields: list[MissingField] = Field(default_factory=list, alias="missingFields")

    @field_validator("target_price", mode="before")
    @classmethod
    def _normalize_numeric_price(cls, value: object) -> object:
        # JSON producers commonly encode prices as numbers even when the
        # contract asks for a decimal string.  Normalize only real JSON numeric
        # primitives here; bool is deliberately left untouched so strict
        # validation still rejects it.
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        return value

    @field_validator("symbol")
    @classmethod
    def _numeric_symbol(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"\d{4,6}", value) is None:
            raise ValueError("symbol must be a numeric Taiwan ticker")
        return value

    @field_validator("quantity_lots")
    @classmethod
    def _positive_quantity(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("quantityLots must be positive")
        return value

    @field_validator("target_price")
    @classmethod
    def _positive_price(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            price = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("targetPrice must be a decimal string") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError("targetPrice must be positive")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> _DecisionPayload:
        if self.decision in {"needs_clarification", "draft_intent"} and self.capability_id is None:
            raise ValueError("capabilityId is required for a trading decision")
        if self.decision in {"unsupported", "ambiguous"}:
            if self.capability_id is not None or any(
                value is not None for value in (self.symbol, self.quantity_lots, self.target_price)
            ):
                raise ValueError("unsupported or ambiguous decisions cannot contain intent fields")
            if self.missing_fields:
                raise ValueError("unsupported or ambiguous decisions cannot contain missingFields")
            return self

        required_fields = {"symbol", "quantityLots"}
        if self.capability_id in {"limit_buy", "limit_sell"}:
            required_fields.add("targetPrice")
        supplied_fields = {
            field
            for field, value in (
                ("symbol", self.symbol),
                ("quantityLots", self.quantity_lots),
                ("targetPrice", self.target_price),
            )
            if value is not None
        }
        actually_missing = required_fields - supplied_fields
        missing_fields = set(self.missing_fields)
        if len(missing_fields) != len(self.missing_fields):
            raise ValueError("missingFields cannot contain duplicates")
        if self.decision == "needs_clarification":
            if not missing_fields or missing_fields != actually_missing:
                raise ValueError("missingFields must exactly match missing required values")
        elif missing_fields or actually_missing:
            raise ValueError("draft_intent must contain every required value")
        if self.capability_id in {"market_buy", "market_sell"} and self.target_price is not None:
            raise ValueError("market capabilities cannot contain targetPrice")
        return self


class DisabledTelegramIntentDecider:
    def decide(
        self,
        *,
        message: str,
        previous_capability_id: str | None = None,
        previous_payload: dict[str, object] | None = None,
    ) -> TelegramIntentDecision:
        raise TelegramIntentLlmError("telegram intent LLM is not configured")


class DeepSeekTelegramIntentDecider:
    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required for DeepSeekTelegramIntentDecider")
        self._api_key = settings.deepseek_api_key
        self._model = settings.telegram_llm_model
        self._timeout = settings.telegram_llm_timeout_seconds

    def decide(
        self,
        *,
        message: str,
        previous_capability_id: str | None = None,
        previous_payload: dict[str, object] | None = None,
    ) -> TelegramIntentDecision:
        body = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify one Telegram stock request and return only one JSON object. "
                        "Use decision=unsupported for ordinary conversation or unsupported trading "
                        "capabilities; use ambiguous when a trading request conflicts or cannot be "
                        "understood. Allowed capabilityId values are limit_buy, limit_sell, market_buy, "
                        "market_sell. limit requests require a numeric symbol, positive quantityLots, "
                        "and positive targetPrice. market requests require symbol and quantityLots and "
                        "must omit targetPrice. quantityLots is board-lot count, not shares. "
                        "Use needs_clarification with missingFields when a supported request is incomplete; "
                        "use draft_intent only when complete. Symbols are numeric tickers only, without .TW. "
                        'Schema: {"decision":"...","capabilityId":null,"symbol":null,'
                        '"quantityLots":null,"targetPrice":null,"missingFields":[]}. '
                        "Do not include any other keys."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "message": message,
                            "previousCapabilityId": previous_capability_id,
                            "previousPayload": previous_payload or {},
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
            "max_tokens": 512,
        }
        request = urllib.request.Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TelegramIntentLlmError(f"telegram intent LLM request failed with HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TelegramIntentLlmError("telegram intent LLM request failed") from exc
        if not isinstance(raw, dict):
            raise TelegramIntentLlmError("telegram intent LLM response is invalid")
        return _parse_response(raw)


def _parse_response(raw: dict[str, Any]) -> TelegramIntentDecision:
    text = _extract_chat_content(raw)
    try:
        payload = _DecisionPayload.model_validate_json(text)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        )
        raise TelegramIntentLlmError(f"telegram intent LLM returned invalid JSON schema ({details})") from exc
    except ValueError as exc:
        raise TelegramIntentLlmError("telegram intent LLM returned invalid JSON") from exc
    target_price: Decimal | None = None
    if payload.target_price is not None:
        try:
            target_price = Decimal(payload.target_price)
        except InvalidOperation as exc:  # guarded by the Pydantic validator; keep boundary defensive
            raise TelegramIntentLlmError("telegram intent LLM returned invalid target price") from exc
    return TelegramIntentDecision(
        decision=payload.decision,
        capability_id=payload.capability_id,
        symbol=payload.symbol,
        quantity_lots=payload.quantity_lots,
        target_price=target_price,
        missing_fields=tuple(payload.missing_fields),
    )


def _extract_chat_content(raw: dict[str, Any]) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TelegramIntentLlmError("telegram intent LLM response has no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise TelegramIntentLlmError("telegram intent LLM response choice is invalid")
    message = first.get("message")
    if not isinstance(message, dict):
        raise TelegramIntentLlmError("telegram intent LLM response message is invalid")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise TelegramIntentLlmError("telegram intent LLM response has no content")
    return content


def build_telegram_intent_decider(settings: Settings) -> TelegramIntentDecider:
    if settings.deepseek_api_key:
        return DeepSeekTelegramIntentDecider(settings)
    return DisabledTelegramIntentDecider()
