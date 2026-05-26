"""Quote evaluation rules — BE-V0.5-09.

Pure decision logic: given a quote and an active intent, decide whether to
trigger. Stateless and side-effect free; persistence, locking and notification
live in the trigger-transaction layer.

`QuoteSnapshot` is imported from the shared quote-provider abstraction
(`app.services.quote.base`) so every provider implementation feeds the same
shape into the evaluator.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.domain.price import PriceService
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import OutsideSessionError, TradingSessionService
from app.services.quote.base import QuoteSnapshot

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
class EvaluationResult:
    should_trigger: bool
    trigger_price: Decimal | None = None
    trigger_reference_price_type: str | None = None
    fallback_used: bool = False
    skip_reason: SkipReason | None = None
    baseline: Decimal | None = None
    dynamic_trigger_price: Decimal | None = None
    baseline_updated_at: datetime | None = None


class QuoteEvaluator:
    """Evaluate a single (quote, intent) pair against V0.5 strategy rules.

    The caller is responsible for filtering quotes to a known symbol set and
    looking up active intents by symbol; this class only decides per pair.
    """

    def __init__(self, session_service: TradingSessionService) -> None:
        self._session = session_service

    def evaluate(self, quote: QuoteSnapshot, intent: TradeIntentData, now: datetime) -> EvaluationResult:
        try:
            self._session.verify_trading_hours(now, quote.quote_time)
        except OutsideSessionError:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.OUTSIDE_SESSION)

        if now - quote.quote_time > QUOTE_FRESHNESS_THRESHOLD:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.QUOTE_STALE)

        validation_failure = self._validate_quote(quote)
        if validation_failure is not None:
            return EvaluationResult(should_trigger=False, skip_reason=validation_failure)

        if intent.strategy in {"buy_price_alert", "limit_buy_order"}:
            return self._evaluate_buy(quote, intent.target_price_effective)
        if intent.strategy in {"sell_price_alert", "limit_sell_order"}:
            return self._evaluate_sell(quote, intent.target_price_effective)
        if intent.strategy == "trailing_stop_alert":
            return self._evaluate_trailing_stop(quote, intent, now)
        return EvaluationResult(should_trigger=False, skip_reason=SkipReason.UNSUPPORTED_STRATEGY)

    @staticmethod
    def _validate_quote(quote: QuoteSnapshot) -> SkipReason | None:
        for price in (quote.bid_price, quote.ask_price, quote.last_price):
            if price is not None and price <= 0:
                return SkipReason.QUOTE_NONPOSITIVE_PRICE
        if quote.bid_price is not None and quote.ask_price is not None and quote.bid_price > quote.ask_price:
            return SkipReason.QUOTE_BID_GT_ASK
        if quote.bid_price is None and quote.ask_price is None and quote.last_price is None:
            return SkipReason.QUOTE_MISSING_ALL_PRICES
        return None

    @staticmethod
    def _evaluate_buy(quote: QuoteSnapshot, target: Decimal | None) -> EvaluationResult:
        if target is None:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.UNSUPPORTED_STRATEGY)
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
    def _evaluate_sell(quote: QuoteSnapshot, target: Decimal | None) -> EvaluationResult:
        if target is None:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.UNSUPPORTED_STRATEGY)
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

    def _evaluate_trailing_stop(
        self,
        quote: QuoteSnapshot,
        intent: TradeIntentData,
        now: datetime,
    ) -> EvaluationResult:
        if intent.trail_mode is None or intent.trail_value is None:
            return EvaluationResult(should_trigger=False, skip_reason=SkipReason.UNSUPPORTED_STRATEGY)

        baseline = intent.baseline
        dynamic_trigger_price = intent.dynamic_trigger_price
        reference_price = self._trailing_reference_price(quote)
        baseline_updated_at: datetime | None = None
        if reference_price is not None and (baseline is None or reference_price > baseline):
            baseline = reference_price
            dynamic_trigger_price = self._calculate_dynamic_trigger_price(intent, baseline)
            baseline_updated_at = now

        if dynamic_trigger_price is None:
            return EvaluationResult(
                should_trigger=False,
                skip_reason=SkipReason.CONDITION_NOT_MET,
                baseline=baseline,
                dynamic_trigger_price=dynamic_trigger_price,
                baseline_updated_at=baseline_updated_at,
            )

        if quote.bid_price is not None:
            return EvaluationResult(
                should_trigger=quote.bid_price <= dynamic_trigger_price,
                trigger_price=quote.bid_price if quote.bid_price <= dynamic_trigger_price else None,
                trigger_reference_price_type="bid" if quote.bid_price <= dynamic_trigger_price else None,
                skip_reason=None if quote.bid_price <= dynamic_trigger_price else SkipReason.CONDITION_NOT_MET,
                baseline=baseline,
                dynamic_trigger_price=dynamic_trigger_price,
                baseline_updated_at=baseline_updated_at,
            )
        if quote.last_price is not None and quote.last_price <= dynamic_trigger_price:
            return EvaluationResult(
                should_trigger=True,
                trigger_price=quote.last_price,
                trigger_reference_price_type="last_fallback",
                fallback_used=True,
                baseline=baseline,
                dynamic_trigger_price=dynamic_trigger_price,
                baseline_updated_at=baseline_updated_at,
            )
        return EvaluationResult(
            should_trigger=False,
            skip_reason=SkipReason.CONDITION_NOT_MET,
            baseline=baseline,
            dynamic_trigger_price=dynamic_trigger_price,
            baseline_updated_at=baseline_updated_at,
        )

    @staticmethod
    def _trailing_reference_price(quote: QuoteSnapshot) -> Decimal | None:
        if quote.last_price is not None:
            return quote.last_price
        if quote.bid_price is not None and quote.ask_price is not None:
            return (quote.bid_price + quote.ask_price) / Decimal("2")
        return None

    @staticmethod
    def _calculate_dynamic_trigger_price(intent: TradeIntentData, baseline: Decimal) -> Decimal:
        if intent.trail_mode is None or intent.trail_value is None:
            raise ValueError("trailing intent requires trail_mode and trail_value")
        if intent.trail_mode == "percentage":
            raw = baseline * (Decimal("1") - (intent.trail_value / Decimal("100")))
        elif intent.trail_mode == "fixed_amount":
            raw = baseline - intent.trail_value
        else:
            raise ValueError(f"Unsupported trail mode: {intent.trail_mode}")
        return PriceService.round_down_to_tick(intent.security_type, raw)
