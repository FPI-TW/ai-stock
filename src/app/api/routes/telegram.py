"""Telegram webhook endpoint for the inbound intent demo."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    DatabaseDep,
    SettingsDep,
    TelegramBotClientDep,
    TelegramIntentCommandDep,
    TelegramIntentInteractionRepoDep,
)
from app.api.errors import ApiError, ErrorCode
from app.commands.telegram_intent import (
    HandleTelegramCallbackInput,
    HandleTelegramMessageInput,
    TelegramIntentCommand,
    TelegramReply,
)

router = APIRouter()


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int | str | None = None
    is_bot: bool = Field(default=False, alias="is_bot")


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | str


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Required by Telegram's Message / MaybeInaccessibleMessage contract and
    # used to bind callback actions to the bot message that rendered them.
    message_id: int
    chat: TelegramChat
    text: str | None = None
    from_user: TelegramUser | None = Field(default=None, alias="from")


class TelegramCallbackQuery(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = None
    data: str | None = None
    message: TelegramMessage | None = None
    from_user: TelegramUser | None = Field(default=None, alias="from")


class TelegramUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None


@router.post("/webhook")
def telegram_webhook(
    request: Request,
    update: TelegramUpdate,
    settings: SettingsDep,
    command: TelegramIntentCommandDep,
    bot: TelegramBotClientDep,
    interactions: TelegramIntentInteractionRepoDep,
    db: DatabaseDep,
) -> dict[str, Any]:
    _verify_secret(request, settings.telegram_webhook_secret)

    chat_id = _update_chat_id(update)
    configured_chat_id = (settings.telegram_chat_id or "").strip()
    if not configured_chat_id or chat_id is None or chat_id != configured_chat_id:
        # Telegram retries webhook deliveries on non-2xx responses.  Ignored
        # chats therefore return 200 and never invoke the LLM or mutate state.
        return {"ok": True, "handled": False}

    callback = update.callback_query
    if callback is not None:
        # A callback must be acknowledged even when its interaction is stale or
        # malformed; this removes Telegram's spinner.  A Bot API failure is
        # swallowed by the client and still does not turn the webhook into a 5xx.
        if callback.id is not None and not (callback.from_user and callback.from_user.is_bot):
            bot.answer_callback_query(callback_query_id=callback.id)
        if callback.from_user is not None and callback.from_user.is_bot:
            return {"ok": True, "handled": False}
        try:
            reply = _handle_callback_update(update, command)
            if reply is None:
                db.commit()
                return {"ok": True, "handled": False}
            db.commit()
        except Exception:
            db.rollback()
            raise
        _edit_callback_message(update, bot, reply)
        return _reply_payload(reply)

    message = update.message
    if message is None or message.text is None or (message.from_user is not None and message.from_user.is_bot):
        return {"ok": True, "handled": False}

    try:
        reply = command.handle_message(HandleTelegramMessageInput(chat_id=chat_id, text=message.text))
        if reply is None:
            db.commit()
            return {"ok": True, "handled": False}
        _deliver_message_reply(reply, bot, interactions)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _reply_payload(reply)


def _verify_secret(request: Request, expected_secret: str | None) -> None:
    actual = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not expected_secret or not actual or not hmac.compare_digest(actual, expected_secret):
        raise ApiError(code=ErrorCode.FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN)


def _update_chat_id(update: TelegramUpdate) -> str | None:
    if update.callback_query is not None and update.callback_query.message is not None:
        return str(update.callback_query.message.chat.id)
    if update.message is not None:
        return str(update.message.chat.id)
    return None


def _handle_callback_update(update: TelegramUpdate, command: TelegramIntentCommand) -> TelegramReply | None:
    callback = update.callback_query
    if callback is None or callback.message is None or callback.data is None:
        return None
    return command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id=str(callback.message.chat.id),
            data=callback.data,
            message_id=callback.message.message_id,
        )
    )


def _deliver_message_reply(
    reply: TelegramReply,
    bot: TelegramBotClientDep,
    interactions: TelegramIntentInteractionRepoDep,
) -> None:
    if reply.bot_message_id is not None:
        # Bot clients return False on a transport/API failure.  Test doubles from
        # the original notification path return None; that is treated as success
        # to preserve their no-throw contract.
        edited = bot.edit_message_text(
            chat_id=reply.chat_id,
            message_id=reply.bot_message_id,
            text=reply.text,
            reply_markup=reply.reply_markup,
        )
        if edited is not False:
            return
    message_id = bot.send_message(chat_id=reply.chat_id, text=reply.text, reply_markup=reply.reply_markup)
    if reply.interaction_id is not None and message_id is not None:
        interactions.attach_bot_message_id(reply.interaction_id, chat_id=reply.chat_id, message_id=message_id)


def _edit_callback_message(update: TelegramUpdate, bot: TelegramBotClientDep, reply: TelegramReply) -> bool:
    callback = update.callback_query
    if callback is None or callback.message is None or callback.message.message_id is None:
        return False
    edited = bot.edit_message_text(
        chat_id=str(callback.message.chat.id),
        message_id=callback.message.message_id,
        text=reply.text,
        reply_markup={"inline_keyboard": []},
    )
    return edited is not False


# Kept as a small compatibility helper for unit tests and local integrations.
def _try_edit_callback_message(update: TelegramUpdate, bot: TelegramBotClientDep, reply: TelegramReply) -> bool:
    callback = update.callback_query
    if callback is not None and callback.id is not None:
        bot.answer_callback_query(callback_query_id=callback.id)
    return _edit_callback_message(update, bot, reply)


def _reply_payload(reply: TelegramReply) -> dict[str, Any]:
    return {
        "ok": True,
        "handled": True,
        "reply": {
            "chatId": reply.chat_id,
            "text": reply.text,
            "replyMarkup": reply.reply_markup,
            "interactionId": str(reply.interaction_id) if reply.interaction_id is not None else None,
        },
    }
