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


class SesMailer:
    """Real sender via AWS SES SMTP endpoint (email-smtp.<region>.amazonaws.com),
    using SES SMTP credentials over STARTTLS. In the SES sandbox both sender and
    recipient must be verified identities.
    """

    def __init__(self, from_address: str, region: str, smtp_username: str, smtp_password: str) -> None:
        self._from = from_address
        self._host = f"email-smtp.{region}.amazonaws.com"
        self._username = smtp_username
        self._password = smtp_password

    def send(self, message: MailMessage) -> None:
        import smtplib
        from email.message import EmailMessage

        mail = EmailMessage()
        mail["From"] = self._from
        mail["To"] = message.to
        mail["Subject"] = message.subject
        mail.set_content(message.body)

        # Port 465 = implicit TLS; more robust than 587 + STARTTLS on locked-down networks.
        with smtplib.SMTP_SSL(self._host, 465, timeout=10) as smtp:
            smtp.login(self._username, self._password)
            smtp.send_message(mail)
        # Never log the raw body/token — recipient + subject only.
        logger.info("mail send (ses)", extra={"to": message.to, "subject": message.subject})
