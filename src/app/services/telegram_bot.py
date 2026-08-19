"""Best-effort Telegram Bot API client used by inbound interactions."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)


class TelegramBotClient:
    def __init__(self, settings: Settings) -> None:
        self._token = (settings.telegram_bot_token or "").strip()
        self._timeout = settings.telegram_timeout_seconds

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> int | None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = self._post("sendMessage", payload, failure_log="telegram message send failed")
        if response is None:
            return None
        result = response.get("result")
        if not isinstance(result, dict):
            return None
        message_id = result.get("message_id")
        return message_id if isinstance(message_id, int) else None

    def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._post("editMessageText", payload, failure_log="telegram message edit failed") is not None

    def clear_reply_markup(self, *, chat_id: str, message_id: int) -> bool:
        return (
            self._post(
                "editMessageReplyMarkup",
                {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
                failure_log="telegram reply markup cleanup failed",
            )
            is not None
        )

    def answer_callback_query(self, *, callback_query_id: str) -> bool:
        return (
            self._post(
                "answerCallbackQuery",
                {"callback_query_id": callback_query_id},
                failure_log="telegram callback acknowledgement failed",
            )
            is not None
        )

    def _post(self, method: str, payload: dict[str, Any], *, failure_log: str) -> dict[str, Any] | None:
        if not self._token:
            logger.info("telegram bot token not configured; skipping request", extra={"method": method})
            return None
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status_code = getattr(response, "status", 200)
                if isinstance(status_code, int) and status_code >= 400:
                    logger.warning(failure_log, extra={"method": method, "status_code": status_code})
                    return None
                read = getattr(response, "read", None)
                if not callable(read):
                    return {}
                raw = read()
                if not raw:
                    return {}
                decoded = json.loads(raw.decode("utf-8"))
                if not isinstance(decoded, dict) or decoded.get("ok") is False:
                    logger.warning(failure_log, extra={"method": method})
                    return None
                return decoded
        except (TimeoutError, urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            # Never include the request URL or exception in logs: both can contain
            # the bot token (HTTPError's URL does).
            logger.warning(failure_log, extra={"method": method})
            return None
