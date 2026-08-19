from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import (
    get_db,
    get_settings,
    get_telegram_bot_client,
    get_telegram_intent_command,
    get_telegram_intent_interaction_repository,
)
from app.api.errors import register_exception_handlers
from app.api.routes.telegram import TelegramUpdate, _deliver_message_reply, _try_edit_callback_message
from app.commands.telegram_intent import HandleTelegramCallbackInput, HandleTelegramMessageInput, TelegramReply
from app.core.config import Settings


class _Bot:
    def __init__(self) -> None:
        self.answered: list[str] = []
        self.edited: list[dict[str, object]] = []

    def answer_callback_query(self, *, callback_query_id: str) -> bool:
        self.answered.append(callback_query_id)
        return True

    def edit_message_text(self, **kwargs: object) -> bool:
        self.edited.append(kwargs)
        return True


def test_callback_helper_acknowledges_and_removes_buttons_from_original_message() -> None:
    bot = _Bot()
    update = TelegramUpdate.model_validate(
        {
            "callback_query": {
                "id": "callback-under-test",
                "data": "tg_intent:cancel:00000000-0000-0000-0000-000000000001",
                "message": {"message_id": 123, "chat": {"id": "configured-group"}},
            }
        }
    )

    edited = _try_edit_callback_message(update, bot, TelegramReply(chat_id="configured-group", text="已取消。"))  # type: ignore[arg-type]

    assert edited is True
    assert bot.answered == ["callback-under-test"]
    assert bot.edited == [
        {
            "chat_id": "configured-group",
            "message_id": 123,
            "text": "已取消。",
            "reply_markup": {"inline_keyboard": []},
        }
    ]


class _Db:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _Command:
    def __init__(self, *, silent: bool = False) -> None:
        self.messages: list[HandleTelegramMessageInput] = []
        self.callbacks: list[HandleTelegramCallbackInput] = []
        self.silent = silent

    def handle_message(self, inp: HandleTelegramMessageInput) -> TelegramReply | None:
        self.messages.append(inp)
        return None if self.silent else TelegramReply(chat_id=inp.chat_id, text="測試回覆")

    def handle_callback(self, inp: HandleTelegramCallbackInput) -> TelegramReply | None:
        self.callbacks.append(inp)
        if inp.message_id != 123:
            return None
        return TelegramReply(chat_id=inp.chat_id, text="已取消。")


class _MessageBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.edited: list[dict[str, object]] = []
        self.answered: list[str] = []

    def send_message(self, **kwargs: object) -> int:
        self.sent.append(kwargs)
        return 123

    def edit_message_text(self, **kwargs: object) -> bool:
        self.edited.append(kwargs)
        return True

    def answer_callback_query(self, *, callback_query_id: str) -> bool:
        self.answered.append(callback_query_id)
        return True


class _Interactions:
    def __init__(self) -> None:
        self.attached = 0

    def attach_bot_message_id(self, **kwargs: object) -> bool:
        self.attached += 1
        return True


def _endpoint_client(*, silent: bool = False) -> tuple[TestClient, _Command, _MessageBot, _Db]:
    app = FastAPI()
    register_exception_handlers(app)
    from app.api.routes.telegram import router

    app.include_router(router, prefix="/telegram")
    command = _Command(silent=silent)
    bot = _MessageBot()
    db = _Db()
    app.dependency_overrides[get_settings] = lambda: Settings(
        LOCAL_USER_ID=UUID("00000000-0000-0000-0000-000000000001"),
        QUOTE_PROVIDER="in_memory",
        TELEGRAM_CHAT_ID="configured-group",
        TELEGRAM_WEBHOOK_SECRET="test-secret",
    )
    app.dependency_overrides[get_telegram_intent_command] = lambda: command  # type: ignore[assignment]
    app.dependency_overrides[get_telegram_bot_client] = lambda: bot  # type: ignore[assignment]
    app.dependency_overrides[get_telegram_intent_interaction_repository] = lambda: _Interactions()  # type: ignore[assignment]
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    return TestClient(app), command, bot, db


