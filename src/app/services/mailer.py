"""Mailer abstraction. V1 ships a logging stub; P3/R3 inject a real SMTP/SES
sender behind the same Protocol. Never log the raw invitation / reset token at
info level in a real sender — the stub logs only a redacted summary.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MailMessage:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, message: MailMessage) -> None: ...


class LoggingMailer:
    """Dev/V1 stub: records that a mail would have been sent, without delivering it."""

    def send(self, message: MailMessage) -> None:
        logger.info("mail send (stub)", extra={"to": message.to, "subject": message.subject})
