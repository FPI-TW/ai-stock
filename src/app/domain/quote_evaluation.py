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
from typing import Literal

from app.domain.price import PriceService, SecurityType
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
class WatermarkMutation:
    """Side-effect of a trailing evaluation that the caller must persist.

    Spec §117/§152: every valid quote that moves the watermark must write
    ``watermark_high/low``, ``dynamic_trigger_price``, ``watermark_updated_at``
    back to ``trade_intents`` so that an evaluator restart doesn't reset the
    trail. Keeping this as a returned value (not a DB call inside the
    evaluator) preserves the evaluator's pure-logic contract; the dispatcher
    is responsible for actually writing it.
    """

    position_side: Literal["long", "short"]
    watermark: Decimal  # new high when position_side='long', new low when 'short'
    dynamic_trigger_price: Decimal
    updated_at: datetime


@dataclass(frozen=True)
class EvaluationResult:
    should_trigger: bool
    trigger_price: Decimal | None = None
    trigger_reference_price_type: str | None = None
    fallback_used: bool = False
    skip_reason: SkipReason | None = None
    # Trailing-only: populated when the watermark moved on this quote. Always
    # None for non-trailing strategies. Watermark mutation is independent of
    # ``should_trigger`` — a quote may update the watermark without triggering,
    # trigger without updating, both, or neither.
    watermark_mutation: WatermarkMutation | None = None


