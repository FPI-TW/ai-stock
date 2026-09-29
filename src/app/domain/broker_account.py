"""Broker-account binding domain: the projection, the login material, and the
error vocabulary. Transport-agnostic; `app.api.errors` maps each error to a code.

Login material lives here so the session pool, repository and command can name it
without importing the Fubon package (the `in_memory` runtime must never load
`app.services.quote.fubon*`). Only `broker="fubon"` exists today; a second broker
is when a `broker`-keyed variant would be introduced, not before.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

BrokerLoginFailureCode = Literal["login_rejected", "session_limit", "provider_unavailable", "cert_invalid", "unknown"]

# The only text that may reach `broker_accounts.last_error`, the API and the logs.
# SDK exception text can echo the identity number or password — never store it.
LOGIN_FAILURE_MESSAGES: dict[BrokerLoginFailureCode, str] = {
    "login_rejected": "券商拒絕登入，請確認身分證字號、密碼與憑證",
    "session_limit": "券商連線數已達上限，請稍後再試",
    "provider_unavailable": "券商服務暫時無法使用",
    "cert_invalid": "憑證檔或憑證密碼無效",
    "unknown": "券商登入發生未預期錯誤",
}


@dataclass(frozen=True, repr=False)
class FubonCredentials:
    """One user's Fubon login material, in memory only. Never logged, never persisted here."""

    personal_id: str
    password: str
    cert_pfx: bytes
    cert_password: str

    def __repr__(self) -> str:
        return "FubonCredentials(personal_id='***', password='***', cert_pfx=<bytes>, cert_password='***')"


@dataclass(frozen=True, slots=True)
class BrokerAccountData:
    id: UUID
    user_id: UUID
    broker: str
    broker_account_no: str
    cert_expires_at: datetime
    status: str
    last_login_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class BrokerAccountError(Exception):
    """Base class for broker-account binding failures."""


class BrokerAccountNotBoundError(BrokerAccountError):
    """The user has no broker session (per-user mode) — cannot watch quotes for them."""


class BrokerLoginFailedError(BrokerAccountError):
    """Login with the supplied material failed. Carries only a safe, enumerable code."""

    def __init__(self, code: BrokerLoginFailureCode) -> None:
        super().__init__(code)
        self.code: BrokerLoginFailureCode = code

    @property
    def safe_message(self) -> str:
        return LOGIN_FAILURE_MESSAGES[self.code]


class BrokerSessionLimitReachedError(BrokerAccountError):
    """This deployment's `BROKER_MAX_SESSIONS` is exhausted; binding is refused."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"limit={limit}")
        self.limit = limit


class BrokerBindInProgressError(BrokerAccountError):
    """Another bind / unbind for the same user is mid-flight; retry after it settles."""


class BrokerBindingNotEnabledError(BrokerAccountError):
    """The runtime is in shared-provider mode (`in_memory` / demo): there is no
    per-user broker login to perform, so binding is refused outright. Not retryable."""

    def __init__(self, quote_provider: str) -> None:
        super().__init__(quote_provider)
        self.quote_provider = quote_provider
