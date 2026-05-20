"""Demo subscription quota — Shioaji free tier caps at 5 concurrent symbols."""

from fastapi import status

from app.services.quote.base import QuoteProviderError

DEFAULT_MAX_SUBSCRIPTIONS = 5


class QuoteSubscriptionLimitExceeded(QuoteProviderError):
    """The provider has hit the demo subscription cap.

    Demo-only: removed at V1 migration. Licensed vendors with their own quota
    rules must introduce a separate error class instead of reusing this one.
    """

    error_code = "QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED"
    http_status = status.HTTP_409_CONFLICT
    default_message = "目前訂閱數已達 Shioaji demo 上限"

    def __init__(self, *, symbol: str, current: int, limit: int) -> None:
        super().__init__(f"limit={limit} reached when subscribing {symbol}")
        self.symbol = symbol
        self.current = current
        self.limit = limit

    def details(self) -> dict[str, object]:
        return {"symbol": self.symbol, "current": self.current, "limit": self.limit}
