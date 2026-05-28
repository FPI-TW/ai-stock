from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from app.domain.trading_session import TAIPEI_TZ, TradingDayPhase, TradingSessionService

TWAP_STRATEGY = "twap_order"
TWAP_TRIGGER_REFERENCE_PRICE_TYPE = "last_fallback"
TWAP_EXECUTION_MODE = "notify_only"
TWAP_TIME_IN_FORCE = "day"

TWAP_MIN_QUANTITY_LOTS = 2
TWAP_MAX_QUANTITY_LOTS = 1000
TWAP_MIN_INTERVAL_SECONDS = 1
TWAP_MAX_INTERVAL_SECONDS = 3600
TWAP_MIN_MATERIALIZED_SLICES = 2
TWAP_MAX_MATERIALIZED_SLICES = 200
TWAP_PRICE_FOLLOWUP_MAX_ATTEMPTS = 3
TWAP_PRICE_FOLLOWUP_DELAY_SECONDS = 10

SESSION_START_TIME = time(9, 0)
TWAP_LATEST_END_TIME = time(13, 25)


class TwapError(Exception):
    error_code = "TWAP_ERROR"

    def details(self) -> dict[str, object]:
        return {}


class TwapInvalidIntervalError(TwapError):
    error_code = "TWAP_INVALID_INTERVAL"

    def __init__(self, interval_seconds: int) -> None:
        self.interval_seconds = interval_seconds
        super().__init__(f"Invalid TWAP interval: {interval_seconds}")

    def details(self) -> dict[str, object]:
        return {
            "intervalSeconds": self.interval_seconds,
            "minIntervalSeconds": TWAP_MIN_INTERVAL_SECONDS,
            "maxIntervalSeconds": TWAP_MAX_INTERVAL_SECONDS,
        }


class TwapInvalidQuantityError(TwapError):
    error_code = "TWAP_INVALID_QUANTITY"

    def __init__(self, quantity_lots: int) -> None:
        self.quantity_lots = quantity_lots
        super().__init__(f"Invalid TWAP quantity: {quantity_lots}")

    def details(self) -> dict[str, object]:
        return {
            "quantityLots": self.quantity_lots,
            "minQuantityLots": TWAP_MIN_QUANTITY_LOTS,
            "maxQuantityLots": TWAP_MAX_QUANTITY_LOTS,
        }


class TwapEndTimeOutsideSessionError(TwapError):
    error_code = "TWAP_END_TIME_OUTSIDE_SESSION"

    def __init__(self, end_time: time) -> None:
        self.end_time = end_time
        super().__init__(f"TWAP end time outside regular session: {end_time.isoformat()}")

    def details(self) -> dict[str, object]:
        return {
            "endTime": self.end_time.isoformat(),
            "sessionStart": SESSION_START_TIME.isoformat(),
            "latestEndTime": TWAP_LATEST_END_TIME.isoformat(),
        }


class TwapEndTimeAlreadyPassedError(TwapError):
    error_code = "TWAP_END_TIME_ALREADY_PASSED"

    def __init__(self, end_at: datetime, now: datetime) -> None:
        self.end_at = end_at
        self.now = now
        super().__init__(f"TWAP end time already passed: {end_at.isoformat()}")

    def details(self) -> dict[str, object]:
        return {"endAt": self.end_at.isoformat(), "now": self.now.isoformat()}


class TwapInsufficientSlicesError(TwapError):
    error_code = "TWAP_INSUFFICIENT_SLICES"

    def __init__(self, available_slice_count: int, materialized_slice_count: int) -> None:
        self.available_slice_count = available_slice_count
        self.materialized_slice_count = materialized_slice_count
        super().__init__("TWAP requires at least two slices")

    def details(self) -> dict[str, object]:
        return {
            "availableSliceCount": self.available_slice_count,
            "materializedSliceCount": self.materialized_slice_count,
            "minMaterializedSliceCount": TWAP_MIN_MATERIALIZED_SLICES,
        }


class TwapTooManySlicesError(TwapError):
    error_code = "TWAP_TOO_MANY_SLICES"

    def __init__(self, materialized_slice_count: int) -> None:
        self.materialized_slice_count = materialized_slice_count
        super().__init__(f"TWAP slice count exceeds limit: {materialized_slice_count}")

    def details(self) -> dict[str, object]:
        return {
            "materializedSliceCount": self.materialized_slice_count,
            "maxMaterializedSliceCount": TWAP_MAX_MATERIALIZED_SLICES,
        }


