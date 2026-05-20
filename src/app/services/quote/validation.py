"""Quote validation rules shared across every provider.

Validation is intentionally provider-agnostic — both the in-memory provider
(tests) and the runtime broker-backed provider feed snapshots through the same
checks before they reach the evaluator. V1 licensed providers reuse this module
as-is.

V0.5 rules:
- Symbol must be tradable (caller responsibility — passed in).
- `quote_time` must fall inside the regular session.
- Bid <= ask when both are present.
- Every present price > 0.
- At least one of bid / ask / last is present.
- If bid or ask is missing, `fallback_used=True` so the evaluator can fallback to last
  with the appropriate trigger_reference_price_type.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteSnapshot


class QuoteValidationError(ValueError):
    """Raised when a snapshot fails any structural check.

    Provider-side this should never escape the provider layer — it indicates
    a normalisation bug or a hostile broker payload. The evaluator should
    skip the symbol on this error rather than triggering on garbage data.
    """

    def __init__(self, reason: str, snapshot: QuoteSnapshot) -> None:
        super().__init__(f"{reason}: {snapshot.symbol}")
        self.reason = reason
        self.snapshot = snapshot


@dataclass(frozen=True)
class ValidatedQuote:
    """Validated snapshot ready for evaluation.

    `fallback_used=True` means bid or ask is missing and the evaluator must consult
    `last_price` via the `last_fallback` trigger reference, instead of the default
    ask/bid reference for buy/sell alerts.
    """

    snapshot: QuoteSnapshot
    fallback_used: bool


class QuoteValidator:
    def __init__(self, session_service: TradingSessionService) -> None:
        self._session = session_service

    def validate(self, snapshot: QuoteSnapshot, *, now: datetime | None = None) -> ValidatedQuote:
        self._require_session(snapshot, now=now)
        self._require_at_least_one_price(snapshot)
        self._require_positive_prices(snapshot)
        self._require_bid_le_ask(snapshot)
        fallback = snapshot.bid_price is None or snapshot.ask_price is None
        if fallback and snapshot.last_price is None:
            # Already caught by `_require_at_least_one_price` above when all three
            # are None, but check again here so the fallback flag is only set when
            # last_price actually exists.
            raise QuoteValidationError("missing both bid/ask and last for fallback", snapshot)
        return ValidatedQuote(snapshot=snapshot, fallback_used=fallback)

    def _require_session(self, snapshot: QuoteSnapshot, *, now: datetime | None) -> None:
        current = now if now is not None else self._session.now_taipei()
        if not self._session.is_within_regular_session(current):
            raise QuoteValidationError("now outside regular session", snapshot)
        if not self._session.is_within_regular_session(snapshot.quote_time):
            raise QuoteValidationError("quote_time outside regular session", snapshot)

    @staticmethod
    def _require_at_least_one_price(snapshot: QuoteSnapshot) -> None:
        if snapshot.bid_price is None and snapshot.ask_price is None and snapshot.last_price is None:
            raise QuoteValidationError("bid, ask, and last all missing", snapshot)

    @staticmethod
    def _require_positive_prices(snapshot: QuoteSnapshot) -> None:
        for label, value in (("bid", snapshot.bid_price), ("ask", snapshot.ask_price), ("last", snapshot.last_price)):
            if value is not None and value <= Decimal(0):
                raise QuoteValidationError(f"{label}_price must be > 0", snapshot)

    @staticmethod
    def _require_bid_le_ask(snapshot: QuoteSnapshot) -> None:
        if snapshot.bid_price is not None and snapshot.ask_price is not None:
            if snapshot.bid_price > snapshot.ask_price:
                raise QuoteValidationError("bid_price > ask_price (crossed quote)", snapshot)
