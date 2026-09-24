"""Unit tests for QuoteEvaluator — BE-V0.5-09."""

from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
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


_SAME_AS_QUOTE_TIME = object()


def _snapshot(
    *,
    quote_time: datetime = SESSION_QUOTE_TIME,
    bid_price: Decimal | None = None,
    ask_price: Decimal | None = None,
    last_price: Decimal | None = None,
    last_trade_time: datetime | None | object = _SAME_AS_QUOTE_TIME,
) -> QuoteSnapshot:
    # received_at defaults to quote_time — the evaluator never inspects it,
    # so tests only differentiate when a specific value matters.
    trade_time = quote_time if last_trade_time is _SAME_AS_QUOTE_TIME else last_trade_time
    assert trade_time is None or isinstance(trade_time, datetime)
    return QuoteSnapshot(
        symbol="2330",
        quote_time=quote_time,
        last_trade_time=trade_time,
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
        stale = SESSION_NOW - timedelta(minutes=10, seconds=1)
        result = evaluator.evaluate(_snapshot(quote_time=stale, ask_price=Decimal("99")), make_intent(), SESSION_NOW)
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_STALE

    def test_quote_exactly_at_freshness_boundary_passes(self, evaluator: QuoteEvaluator) -> None:
        # 10min old — threshold is inclusive.
        boundary = SESSION_NOW - timedelta(minutes=10)
        result = evaluator.evaluate(_snapshot(quote_time=boundary, ask_price=Decimal("99")), make_intent(), SESSION_NOW)
        assert result.should_trigger

    def test_thin_symbol_book_older_than_ten_seconds_still_triggers(self, evaluator: QuoteEvaluator) -> None:
        """冷門股實測間隔（中位 192s、最長 360s）落在掛單窗內，不得再被判 stale。"""
        book = SESSION_NOW - timedelta(seconds=360)
        result = evaluator.evaluate(
            _snapshot(quote_time=book, ask_price=Decimal("99"), last_trade_time=None),
            make_intent(),
            SESSION_NOW,
        )
        assert result.should_trigger
        assert result.trigger_reference_price_type == "ask"

    def test_trade_window_stays_at_ten_seconds_while_book_window_is_wide(self, evaluator: QuoteEvaluator) -> None:
        """掛單窗放寬不得順帶放寬成交價：11 秒前的成交價仍不可當現價用。"""
        book = SESSION_NOW - timedelta(minutes=5)
        result = evaluator.evaluate(
            _snapshot(
                quote_time=book,
                last_price=Decimal("99"),
                last_trade_time=SESSION_NOW - timedelta(seconds=11),
            ),
            make_intent(),
            SESSION_NOW,
        )
        assert not result.should_trigger
        assert result.skip_reason is SkipReason.QUOTE_STALE

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


class TestTriggerInvariant:
    """不變量：`should_trigger=True` 必然帶齊 `trigger_price` 與
    `trigger_reference_price_type`。dispatcher 靠這條把「到價」翻成觸發寫入；一旦
    某分支說到價卻不給價，dispatcher 會拋 RuntimeError 中斷整條 symbol 的處理（毒單）。
    這裡在源頭釘死，讓未來新增策略 / 重構誤破不變量時，測試先炸而非上線後炸。"""

    def _intent_for(self, strategy: str) -> TradeIntentData:
        if strategy == "trailing_stop_alert":
            # 帶 baseline/dynamic 才可能走到觸發分支；baseline 高於掃描價上限，
            # 不會在掃描中被抬高，dynamic 維持可被跌破。
            return make_intent(
                strategy=strategy,
                target=None,
                trail_mode="percentage",
                trail_value=Decimal("5"),
                baseline=Decimal("110"),
                dynamic_trigger_price=Decimal("100"),
            )
        return make_intent(strategy=strategy, target=Decimal("100"))

    def test_should_trigger_always_carries_price(self, evaluator: QuoteEvaluator) -> None:
        strategies = (
            "buy_price_alert",
            "limit_buy_order",
            "sell_price_alert",
            "limit_sell_order",
            "market_order",
            "market_buy_order",
            "market_sell_order",
            "trailing_stop_alert",
        )
        prices: tuple[Decimal | None, ...] = (None, Decimal("95"), Decimal("100"), Decimal("105"))

        triggered = 0
        for strategy in strategies:
            intent = self._intent_for(strategy)
            for bid, ask, last in product(prices, prices, prices):
                result = evaluator.evaluate(
                    _snapshot(bid_price=bid, ask_price=ask, last_price=last), intent, SESSION_NOW
                )
                if result.should_trigger:
                    triggered += 1
                    assert result.trigger_price is not None, f"{strategy} triggered without trigger_price: {result}"
                    assert result.trigger_reference_price_type is not None, (
                        f"{strategy} triggered without reference_price_type: {result}"
                    )

        # 掃描確實踩到觸發分支（否則不變量恆真、測試空過）。
        assert triggered > 0


def test_fresh_book_triggers_even_when_last_trade_is_old(evaluator: QuoteEvaluator) -> None:
    # Thin stock: ask reached the target an hour after the last match. Buy-side
    # intents read the ask, so the trade's age must not block them.
    quote = _snapshot(
        ask_price=Decimal("99"),
        last_price=Decimal("120"),
        last_trade_time=SESSION_QUOTE_TIME - timedelta(hours=1),
    )

    result = evaluator.evaluate(quote, make_intent(target=Decimal("100")), SESSION_NOW)

    assert result.should_trigger is True
    assert result.trigger_reference_price_type == "ask"


def test_stale_last_trade_cannot_stand_in_for_missing_book(evaluator: QuoteEvaluator) -> None:
    # No book at all and the only price is an old trade → stale, not a fallback trigger.
    quote = _snapshot(last_price=Decimal("99"), last_trade_time=SESSION_QUOTE_TIME - timedelta(seconds=11))

    result = evaluator.evaluate(quote, make_intent(target=Decimal("100")), SESSION_NOW)

    assert result.should_trigger is False
    assert result.skip_reason == SkipReason.QUOTE_STALE


def test_fresh_last_trade_still_falls_back_when_book_is_missing(evaluator: QuoteEvaluator) -> None:
    quote = _snapshot(last_price=Decimal("99"))

    result = evaluator.evaluate(quote, make_intent(target=Decimal("100")), SESSION_NOW)

    assert result.should_trigger is True
    assert result.fallback_used is True


def test_stale_last_trade_is_ignored_when_other_side_of_book_exists(evaluator: QuoteEvaluator) -> None:
    # Buy intent, ask missing, bid present, trade stale: no fallback, no trigger,
    # and not reported as stale because the frame itself is current.
    quote = _snapshot(
        bid_price=Decimal("98"),
        last_price=Decimal("99"),
        last_trade_time=SESSION_QUOTE_TIME - timedelta(minutes=5),
    )

    result = evaluator.evaluate(quote, make_intent(target=Decimal("100")), SESSION_NOW)

    assert result.should_trigger is False
    assert result.skip_reason == SkipReason.CONDITION_NOT_MET