class TwapDuplicateActivePlanError(TwapError):
    error_code = "TWAP_DUPLICATE_ACTIVE_PLAN"

    def __init__(self, owner_user_id: UUID, symbol: str, position_side: str) -> None:
        self.owner_user_id = owner_user_id
        self.symbol = symbol
        self.position_side = position_side
        super().__init__(f"Duplicate TWAP plan for {symbol}/{position_side}")

    def details(self) -> dict[str, object]:
        return {"symbol": self.symbol, "positionSide": self.position_side}


@dataclass(frozen=True)
class TwapSlicePlan:
    sequence_no: int
    scheduled_at: datetime
    planned_quantity_lots: int


@dataclass(frozen=True)
class TwapPlan:
    position_side: str
    trading_phase: TradingDayPhase
    trading_date: date
    start_at: datetime
    end_at: datetime
    interval_seconds: int
    target_quantity_lots: int
    available_slice_count: int
    materialized_slice_count: int
    slices: tuple[TwapSlicePlan, ...]


def build_twap_plan(
    *,
    position_side: str,
    quantity_lots: int,
    interval_seconds: int,
    end_time: time,
    now: datetime,
    session_service: TradingSessionService,
) -> TwapPlan:
    if interval_seconds < TWAP_MIN_INTERVAL_SECONDS or interval_seconds > TWAP_MAX_INTERVAL_SECONDS:
        raise TwapInvalidIntervalError(interval_seconds)
    if quantity_lots < TWAP_MIN_QUANTITY_LOTS or quantity_lots > TWAP_MAX_QUANTITY_LOTS:
        raise TwapInvalidQuantityError(quantity_lots)
    if end_time < SESSION_START_TIME or end_time > TWAP_LATEST_END_TIME:
        raise TwapEndTimeOutsideSessionError(end_time)

    now_taipei = now.astimezone(TAIPEI_TZ)
    phase = session_service.get_trading_day_phase(now_taipei)
    today = now_taipei.date()

    if phase == TradingDayPhase.PRE_MARKET:
        trading_date = today
        start_at = datetime.combine(trading_date, SESSION_START_TIME, tzinfo=TAIPEI_TZ)
    elif phase == TradingDayPhase.REGULAR_SESSION:
        trading_date = today
        start_at = _ceil_to_second(now_taipei)
    else:
        trading_date = session_service.next_trading_day(today)
        start_at = datetime.combine(trading_date, SESSION_START_TIME, tzinfo=TAIPEI_TZ)

    end_at = datetime.combine(trading_date, end_time, tzinfo=TAIPEI_TZ)
    if phase == TradingDayPhase.REGULAR_SESSION and end_at < start_at:
        raise TwapEndTimeAlreadyPassedError(end_at, now_taipei)
    if end_at < start_at:
        raise TwapInsufficientSlicesError(0, 0)

    available_slice_count = int((end_at - start_at).total_seconds() // interval_seconds) + 1
    materialized_slice_count = min(available_slice_count, quantity_lots)
    if materialized_slice_count < TWAP_MIN_MATERIALIZED_SLICES:
        raise TwapInsufficientSlicesError(available_slice_count, materialized_slice_count)
    if materialized_slice_count > TWAP_MAX_MATERIALIZED_SLICES:
        raise TwapTooManySlicesError(materialized_slice_count)

    slices = _allocate_slices(
        start_at=start_at,
        interval_seconds=interval_seconds,
        target_quantity_lots=quantity_lots,
        materialized_slice_count=materialized_slice_count,
    )
    return TwapPlan(
        position_side=position_side,
        trading_phase=phase,
        trading_date=trading_date,
        start_at=start_at,
        end_at=end_at,
        interval_seconds=interval_seconds,
        target_quantity_lots=quantity_lots,
        available_slice_count=available_slice_count,
        materialized_slice_count=materialized_slice_count,
        slices=tuple(slices),
    )


def _ceil_to_second(dt: datetime) -> datetime:
    if dt.microsecond == 0:
        return dt
    return dt.replace(microsecond=0) + timedelta(seconds=1)


def _allocate_slices(
    *,
    start_at: datetime,
    interval_seconds: int,
    target_quantity_lots: int,
    materialized_slice_count: int,
) -> list[TwapSlicePlan]:
    remaining_quantity = target_quantity_lots
    slices: list[TwapSlicePlan] = []
    for index in range(materialized_slice_count):
        remaining_slices = materialized_slice_count - index
        planned_quantity = -(-remaining_quantity // remaining_slices)
        slices.append(
            TwapSlicePlan(
                sequence_no=index + 1,
                scheduled_at=start_at + timedelta(seconds=interval_seconds * index),
                planned_quantity_lots=planned_quantity,
            )
        )
        remaining_quantity -= planned_quantity
    return slices
