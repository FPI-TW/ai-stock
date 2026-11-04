from __future__ import annotations

import json
import urllib.error
from decimal import Decimal
from email.message import Message
from types import TracebackType
from typing import Any, cast
from urllib.request import Request
from uuid import UUID

import pytest

from app.core.config import Settings
from app.domain.telegram_intent import TelegramIntentLlmError
from app.services.telegram_intent_llm import (
    DisabledTelegramIntentDecider,
    OpenAITelegramIntentDecider,
    _parse_response,
    build_telegram_intent_decider,
)


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _settings() -> Settings:
    return Settings(
        LOCAL_USER_ID=UUID("00000000-0000-0000-0000-000000000001"),
        QUOTE_PROVIDER="in_memory",
        OPENAI_API_KEY="test-key",
        TELEGRAM_LLM_TIMEOUT_SECONDS=10,
    )


def _response(payload: dict[str, object]) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            }
        ],
    }


def _limit_payload() -> dict[str, object]:
    return {
        "decision": "draft_intent",
        "capabilityId": "limit_buy",
        "symbol": "0050",
        "quantityLots": 1,
        "targetPrice": "180",
        "missingFields": [],
    }


@pytest.mark.parametrize(
    ("target_price", "expected"),
    [(180, Decimal("180")), (180.5, Decimal("180.5")), ("180.50", Decimal("180.50"))],
)
def test_parse_response_accepts_numeric_or_string_target_price(
    target_price: int | float | str, expected: Decimal
) -> None:
    payload = {**_limit_payload(), "targetPrice": target_price}
    decision = _parse_response(_response(payload))
    assert decision.target_price == expected
    assert decision.symbol == "0050"
    assert decision.quantity_lots == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"capabilityId": "trailing_stop"},
        {"extra": "not allowed"},
        {"targetPrice": True},
        {"targetPrice": "NaN"},
        {"targetPrice": "-1"},
        {"quantityLots": None},
        {"quantityLots": True},
        {"quantityLots": 0},
        {"symbol": "0050.TW"},
        {"capabilityId": "market_buy"},
        {
            "decision": "needs_clarification",
            "capabilityId": "market_buy",
            "quantityLots": None,
            "targetPrice": None,
            "missingFields": ["quantityLots", "targetPrice"],
        },
        {"decision": "needs_clarification", "targetPrice": None, "missingFields": ["targetPrice", "targetPrice"]},
        {"decision": "unsupported"},
        {"decision": "ambiguous"},
    ],
)
def test_parse_response_rejects_invalid_intent_payload(changes: dict[str, object]) -> None:
    with pytest.raises(TelegramIntentLlmError, match="invalid JSON schema"):
        _parse_response(_response({**_limit_payload(), **changes}))


def test_parse_response_accepts_market_clarification() -> None:
    decision = _parse_response(
        _response(
            {
                "decision": "needs_clarification",
                "capabilityId": "market_sell",
                "symbol": "0050",
                "quantityLots": None,
                "targetPrice": None,
                "missingFields": ["quantityLots"],
            }
        )
    )
    assert decision.decision == "needs_clarification"
    assert decision.missing_fields == ("quantityLots",)
    assert decision.target_price is None


def test_parse_response_skips_reasoning_and_combines_text_parts() -> None:
    raw = _response(_limit_payload())
    message = raw["output"][0]
    text = message["content"][0]["text"]
    message["content"] = [{"type": "output_text", "text": text[:20]}, {"type": "output_text", "text": text[20:]}]
    raw["output"].insert(0, {"type": "reasoning", "summary": []})
    assert _parse_response(raw).capability_id == "limit_buy"


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress", None])
def test_parse_response_rejects_unfinished_output(status: str | None) -> None:
    raw = _response(_limit_payload())
    raw["status"] = status
    with pytest.raises(TelegramIntentLlmError, match="did not complete"):
        _parse_response(raw)


@pytest.mark.parametrize("field", ["error", "incomplete_details"])
def test_parse_response_rejects_error_even_with_completed_status(field: str) -> None:
    raw = _response(_limit_payload())
    raw[field] = {"reason": "max_output_tokens"}
    with pytest.raises(TelegramIntentLlmError, match="did not complete"):
        _parse_response(raw)


def test_parse_response_rejects_refusal_even_with_valid_content() -> None:
    raw = _response(_limit_payload())
    raw["output"][0]["content"].append({"type": "refusal", "refusal": "refused"})
    with pytest.raises(TelegramIntentLlmError, match="refused"):
        _parse_response(raw)


