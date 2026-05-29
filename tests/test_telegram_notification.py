import json
from datetime import datetime
from email.message import Message
from types import TracebackType
from urllib.error import HTTPError
from urllib.request import Request
from uuid import uuid4

import pytest

from app.db.models.core import Notification
from app.services.telegram_notification import TelegramNotificationSender


class FakeResponse:
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok":true}'


def _notification() -> Notification:
    return Notification(
        id=uuid4(),
        owner_user_id=uuid4(),
        trade_intent_id=uuid4(),
        type="price_triggered",
        rendered_title="2330 到價提醒已觸發",
        rendered_body="買進到價提醒\n\n僅通知、未下單、不保證成交。",
        created_at=datetime(2026, 5, 29),
        updated_at=datetime(2026, 5, 29),
    )


def test_sender_is_disabled_without_complete_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(_request: Request, timeout: float) -> FakeResponse:
        raise AssertionError(f"urlopen should not be called: {timeout}")

    monkeypatch.setattr("app.services.telegram_notification.urlopen", fail_urlopen)

    assert TelegramNotificationSender(bot_token=None, chat_id="chat").send_notification(_notification()) is False
    assert TelegramNotificationSender(bot_token="token", chat_id="").send_notification(_notification()) is False


def test_sender_posts_notification_to_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.telegram_notification.urlopen", fake_urlopen)

    sent = TelegramNotificationSender(
        bot_token="token-1",
        chat_id="chat-1",
        timeout_seconds=2.5,
    ).send_notification(_notification())

    assert sent is True
    request = captured["request"]
    assert isinstance(request, Request)
    assert request.full_url == "https://api.telegram.org/bottoken-1/sendMessage"
    assert captured["timeout"] == 2.5
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {
        "chat_id": "chat-1",
        "text": "2330 到價提醒已觸發\n\n買進到價提醒\n\n僅通知、未下單、不保證成交。",
        "disable_web_page_preview": True,
    }


def test_sender_swallow_telegram_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(_request: Request, timeout: float) -> FakeResponse:
        raise HTTPError("https://api.telegram.org/bot<redacted>/sendMessage", 500, "server error", Message(), None)

    monkeypatch.setattr("app.services.telegram_notification.urlopen", fake_urlopen)

    sent = TelegramNotificationSender(bot_token="token-1", chat_id="chat-1").send_notification(_notification())

    assert sent is False
