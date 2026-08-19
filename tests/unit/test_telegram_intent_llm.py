from __future__ import annotations

import json
from types import TracebackType
from typing import cast
from urllib.request import Request
from uuid import UUID

import pytest

from app.core.config import Settings
from app.domain.telegram_intent import TelegramIntentLlmError
from app.services.telegram_intent_llm import DeepSeekTelegramIntentDecider, _parse_response


class _Response:
    status = 200

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
        DEEPSEEK_API_KEY="test-key",
        TELEGRAM_LLM_TIMEOUT_SECONDS=10,
    )


def test_parse_response_rejects_unknown_capability_and_extra_keys() -> None:
    with pytest.raises(TelegramIntentLlmError):
        _parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "draft_intent",
                                    "capabilityId": "trailing_stop",
                                    "symbol": "0050",
                                    "quantityLots": 1,
                                    "targetPrice": "180",
                                    "missingFields": [],
                                }
                            )
                        }
                    }
                ]
            }
        )


def test_parse_response_rejects_incomplete_draft_and_market_target() -> None:
    incomplete_draft = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "decision": "draft_intent",
                            "capabilityId": "limit_buy",
                            "symbol": "0050",
                            "quantityLots": None,
                            "targetPrice": "180",
                            "missingFields": [],
                        }
                    )
                }
            }
        ]
    }
    market_target = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "decision": "draft_intent",
                            "capabilityId": "market_buy",
                            "symbol": "0050",
                            "quantityLots": 1,
                            "targetPrice": "180",
                            "missingFields": [],
                        }
                    )
                }
            }
        ]
    }

    with pytest.raises(TelegramIntentLlmError):
        _parse_response(incomplete_draft)
    with pytest.raises(TelegramIntentLlmError):
        _parse_response(market_target)


def test_parse_response_requires_capability_compatible_missing_fields() -> None:
    with pytest.raises(TelegramIntentLlmError):
        _parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "needs_clarification",
                                    "capabilityId": "market_buy",
                                    "symbol": "0050",
                                    "quantityLots": None,
                                    "targetPrice": None,
                                    "missingFields": ["quantityLots", "targetPrice"],
                                }
                            )
                        }
                    }
                ]
            }
        )


def test_parse_response_rejects_duplicate_missing_fields() -> None:
    with pytest.raises(TelegramIntentLlmError):
        _parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "needs_clarification",
                                    "capabilityId": "limit_buy",
                                    "symbol": "0050",
                                    "quantityLots": 1,
                                    "targetPrice": None,
                                    "missingFields": ["targetPrice", "targetPrice"],
                                }
                            )
                        }
                    }
                ]
            }
        )


def test_deepseek_request_disables_thinking_and_uses_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response(
            b'{"choices":[{"message":{"content":"{\\"decision\\":\\"unsupported\\",\\"missingFields\\":[]}"}}]}'
        )

    monkeypatch.setattr("app.services.telegram_intent_llm.urllib.request.urlopen", fake_urlopen)
    decision = DeepSeekTelegramIntentDecider(_settings()).decide(message="hello")

    assert decision.decision == "unsupported"
    assert captured["timeout"] == 10.0
    request = captured["request"]
    assert isinstance(request, Request)
    body = json.loads(cast(bytes, request.data).decode("utf-8"))
    assert body["thinking"] == {"type": "disabled"}
    assert body["model"] == "deepseek-v4-flash"
