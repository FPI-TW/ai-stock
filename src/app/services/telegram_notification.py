import json
import logging
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import Settings, get_settings
from app.db.models.core import Notification

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramNotificationSender:
    """Best-effort Telegram sender for V0.5 notification rows."""

    bot_token: str | None
    chat_id: str | None
    timeout_seconds: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramNotificationSender":
        return cls(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            timeout_seconds=settings.telegram_timeout_seconds,
        )

    @property
    def enabled(self) -> bool:
        return bool((self.bot_token or "").strip() and (self.chat_id or "").strip())

    def send_notification(self, notification: Notification) -> bool:
        return self.send_message(
            title=notification.rendered_title,
            body=notification.rendered_body,
            notification_type=notification.type,
        )

    def send_message(self, *, title: str, body: str, notification_type: str) -> bool:
        if not self.enabled:
            return False

        token = (self.bot_token or "").strip()
        chat_id = (self.chat_id or "").strip()
        payload = {
            "chat_id": chat_id,
            "text": f"{title}\n\n{body}".strip(),
            "disable_web_page_preview": True,
        }
        request = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                response.read()
        except HTTPError as exc:
            logger.warning(
                "telegram notification send failed",
                extra={"notification_type": notification_type, "status_code": exc.code},
            )
            return False
        except (TimeoutError, URLError, OSError) as exc:
            logger.warning(
                "telegram notification send failed",
                extra={"notification_type": notification_type, "error_type": type(exc).__name__},
            )
            return False
        return True


def dispatch_notification_to_telegram(notification: Notification, settings: Settings | None = None) -> None:
    """Send a notification row to Telegram without affecting the DB transaction."""

    sender = TelegramNotificationSender.from_settings(settings or get_settings())
    sender.send_notification(notification)