@pytest.mark.parametrize(
    "output",
    [
        None,
        [],
        [None],
        [{"type": "reasoning"}],
        [{"type": "function_call"}],
        [{"type": "message", "role": "user", "status": "completed", "content": []}],
        [{"type": "message", "role": "assistant", "status": "incomplete", "content": []}],
    ],
)
def test_parse_response_rejects_invalid_output(output: object) -> None:
    with pytest.raises(TelegramIntentLlmError):
        _parse_response({"status": "completed", "output": output})


@pytest.mark.parametrize(
    "content",
    [
        None,
        [],
        [None],
        [{"type": "output_text", "text": None}],
        [{"type": "output_text", "text": ""}],
        [{"type": "unknown", "text": "ignored"}],
        [{"type": "output_text", "text": "not json"}],
    ],
)
def test_parse_response_rejects_invalid_content(content: object) -> None:
    raw = _response(_limit_payload())
    raw["output"][0]["content"] = content
    with pytest.raises(TelegramIntentLlmError):
        _parse_response(raw)


def test_openai_request_uses_responses_api_and_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(json.dumps(_response({"decision": "unsupported"})).encode())

    monkeypatch.setattr("app.services.telegram_intent_llm.urllib.request.urlopen", fake_urlopen)
    decision = OpenAITelegramIntentDecider(_settings()).decide(message="hello")
    assert decision.decision == "unsupported"
    assert captured["timeout"] == 10.0
    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == "https://api.openai.com/v1/responses"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert request.get_header("Content-type") == "application/json"
    body = json.loads(cast(bytes, request.data))
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"] == {"effort": "none"}
    assert body["text"] == {"format": {"type": "json_object"}}
    assert body["max_output_tokens"] == 512
    assert body["store"] is False
    assert body["stream"] is False
    assert body["input"][0]["role"] == "system"
    assert json.loads(body["input"][1]["content"])["message"] == "hello"
    assert not {
        "messages",
        "response_format",
        "thinking",
        "reasoning_effort",
        "max_tokens",
        "max_completion_tokens",
    }.intersection(body)


def test_openai_request_preserves_configured_model_and_clarification_context(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    settings.telegram_llm_model = "configured-model"
    settings.telegram_llm_timeout_seconds = 3.5
    previous_payload = {"symbol": "0050", "quantityLots": 1}

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        body = json.loads(cast(bytes, request.data))
        assert body["model"] == "configured-model"
        assert timeout == 3.5
        assert json.loads(body["input"][1]["content"]) == {
            "message": "價格 180",
            "previousCapabilityId": "limit_buy",
            "previousPayload": previous_payload,
        }
        return _Response(json.dumps(_response(_limit_payload())).encode())

    monkeypatch.setattr("app.services.telegram_intent_llm.urllib.request.urlopen", fake_urlopen)
    assert OpenAITelegramIntentDecider(settings).decide(
        message="價格 180", previous_capability_id="limit_buy", previous_payload=previous_payload
    ).target_price == Decimal("180")


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://api.openai.com/v1/responses", 401, "secret-body", Message(), None),
        urllib.error.URLError("secret-body"),
        TimeoutError("secret-body"),
    ],
)
def test_openai_request_errors_are_wrapped_without_provider_details(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def fake_urlopen(request: Request, timeout: float) -> _Response:
        raise error

    monkeypatch.setattr("app.services.telegram_intent_llm.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(TelegramIntentLlmError) as exc_info:
        OpenAITelegramIntentDecider(_settings()).decide(message="private message")
    assert "request failed" in str(exc_info.value)
    assert "secret-body" not in str(exc_info.value)
    assert "private message" not in str(exc_info.value)
    if isinstance(error, urllib.error.HTTPError):
        assert "HTTP 401" in str(exc_info.value)


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b"\xff"])
def test_openai_request_rejects_invalid_response(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    def fake_urlopen(request: Request, timeout: float) -> _Response:
        return _Response(body)

    monkeypatch.setattr("app.services.telegram_intent_llm.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(TelegramIntentLlmError):
        OpenAITelegramIntentDecider(_settings()).decide(message="hello")


def test_factory_uses_openai_key_and_disables_without_it() -> None:
    settings = _settings()
    assert isinstance(build_telegram_intent_decider(settings), OpenAITelegramIntentDecider)
    settings.openai_api_key = None
    decider = build_telegram_intent_decider(settings)
    assert isinstance(decider, DisabledTelegramIntentDecider)
    with pytest.raises(TelegramIntentLlmError, match="not configured"):
        decider.decide(message="hello")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAITelegramIntentDecider(settings)
