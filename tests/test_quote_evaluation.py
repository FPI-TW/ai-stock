"""Unit tests for QuoteEvaluator — BE-V0.5-09."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.domain.quote_evaluation import EvaluationResult, QuoteEvaluator, SkipReason
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteSnapshot

TAIPEI = ZoneInfo("Asia/Taipei")

# 2026-05-11 is Monday — anchor everything inside the regular session.
SESSION_NOW = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
SESSION_QUOTE_TIME = datetime(2026, 5, 11, 9, 59, 55, tzinfo=TAIPEI)  # 5s before SESSION_NOW


def make_intent(
    strategy: str = "buy_price_alert",
    target: Decimal | None = Decimal("100"),
    trail_mode: str | None = None,
    trail_value: Decimal | None = None,
    baseline: Decimal | None = None,
    dynamic_trigger_price: Decimal | None = None,
) -> TradeIntentData:
    return TradeIntentData(
        id=uuid4(),
        owner_user_id=uuid4(),
        symbol="2330",
        strategy=strategy,
        execution_mode="notify_only",
        quantity_lots=1,
        target_price_original=target,
        target_price_effective=target,
        trigger_reference_price_type="ask",
        trading_date=SESSION_NOW.date(),
        time_in_force="day",
        status="active",
        created_at=SESSION_NOW,
        updated_at=SESSION_NOW,
        trail_mode=trail_mode,
        trail_value=trail_value,
        baseline=baseline,
        dynamic_trigger_price=dynamic_trigger_price,
    )


def _snapshot(
    *,
    quote_time: datetime = SESSION_QUOTE_TIME,
    bid_price: Decimal | None = None,
    ask_price: Decimal | None = None,
    last_price: Decimal | None = None,
) -> QuoteSnapshot:
    # received_at defaults to quote_time — the evaluator never inspects it,
    # so tests only differentiate when a specific value matters.
    return QuoteSnapshot(
        symbol="2330",
        quote_time=quote_time,
        received_at=quote_time,
        bid_price=bid_price,
        ask_price=ask_price,
        last_price=last_price,
    )


@pytest.fixture
def evaluator() -> QuoteEvaluator:
    return QuoteEvaluator(TradingSessionService())


class TestBuyPriceAlert:
    def test_ask_at_target_triggers(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(ask_price=Decimal("100")), make_intent(), SESSION_NOW)
        assert result == EvaluationResult(
            should_trigger=True,
            trigger_price=Decimal("100"),
            trigger_reference_price_type="ask",
            fallback_used=False,
        )

    def test_ask_below_target_triggers(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(ask_price=Decimal("99")), make_intent(), SESSION_NOW)
        assert result.should_trigger
        assert result.trigger_price == Decimal("99")
        assert result.trigger_reference_price_type == "ask"
        assert result.fallback_used is False

    def test_ask_above_target_does_not_trigger(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(ask_price=Decimal("101")), make_intent(), SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.CONDITION_NOT_MET

    def test_missing_ask_falls_back_to_last(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(last_price=Decimal("99")), make_intent(), SESSION_NOW)
        assert result.should_trigger
        assert result.trigger_price == Decimal("99")
        assert result.trigger_reference_price_type == "last_fallback"
        assert result.fallback_used is True

    def test_ask_present_does_not_fall_back_even_when_last_would_meet(self, evaluator: QuoteEvaluator) -> None:
        # ask=101 fails; last=99 would meet but spec §4 forbids falling back when ask is present.
        result = evaluator.evaluate(
            _snapshot(ask_price=Decimal("101"), last_price=Decimal("99")),
            make_intent(),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.CONDITION_NOT_MET

    def test_missing_ask_last_above_target_does_not_trigger(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(last_price=Decimal("101")), make_intent(), SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.CONDITION_NOT_MET


class TestSellPriceAlert:
    def test_bid_at_target_triggers(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("100")),
            make_intent("sell_price_alert"),
            SESSION_NOW,
        )
        assert result.should_trigger
        assert result.trigger_price == Decimal("100")
        assert result.trigger_reference_price_type == "bid"
        assert result.fallback_used is False

    def test_bid_above_target_triggers(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("101")),
            make_intent("sell_price_alert"),
            SESSION_NOW,
        )
        assert result.should_trigger
        assert result.trigger_price == Decimal("101")
        assert result.trigger_reference_price_type == "bid"

    def test_bid_below_target_does_not_trigger(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("99")),
            make_intent("sell_price_alert"),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.CONDITION_NOT_MET


class TestLimitOrders:
    def test_limit_buy_uses_buy_alert_rules(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(ask_price=Decimal("99")),
            make_intent("limit_buy_order"),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_reference_price_type == "ask"

    def test_limit_buy_falls_back_to_last(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(last_price=Decimal("99")),
            make_intent("limit_buy_order"),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_reference_price_type == "last_fallback"
        assert result.fallback_used is True

    def test_limit_sell_uses_sell_alert_rules(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("101")),
            make_intent("limit_sell_order"),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_reference_price_type == "bid"

    def test_limit_sell_falls_back_to_last(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(last_price=Decimal("101")),
            make_intent("limit_sell_order"),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_reference_price_type == "last_fallback"
        assert result.fallback_used is True


class TestMarketOrder:
    def test_market_order_triggers_with_ask(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(ask_price=Decimal("100")),
            make_intent("market_buy_order", target=None),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_price == Decimal("100")
        assert result.trigger_reference_price_type == "ask"

    def test_market_order_falls_back_to_last(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(last_price=Decimal("100")),
            make_intent("market_buy_order", target=None),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_price == Decimal("100")
        assert result.trigger_reference_price_type == "last_fallback"
        assert result.fallback_used is True

    def test_market_sell_order_triggers_with_bid(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("99")),
            make_intent("market_sell_order", target=None),
            SESSION_NOW,
        )

        assert result.should_trigger
        assert result.trigger_price == Decimal("99")
        assert result.trigger_reference_price_type == "bid"


class TestTrailingStopAlert:
    def test_initializes_baseline_from_last_and_dynamic_price(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("96"), ask_price=Decimal("101"), last_price=Decimal("100")),
            make_intent("trailing_stop_alert", trail_mode="percentage", trail_value=Decimal("5")),
            SESSION_NOW,
        )

        assert not result.should_trigger
        assert result.baseline == Decimal("100")
        assert result.dynamic_trigger_price == Decimal("95.0")
        assert result.baseline_updated_at == SESSION_NOW

    def test_updates_baseline_only_on_higher_reference_price(self, evaluator: QuoteEvaluator) -> None:
        intent = make_intent(
            "trailing_stop_alert",
            trail_mode="fixed_amount",
            trail_value=Decimal("5"),
            baseline=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
        )
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("99"), ask_price=Decimal("100"), last_price=Decimal("110")),
            intent,
            SESSION_NOW,
        )

        assert result.baseline == Decimal("110")
        assert result.dynamic_trigger_price == Decimal("105.0")
        assert result.baseline_updated_at == SESSION_NOW

    def test_lower_reference_keeps_existing_baseline(self, evaluator: QuoteEvaluator) -> None:
        intent = make_intent(
            "trailing_stop_alert",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            baseline=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
        )
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("96"), last_price=Decimal("99")),
            intent,
            SESSION_NOW,
        )

        assert not result.should_trigger
        assert result.baseline == Decimal("100")
        assert result.dynamic_trigger_price == Decimal("95")
        assert result.baseline_updated_at is None

    def test_triggers_when_bid_reaches_dynamic_price(self, evaluator: QuoteEvaluator) -> None:
        intent = make_intent(
            "trailing_stop_alert",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            baseline=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
        )
        result = evaluator.evaluate(_snapshot(bid_price=Decimal("94.9")), intent, SESSION_NOW)

        assert result.should_trigger
        assert result.trigger_price == Decimal("94.9")
        assert result.trigger_reference_price_type == "bid"

    def test_trailing_falls_back_to_last_when_bid_missing(self, evaluator: QuoteEvaluator) -> None:
        intent = make_intent(
            "trailing_stop_alert",
            trail_mode="percentage",
            trail_value=Decimal("5"),
            baseline=Decimal("100"),
            dynamic_trigger_price=Decimal("95"),
        )
        result = evaluator.evaluate(_snapshot(last_price=Decimal("94.9")), intent, SESSION_NOW)

        assert result.should_trigger
        assert result.trigger_reference_price_type == "last_fallback"
        assert result.fallback_used is True

    def test_missing_bid_falls_back_to_last(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(last_price=Decimal("101")),
            make_intent("sell_price_alert"),
            SESSION_NOW,
        )
        assert result.should_trigger
        assert result.trigger_price == Decimal("101")
        assert result.trigger_reference_price_type == "last_fallback"
        assert result.fallback_used is True

    def test_bid_present_does_not_fall_back_even_when_last_would_meet(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("99"), last_price=Decimal("101")),
            make_intent("sell_price_alert"),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.CONDITION_NOT_MET


class TestSessionGuard:
    def test_now_outside_session_skips(self, evaluator: QuoteEvaluator) -> None:
        weekend = datetime(2026, 5, 16, 10, 0, tzinfo=TAIPEI)  # Saturday
        result = evaluator.evaluate(
            _snapshot(quote_time=weekend, ask_price=Decimal("99")),
            make_intent(),
            weekend,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.OUTSIDE_SESSION

    def test_quote_time_outside_session_skips(self, evaluator: QuoteEvaluator) -> None:
        # now is in session but quote_time is before market open.
        pre_open = datetime(2026, 5, 11, 8, 59, tzinfo=TAIPEI)
        result = evaluator.evaluate(
            _snapshot(quote_time=pre_open, ask_price=Decimal("99")),
            make_intent(),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.OUTSIDE_SESSION


class TestQuoteValidation:
    def test_stale_quote_skips(self, evaluator: QuoteEvaluator) -> None:
        stale = SESSION_NOW - timedelta(seconds=11)
        result = evaluator.evaluate(_snapshot(quote_time=stale, ask_price=Decimal("99")), make_intent(), SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_STALE

    def test_quote_exactly_at_freshness_boundary_passes(self, evaluator: QuoteEvaluator) -> None:
        # 10s old — threshold is inclusive.
        boundary = SESSION_NOW - timedelta(seconds=10)
        result = evaluator.evaluate(_snapshot(quote_time=boundary, ask_price=Decimal("99")), make_intent(), SESSION_NOW)
        assert result.should_trigger

    def test_bid_greater_than_ask_skips(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("101"), ask_price=Decimal("100")),
            make_intent(),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_BID_GT_ASK

    def test_zero_price_skips(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(ask_price=Decimal("0")), make_intent(), SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_NONPOSITIVE_PRICE

    def test_all_prices_missing_skips(self, evaluator: QuoteEvaluator) -> None:
        result = evaluator.evaluate(_snapshot(), make_intent(), SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_MISSING_ALL_PRICES

    def test_nonpositive_price_takes_priority_over_bid_gt_ask(self, evaluator: QuoteEvaluator) -> None:
        # bid=0 is both non-positive AND <= ask=100 (so no bid_gt_ask). The
        # interesting overlap is bid=0 with ask>0: non-positive must win so
        # downstream ops alerts surface the more specific reason. Pin the
        # ordering so a future refactor of `_validate_quote` doesn't silently
        # flip the signal.
        result = evaluator.evaluate(
            _snapshot(bid_price=Decimal("0"), ask_price=Decimal("100")),
            make_intent(),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_NONPOSITIVE_PRICE


class TestUnsupportedStrategy:
    def test_unknown_strategy_skips(self, evaluator: QuoteEvaluator) -> None:
        intent = make_intent(strategy="take_profit_alert")
        result = evaluator.evaluate(_snapshot(ask_price=Decimal("99")), intent, SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.UNSUPPORTED_STRATEGY
