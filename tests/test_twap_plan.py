from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.domain.trading_session import TradingDayPhase, TradingSessionService
from app.domain.twap import (
    TwapEndTimeAlreadyPassedError,
    TwapEndTimeOutsideSessionError,
    TwapInsufficientSlicesError,
    TwapStartTimeAfterEndTimeError,
    TwapStartTimeOutsideSessionError,
    TwapTooManySlicesError,
    build_twap_plan,
)

TAIPEI = ZoneInfo("Asia/Taipei")


def dt(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=TAIPEI)


def test_pre_market_starts_at_today_open() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 8, 30))

    plan = build_twap_plan(
        position_side="long",
        quantity_lots=10,
        interval_seconds=300,
        end_time=time(9, 45),
        now=svc.now_taipei(),
        session_service=svc,
    )

    assert plan.trading_phase == TradingDayPhase.PRE_MARKET
    assert plan.trading_date == date(2026, 5, 28)
    assert plan.requested_start_time == time(9, 0)
    assert plan.start_at == dt(2026, 5, 28, 9, 0)
    assert plan.available_slice_count == 10
    assert [s.planned_quantity_lots for s in plan.slices] == [1] * 10


def test_regular_session_starts_at_current_second_and_ceil_allocates_earlier_slices() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 10, 0))

    plan = build_twap_plan(
        position_side="short",
        quantity_lots=100,
        interval_seconds=300,
        end_time=time(10, 45),
        now=svc.now_taipei(),
        session_service=svc,
    )

    assert plan.trading_phase == TradingDayPhase.REGULAR_SESSION
    assert plan.start_at == dt(2026, 5, 28, 10, 0)
    assert plan.materialized_slice_count == 10
    assert [s.planned_quantity_lots for s in plan.slices] == [10] * 10


def test_uses_requested_start_time() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 8, 30))

    plan = build_twap_plan(
        position_side="long",
        quantity_lots=4,
        interval_seconds=300,
        start_time=time(9, 10),
        end_time=time(9, 25),
        now=svc.now_taipei(),
        session_service=svc,
    )

    assert plan.requested_start_time == time(9, 10)
    assert plan.start_at == dt(2026, 5, 28, 9, 10)
    assert [s.scheduled_at for s in plan.slices] == [
        dt(2026, 5, 28, 9, 10),
        dt(2026, 5, 28, 9, 15),
        dt(2026, 5, 28, 9, 20),
        dt(2026, 5, 28, 9, 25),
    ]


def test_post_market_moves_to_next_trading_day_open() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 29, 14, 0))

    plan = build_twap_plan(
        position_side="long",
        quantity_lots=2,
        interval_seconds=60,
        end_time=time(9, 1),
        now=svc.now_taipei(),
        session_service=svc,
    )

    assert plan.trading_phase == TradingDayPhase.POST_MARKET
    assert plan.trading_date == date(2026, 6, 1)
    assert [s.scheduled_at for s in plan.slices] == [dt(2026, 6, 1, 9, 0), dt(2026, 6, 1, 9, 1)]


def test_regular_session_rejects_end_time_already_passed() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 10, 0))

    with pytest.raises(TwapEndTimeAlreadyPassedError):
        build_twap_plan(
            position_side="long",
            quantity_lots=2,
            interval_seconds=60,
            end_time=time(9, 59),
            now=svc.now_taipei(),
            session_service=svc,
        )


def test_rejects_single_materialized_slice() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 10, 0))

    with pytest.raises(TwapInsufficientSlicesError):
        build_twap_plan(
            position_side="long",
            quantity_lots=2,
            interval_seconds=1,
            end_time=time(10, 0),
            now=svc.now_taipei(),
            session_service=svc,
        )


def test_rejects_more_than_200_materialized_slices() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 9, 0))

    with pytest.raises(TwapTooManySlicesError):
        build_twap_plan(
            position_side="long",
            quantity_lots=201,
            interval_seconds=1,
            end_time=time(9, 3, 20),
            now=svc.now_taipei(),
            session_service=svc,
        )


def test_accepts_latest_end_time_1325() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 13, 23))

    plan = build_twap_plan(
        position_side="long",
        quantity_lots=2,
        interval_seconds=60,
        end_time=time(13, 25),
        now=svc.now_taipei(),
        session_service=svc,
    )

    assert plan.end_at == dt(2026, 5, 28, 13, 25)


def test_rejects_end_time_after_1325() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 9, 0))

    with pytest.raises(TwapEndTimeOutsideSessionError):
        build_twap_plan(
            position_side="long",
            quantity_lots=2,
            interval_seconds=60,
            end_time=time(13, 26),
            now=svc.now_taipei(),
            session_service=svc,
        )


def test_rejects_start_time_after_end_time() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 8, 30))

    with pytest.raises(TwapStartTimeAfterEndTimeError):
        build_twap_plan(
            position_side="long",
            quantity_lots=2,
            interval_seconds=60,
            start_time=time(9, 10),
            end_time=time(9, 5),
            now=svc.now_taipei(),
            session_service=svc,
        )


def test_rejects_start_time_before_open() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 8, 30))

    with pytest.raises(TwapStartTimeOutsideSessionError):
        build_twap_plan(
            position_side="long",
            quantity_lots=2,
            interval_seconds=60,
            start_time=time(8, 59),
            end_time=time(9, 5),
            now=svc.now_taipei(),
            session_service=svc,
        )


def test_rejects_start_time_after_1320() -> None:
    svc = TradingSessionService(clock=lambda: dt(2026, 5, 28, 8, 30))

    with pytest.raises(TwapStartTimeOutsideSessionError):
        build_twap_plan(
            position_side="long",
            quantity_lots=2,
            interval_seconds=60,
            start_time=time(13, 21),
            end_time=time(13, 25),
            now=svc.now_taipei(),
            session_service=svc,
        )