def test_webhook_requires_secret_and_ignores_other_chat_and_bot_message() -> None:
    client, command, bot, db = _endpoint_client()
    update = {"message": {"message_id": 1, "chat": {"id": "configured-group"}, "text": "hello"}}

    assert client.post("/telegram/webhook", json=update).status_code == 403
    assert command.messages == []

    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
    ignored = client.post(
        "/telegram/webhook",
        json={"message": {"message_id": 2, "chat": {"id": "other-group"}, "text": "hello"}},
        headers=headers,
    )
    assert ignored.status_code == 200
    assert ignored.json() == {"ok": True, "handled": False}

    bot_update = {
        "message": {
            "message_id": 3,
            "chat": {"id": "configured-group"},
            "text": "bot echo",
            "from": {"id": 7, "is_bot": True},
        }
    }
    bot_response = client.post("/telegram/webhook", json=bot_update, headers=headers)
    assert bot_response.status_code == 200
    assert bot_response.json() == {"ok": True, "handled": False}
    assert command.messages == []
    assert bot.sent == []
    assert db.commits == 0

    callback_without_message_id = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "callback-without-message",
                "data": "tg_intent:confirm:00000000-0000-0000-0000-000000000001",
                "message": {"chat": {"id": "configured-group"}},
            }
        },
        headers=headers,
    )
    assert callback_without_message_id.status_code == 422

    non_text_response = client.post(
        "/telegram/webhook",
        json={
            "message": {
                "message_id": 4,
                "chat": {"id": "configured-group"},
                "photo": [{"file_id": "photo"}],
            }
        },
        headers=headers,
    )
    assert non_text_response.status_code == 200
    assert non_text_response.json() == {"ok": True, "handled": False}
    assert command.messages == []
    assert bot.sent == []
    assert db.commits == 0


def test_allowed_text_is_handled_and_sent() -> None:
    client, command, bot, db = _endpoint_client()
    response = client.post(
        "/telegram/webhook",
        json={"message": {"message_id": 5, "chat": {"id": "configured-group"}, "text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["handled"] is True
    assert len(command.messages) == 1
    assert bot.sent == [{"chat_id": "configured-group", "text": "測試回覆", "reply_markup": None}]
    assert db.commits == 1


def test_existing_bot_message_is_edited_without_sending_a_second_message() -> None:
    bot = _MessageBot()
    interactions = _Interactions()
    _deliver_message_reply(
        TelegramReply(chat_id="configured-group", text="updated", bot_message_id=321),
        bot,  # type: ignore[arg-type]
        interactions,  # type: ignore[arg-type]
    )

    assert bot.edited == [{"chat_id": "configured-group", "message_id": 321, "text": "updated", "reply_markup": None}]
    assert bot.sent == []
    assert interactions.attached == 0


def test_non_trading_text_is_silent_after_command_classification() -> None:
    client, command, bot, db = _endpoint_client(silent=True)
    response = client.post(
        "/telegram/webhook",
        json={"message": {"message_id": 6, "chat": {"id": "configured-group"}, "text": "ordinary chat"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handled": False}
    assert len(command.messages) == 1
    assert bot.sent == []
    assert db.commits == 1


def test_callback_message_id_is_propagated_and_wrong_message_is_ignored() -> None:
    client, command, bot, db = _endpoint_client()
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}

    valid = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "valid-callback",
                "data": "tg_intent:cancel:00000000-0000-0000-0000-000000000001",
                "message": {"message_id": 123, "chat": {"id": "configured-group"}},
            }
        },
        headers=headers,
    )

    assert valid.status_code == 200
    assert valid.json()["handled"] is True
    assert command.callbacks[-1].message_id == 123
    assert bot.answered == ["valid-callback"]
    assert bot.edited[-1]["message_id"] == 123

    wrong = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "wrong-message-callback",
                "data": "tg_intent:cancel:00000000-0000-0000-0000-000000000001",
                "message": {"message_id": 999, "chat": {"id": "configured-group"}},
            }
        },
        headers=headers,
    )

    assert wrong.status_code == 200
    assert wrong.json() == {"ok": True, "handled": False}
    assert command.callbacks[-1].message_id == 999
    assert len(bot.edited) == 1
    assert db.commits == 2
