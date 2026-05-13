"""Unit tests for TradingSessionService — BE-V0.5-06."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.trading_session import OutsideSessionError, TradingSessionService

TAIPEI = ZoneInfo("Asia/Taipei")


def dt(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TAIPEI)


# Concrete reference dates (verified weekdays):
# 2026-05-11 = Monday, 2026-05-12 = Tuesday
# 2026-05-15 = Friday, 2026-05-16 = Saturday, 2026-05-18 = Monday
MON = (2026, 5, 11)
TUE = (2026, 5, 12)
FRI = (2026, 5, 15)
SAT = (2026, 5, 16)
NEXT_MON = (2026, 5, 18)


class TestIsTradingDay:
    svc = TradingSessionService()

    def test_monday(self) -> None:
        assert self.svc.is_trading_day(date(*MON))

    def test_friday(self) -> None:
        assert self.svc.is_trading_day(date(*FRI))

    def test_saturday(self) -> None:
        assert not self.svc.is_trading_day(date(*SAT))

    def test_sunday(self) -> None:
        assert not self.svc.is_trading_day(date(2026, 5, 17))


class TestIsWithinRegularSession:
    svc = TradingSessionService()

    def test_before_open_not_in_session(self) -> None:
        assert not self.svc.is_within_regular_session(dt(*MON, 8, 59))

    def test_at_open_in_session(self) -> None:
        assert self.svc.is_within_regular_session(dt(*MON, 9, 0))

    def test_during_session(self) -> None:
        assert self.svc.is_within_regular_session(dt(*MON, 11, 0))

    def test_at_close_not_in_session(self) -> None:
        # 13:30 is exclusive — session is [09:00, 13:30)
        assert not self.svc.is_within_regular_session(dt(*MON, 13, 30))

    def test_after_close_not_in_session(self) -> None:
        assert not self.svc.is_within_regular_session(dt(*MON, 15, 0))

    def test_saturday_not_in_session(self) -> None:
        assert not self.svc.is_within_regular_session(dt(*SAT, 10, 0))


class TestGetDayIntentTradingDate:
    svc = TradingSessionService()

    def test_monday_before_session_returns_today(self) -> None:
        # 週一 08:59 → scheduled today
        assert self.svc.get_day_intent_trading_date(dt(*MON, 8, 59)) == date(*MON)

    def test_monday_at_open_returns_today(self) -> None:
        # 週一 09:00 → active today
        assert self.svc.get_day_intent_trading_date(dt(*MON, 9, 0)) == date(*MON)

    def test_monday_at_close_returns_next_weekday(self) -> None:
        # 週一 13:30 後 → scheduled next weekday (Tuesday)
        assert self.svc.get_day_intent_trading_date(dt(*MON, 13, 30)) == date(*TUE)

    def test_saturday_returns_next_monday(self) -> None:
        # 週六 → scheduled next Monday
        assert self.svc.get_day_intent_trading_date(dt(*SAT, 10, 0)) == date(*NEXT_MON)

    def test_friday_after_close_returns_next_monday(self) -> None:
        assert self.svc.get_day_intent_trading_date(dt(*FRI, 14, 0)) == date(*NEXT_MON)


class TestGetInitialDayIntentStatus:
    svc = TradingSessionService()

    def test_monday_before_session_is_scheduled(self) -> None:
        assert self.svc.get_initial_day_intent_status(dt(*MON, 8, 59)) == "scheduled"

    def test_monday_at_open_is_active(self) -> None:
        assert self.svc.get_initial_day_intent_status(dt(*MON, 9, 0)) == "active"

    def test_monday_at_close_is_scheduled(self) -> None:
        assert self.svc.get_initial_day_intent_status(dt(*MON, 13, 30)) == "scheduled"

    def test_saturday_is_scheduled(self) -> None:
        assert self.svc.get_initial_day_intent_status(dt(*SAT, 10, 0)) == "scheduled"


class TestAssertCanEvaluate:
    svc = TradingSessionService()

    def test_both_in_session_does_not_raise(self) -> None:
        # Evaluator test: valid case
        self.svc.assert_can_evaluate(dt(*MON, 10, 0), dt(*MON, 10, 0))

    def test_now_outside_session_raises(self) -> None:
        # Evaluator test: now outside session 不觸發
        with pytest.raises(OutsideSessionError):
            self.svc.assert_can_evaluate(dt(*MON, 8, 59), dt(*MON, 10, 0))

    def test_now_after_close_raises(self) -> None:
        with pytest.raises(OutsideSessionError):
            self.svc.assert_can_evaluate(dt(*MON, 13, 30), dt(*MON, 13, 0))

    def test_quote_time_outside_session_raises(self) -> None:
        # Evaluator test: quote_time outside session 不觸發
        with pytest.raises(OutsideSessionError):
            self.svc.assert_can_evaluate(dt(*MON, 10, 0), dt(*SAT, 10, 0))

    def test_now_weekend_raises(self) -> None:
        with pytest.raises(OutsideSessionError):
            self.svc.assert_can_evaluate(dt(*SAT, 10, 0), dt(*SAT, 10, 0))


class TestClockInjection:
    def test_now_taipei_uses_injected_clock(self) -> None:
        fixed = dt(*MON, 10, 0)
        svc = TradingSessionService(clock=lambda: fixed)
        assert svc.now_taipei() == fixed