class QuoteEvaluator:
    """Evaluate a single (quote, intent) pair against V0.5 strategy rules.

    The caller is responsible for filtering quotes to a known symbol set and
    looking up active intents by symbol; this class only decides per pair.
    """

    def __init__(self, session_service: TradingSessionService) -> None:
        self._session = session_service

    def evaluate(
        self,
        quote: QuoteSnapshot,
        intent: TradeIntentData,
        now: datetime,
        *,
        security_type: SecurityType | None = None,
    ) -> EvaluationResult:
        """Evaluate one (quote, intent) pair.

        ``security_type`` is only consumed by the trailing branch (for tick
        rounding of ``dynamic_trigger_price``) — buy/sell strategies ignore
        it. The caller (dispatcher / create-immediate-trigger path) looks up
        the type once per symbol and passes it in; the evaluator itself stays
        out of the symbol-table dependency.
        """
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
            # DB ck `trailing_field_exclusivity` guarantees non-null for buy/sell.
            assert intent.target_price_effective is not None
            return self._evaluate_buy(quote, intent.target_price_effective)
        if intent.strategy == "sell_price_alert":
            assert intent.target_price_effective is not None
            return self._evaluate_sell(quote, intent.target_price_effective)
        if intent.strategy == "trailing_stop_alert":
            if security_type is None:
                # Internal contract violation — dispatcher / immediate-trigger
                # caller must look up the symbol's instrument_type before
                # calling for trailing intents.
                raise ValueError(f"trailing_stop_alert evaluation requires security_type (intent={intent.id})")
            return self._evaluate_trailing(quote, intent, security_type, now)
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
    def _evaluate_buy(quote: QuoteSnapshot, target: Decimal) -> EvaluationResult:
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
    def _evaluate_sell(quote: QuoteSnapshot, target: Decimal) -> EvaluationResult:
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

    @staticmethod
    def _reference_price(quote: QuoteSnapshot) -> Decimal | None:
        """Pick the price used to move the watermark (spec §122 / §149).

        Priority: ``last_price`` for primary, ``mid(bid, ask)`` for fallback.
        Returns None when neither is computable — caller skips watermark
        mutation but may still check trigger against any prior dynamic price.
        """
        if quote.last_price is not None:
            return quote.last_price
        if quote.bid_price is not None and quote.ask_price is not None:
            return (quote.bid_price + quote.ask_price) / Decimal(2)
        return None

    @staticmethod
    def _compute_dynamic_trigger_price(
        watermark: Decimal,
        trail_mode: str,
        trail_value: Decimal,
        position_side: Literal["long", "short"],
        security_type: SecurityType,
    ) -> Decimal:
        """Raw dynamic_trigger_price then tick-rounded away from trigger.

        Long: ``watermark × (1 − trail/100)`` or ``watermark − trail``, floored.
        Short: ``watermark × (1 + trail/100)`` or ``watermark + trail``, ceiled.
        """
        if position_side == "long":
            raw = (
                watermark * (Decimal(1) - trail_value / Decimal(100))
                if trail_mode == "percentage"
                else watermark - trail_value
            )
            return PriceService.round_to_tick_away_from_trigger(security_type, raw, "down")
        raw = (
            watermark * (Decimal(1) + trail_value / Decimal(100))
            if trail_mode == "percentage"
            else watermark + trail_value
        )
        return PriceService.round_to_tick_away_from_trigger(security_type, raw, "up")

    def _evaluate_trailing(
        self,
        quote: QuoteSnapshot,
        intent: TradeIntentData,
        security_type: SecurityType,
        now: datetime,
    ) -> EvaluationResult:
        # DB ck `trailing_field_exclusivity` guarantees these for trailing.
        assert intent.position_side in ("long", "short")
        assert intent.trail_mode in ("percentage", "fixed_amount")
        assert intent.trail_value is not None
        position_side: Literal["long", "short"] = "long" if intent.position_side == "long" else "short"

        # 1. Watermark update — only when reference moves favorably.
        reference = self._reference_price(quote)
        watermark_mutation: WatermarkMutation | None = None
        if reference is not None:
            current = intent.watermark_high if position_side == "long" else intent.watermark_low
            moved = current is None or (reference > current if position_side == "long" else reference < current)
            if moved:
                dynamic = self._compute_dynamic_trigger_price(
                    reference, intent.trail_mode, intent.trail_value, position_side, security_type
                )
                watermark_mutation = WatermarkMutation(
                    position_side=position_side,
                    watermark=reference,
                    dynamic_trigger_price=dynamic,
                    updated_at=now,
                )

        # 2. Effective dynamic for trigger comparison = freshly computed value
        # if watermark moved this quote, else the previously persisted value.
        effective_dynamic = (
            watermark_mutation.dynamic_trigger_price if watermark_mutation is not None else intent.dynamic_trigger_price
        )
        if effective_dynamic is None:
            # No basis to trigger yet (first quote couldn't form a reference).
            return EvaluationResult(
                should_trigger=False,
                skip_reason=SkipReason.CONDITION_NOT_MET,
                watermark_mutation=watermark_mutation,
            )

        # 3. Trigger condition: long → bid <= dynamic (last fallback);
        # short → ask >= dynamic (last fallback). Spec §159, §165.
        if position_side == "long":
            return self._trailing_long_trigger(quote, effective_dynamic, watermark_mutation)
        return self._trailing_short_trigger(quote, effective_dynamic, watermark_mutation)

    @staticmethod
    def _trailing_long_trigger(
        quote: QuoteSnapshot,
        dynamic: Decimal,
        mutation: WatermarkMutation | None,
    ) -> EvaluationResult:
        if quote.bid_price is not None:
            if quote.bid_price <= dynamic:
                return EvaluationResult(
                    should_trigger=True,
                    trigger_price=quote.bid_price,
                    trigger_reference_price_type="bid",
                    watermark_mutation=mutation,
                )
            return EvaluationResult(
                should_trigger=False,
                skip_reason=SkipReason.CONDITION_NOT_MET,
                watermark_mutation=mutation,
            )
        if quote.last_price is not None and quote.last_price <= dynamic:
            return EvaluationResult(
                should_trigger=True,
                trigger_price=quote.last_price,
                trigger_reference_price_type="last_fallback",
                fallback_used=True,
                watermark_mutation=mutation,
            )
        return EvaluationResult(
            should_trigger=False,
            skip_reason=SkipReason.CONDITION_NOT_MET,
            watermark_mutation=mutation,
        )

    @staticmethod
    def _trailing_short_trigger(
        quote: QuoteSnapshot,
        dynamic: Decimal,
        mutation: WatermarkMutation | None,
    ) -> EvaluationResult:
        if quote.ask_price is not None:
            if quote.ask_price >= dynamic:
                return EvaluationResult(
                    should_trigger=True,
                    trigger_price=quote.ask_price,
                    trigger_reference_price_type="ask",
                    watermark_mutation=mutation,
                )
            return EvaluationResult(
                should_trigger=False,
                skip_reason=SkipReason.CONDITION_NOT_MET,
                watermark_mutation=mutation,
            )
        if quote.last_price is not None and quote.last_price >= dynamic:
            return EvaluationResult(
                should_trigger=True,
                trigger_price=quote.last_price,
                trigger_reference_price_type="last_fallback",
                fallback_used=True,
                watermark_mutation=mutation,
            )
        return EvaluationResult(
            should_trigger=False,
            skip_reason=SkipReason.CONDITION_NOT_MET,
            watermark_mutation=mutation,
        )
