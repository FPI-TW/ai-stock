"""Quote evaluation rules — BE-V0.5-09.

Pure decision logic: given a quote and an active intent, decide whether to
trigger. Stateless and side-effect free; persistence, locking and notification
live in the trigger-transaction layer.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import OutsideSessionError, TradingSessionService

# Spec §11: now - quote_time <= 10s (inclusive)
QUOTE_FRESHNESS_THRESHOLD = timedelta(seconds=10)


class SkipReason(StrEnum):
    OUTSIDE_SESSION = "outside_session"
    QUOTE_STALE = "quote_stale"
    QUOTE_BID_GT_ASK = "quote_bid_gt_ask"
    QUOTE_NONPOSITIVE_PRICE = "quote_nonpositive_price"
    QUOTE_MISSING_ALL_PRICES = "quote_missing_all_prices"
    CONDITION_NOT_MET = "condition_not_met"
    UNSUPPORTED_STRATEGY = "unsupported_strategy"


@dataclass(frozen=True)
class Quote:
    """V0.5 evaluator quote shape.

    Spec §11 normalized quote also carries `source`, `source_latency_label`,
    `raw_payload_ref` and `received_at`. Those belong in the persisted
    `trigger_events.quote_snapshot` JSONB but are not required by the decision
    logic, so they are kept out of this dataclass.
    """

    symbol: str
    quote_time: datetime
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    last_price: Decimal | None = None


@dataclass(frozen=True)
class EvaluationResult:
    should_trigger: bool
    trigger_price: Decimal | None = None
    trigger_reference_price_type: str | None = None
    fallback_used: bool = False
    skip_reason: SkipReason | None = None


class QuoteEvaluator:
    """Evaluate a single (quote, intent) pair against V0.5 strategy rules.

    The caller is responsible for filtering quotes to a known symbol set and
    looking up active intents by symbol; this class only decides per pair.
    """

    def __init__(self, session_service: TradingSessionService) -> None:
        self._session = session_service

    def evaluate(self, quote: Quote, intent: TradeIntentData, now: datetime) -> EvaluationResult:
        try:
            self._session.verify_trading_hours(now, quote.quote_time)
        except OutsideSessionError:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.OUTSIDE_SESSION)

        if now - quote.quote_time > QUOTE_FRESHNESS_THRESHOLD:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.QUOTE_STALE)

        validation_failure = self._validate_quote(quote)
        if validation_failure is not None:
            return EvaluationResult(should_trigger=False, skip_reason=validation_failure)

        if intent.strategy == "buy_price_alert":
            return self._evaluate_buy(quote, intent.target_price_effective)
        if intent.strategy == "sell_price_alert":
            return self._evaluate_sell(quote, intent.target_price_effective)
        return EvaluationResult(should_trigger=False, skip_reason=SkipReason.UNSUPPORTED_STRATEGY)

    @staticmethod
    def _validate_quote(quote: Quote) -> SkipReason | None:
        for price in (quote.bid_price, quote.ask_price, quote.last_price):
            if price is not None and price <= 0:
                return SkipReason.QUOTE_NONPOSITIVE_PRICE
        if quote.bid_price is not None and quote.ask_price is not None and quote.bid_price > quote.ask_price:
            return SkipReason.QUOTE_BID_GT_ASK
        if quote.bid_price is None and quote.ask_price is None and quote.last_price is None:
            return SkipReason.QUOTE_MISSING_ALL_PRICES
        return None

    @staticmethod
    def _evaluate_buy(quote: Quote, target: Decimal) -> EvaluationResult:
        # Spec §4: prefer ask; only fall back to last when ask is missing.
        if quote.ask_price is not None:
            if quote.ask_price <= target:
                return EvaluationResult(
                    should_trigger=True,
                    trigger_price=quote.ask_price,
                    trigger_reference_price_type="ask",
                )
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.CONDITION_NOT_MET)
        if quote.last_price is not None and quote.last_price <= target:
            return EvaluationResult(
                should_trigger=True,
                trigger_price=quote.last_price,
                trigger_reference_price_type="last_fallback",
                fallback_used=True,
            )
        return EvaluationResult(should_trigger=False, skip_reason=SkipReason.CONDITION_NOT_MET)

    @staticmethod
    def _evaluate_sell(quote: Quote, target: Decimal) -> EvaluationResult:
        if quote.bid_price is not None:
            if quote.bid_price >= target:
                return EvaluationResult(
                    should_trigger=True,
                    trigger_price=quote.bid_price,
                    trigger_reference_price_type="bid",
                )
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.CONDITION_NOT_MET)
        if quote.last_price is not None and quote.last_price >= target:
            return EvaluationResult(
                should_trigger=True,
                trigger_price=quote.last_price,
                trigger_reference_price_type="last_fallback",
                fallback_used=True,
            )
        return EvaluationResult(should_trigger=False, skip_reason=SkipReason.CONDITION_NOT_MET)
