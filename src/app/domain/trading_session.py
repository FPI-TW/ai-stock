"""Taiwan regular trading session rules — V0.5 weekday-only implementation.

V1 will replace the Mon-Fri weekday rule with a proper market_calendar table.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# Regular session: [09:00, 13:30) Asia/Taipei
_SESSION_START = time(9, 0)
_SESSION_END = time(13, 30)


class OutsideSessionError(RuntimeError):
    """Raised when evaluation is attempted outside the regular trading session."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class TradingSessionService:
    """V0.5 Taiwan trading session: Mon-Fri 09:00-13:30 Asia/Taipei.

    Inject `clock` in tests to avoid calling datetime.now() directly.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock

    def now_taipei(self) -> datetime:
        if self._clock is not None:
            return self._clock().astimezone(TAIPEI_TZ)
        return datetime.now(tz=TAIPEI_TZ)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5  # Mon=0 … Fri=4

    def is_within_regular_session(self, dt: datetime) -> bool:
        taipei = dt.astimezone(TAIPEI_TZ)
        if not self.is_trading_day(taipei.date()):
            return False
        t = taipei.time().replace(tzinfo=None)
        return _SESSION_START <= t < _SESSION_END

    def get_day_intent_trading_date(self, now: datetime) -> date:
        """Return the trading_date a new day-intent should carry.

        Before session end on a weekday → today (Taipei).
        At or after session end, or on a weekend → next weekday.
        """
        taipei = now.astimezone(TAIPEI_TZ)
        today = taipei.date()
        t = taipei.time().replace(tzinfo=None)
        if self.is_trading_day(today) and t < _SESSION_END:
            return today
        return self._next_weekday(today)

    def get_initial_day_intent_status(self, now: datetime) -> Literal["active", "scheduled"]:
        return "active" if self.is_within_regular_session(now) else "scheduled"

    def assert_can_evaluate(self, now: datetime, quote_time: datetime) -> None:
        """Raise OutsideSessionError if now or quote_time is outside the regular session."""
        if not self.is_within_regular_session(now):
            raise OutsideSessionError(f"now {now.isoformat()} is outside regular session")
        if not self.is_within_regular_session(quote_time):
            raise OutsideSessionError(f"quote_time {quote_time.isoformat()} is outside regular session")

    @staticmethod
    def _next_weekday(d: date) -> date:
        candidate = d + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate
