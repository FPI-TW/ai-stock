from __future__ import annotations

import json
from types import TracebackType
from typing import cast
from urllib.error import URLError
from urllib.request import Request
from uuid import UUID

import pytest

from app.core.config import Settings
from app.services.telegram_bot import TelegramBotClient


class _Response:
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok":true,"result":{"message_id":42}}'


def test_bot_client_sends_and_returns_message_id_without_logging_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("app.services.telegram_bot.urllib.request.urlopen", fake_urlopen)
    settings = Settings(
        LOCAL_USER_ID=UUID("00000000-0000-0000-0000-000000000001"),
        QUOTE_PROVIDER="in_memory",
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_TIMEOUT_SECONDS=2.5,
    )

    message_id = TelegramBotClient(settings).send_message(
        chat_id="configured-group",
        text="draft",
        reply_markup={"inline_keyboard": []},
    )

    assert message_id == 42
    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url.endswith("/sendMessage")
    assert captured["timeout"] == 2.5
    assert json.loads(cast(bytes, request.data).decode("utf-8")) == {
        "chat_id": "configured-group",
        "text": "draft",
        "reply_markup": {"inline_keyboard": []},
    }


def test_bot_client_edits_clears_markup_and_answers_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        requests.append(request)
        return _Response()

    monkeypatch.setattr("app.services.telegram_bot.urllib.request.urlopen", fake_urlopen)
    settings = Settings(
        LOCAL_USER_ID=UUID("00000000-0000-0000-0000-000000000001"),
        QUOTE_PROVIDER="in_memory",
        TELEGRAM_BOT_TOKEN="test-token",
    )
    client = TelegramBotClient(settings)

    assert client.edit_message_text(chat_id="configured-group", message_id=42, text="done") is True
    assert client.clear_reply_markup(chat_id="configured-group", message_id=42) is True
    assert client.answer_callback_query(callback_query_id="callback") is True

    assert [request.full_url.rsplit("/", 1)[-1] for request in requests] == [
        "editMessageText",
        "editMessageReplyMarkup",
        "answerCallbackQuery",
    ]
    assert json.loads(cast(bytes, requests[1].data).decode("utf-8"))["reply_markup"] == {"inline_keyboard": []}
    assert json.loads(cast(bytes, requests[2].data).decode("utf-8")) == {"callback_query_id": "callback"}


def test_bot_client_swallow_transport_failure_for_all_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(_request: Request, timeout: float) -> _Response:
        raise URLError("offline")

    monkeypatch.setattr("app.services.telegram_bot.urllib.request.urlopen", fail_urlopen)
    settings = Settings(
        LOCAL_USER_ID=UUID("00000000-0000-0000-0000-000000000001"),
        QUOTE_PROVIDER="in_memory",
        TELEGRAM_BOT_TOKEN="test-token",
    )
    client = TelegramBotClient(settings)

    assert client.send_message(chat_id="configured-group", text="draft") is None
    assert client.edit_message_text(chat_id="configured-group", message_id=42, text="done") is False
    assert client.answer_callback_query(callback_query_id="callback") is False
