"""Taiwan regular trading session rules — V0.5 weekday-only implementation.

V1 will replace the Mon-Fri weekday rule with a proper market_calendar table.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# Regular session: [09:00, 13:30) Asia/Taipei
_SESSION_START = time(9, 0)
_SESSION_END = time(13, 30)


def _require_aware(dt: datetime) -> None:
    if dt.tzinfo is None:
        raise TypeError(f"naive datetime is not allowed; attach a timezone before calling: {dt!r}")


class OutsideSessionError(Exception):
    """Raised when evaluation is attempted outside the regular trading session."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class TradingDayPhase(StrEnum):
    PRE_MARKET = "pre_market"
    REGULAR_SESSION = "regular_session"
    POST_MARKET = "post_market"


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
        _require_aware(dt)
        taipei = dt.astimezone(TAIPEI_TZ)
        if not self.is_trading_day(taipei.date()):
            return False
        t = taipei.time()
        return _SESSION_START <= t < _SESSION_END

    def get_trading_day_phase(self, dt: datetime) -> TradingDayPhase:
        """Classify a request time into today's pre-market, regular, or post-market phase."""

        _require_aware(dt)
        taipei = dt.astimezone(TAIPEI_TZ)
        if not self.is_trading_day(taipei.date()):
            return TradingDayPhase.POST_MARKET
        t = taipei.time()
        if t < _SESSION_START:
            return TradingDayPhase.PRE_MARKET
        if t < _SESSION_END:
            return TradingDayPhase.REGULAR_SESSION
        return TradingDayPhase.POST_MARKET

    def next_trading_day(self, d: date) -> date:
        return self._next_weekday(d)

    def get_day_intent_trading_date(self, now: datetime) -> date:
        """Return the trading_date a new day-intent should carry.

        Before session end (13:30) on a weekday → today (Taipei), even if the
        time is before session open (09:00).  This is intentional: the intent
        is "which trading day are you targeting", not "are you inside the
        session right now".  Use is_within_regular_session() when you need the
        stricter >= 09:00 gate.
        At or after session end, or on a weekend → next weekday.
        """
        _require_aware(now)
        taipei = now.astimezone(TAIPEI_TZ)
        today = taipei.date()
        t = taipei.time()
        if self.is_trading_day(today) and t < _SESSION_END:
            return today
        return self._next_weekday(today)

    def get_initial_day_intent_status(self, now: datetime) -> Literal["active", "scheduled"]:
        _require_aware(now)
        return "active" if self.is_within_regular_session(now) else "scheduled"

    def get_expirable_day_intent_cutoff(self, now: datetime) -> date:
        """Return the latest trading_date whose day intents can no longer trigger."""

        _require_aware(now)
        taipei = now.astimezone(TAIPEI_TZ)
        today = taipei.date()
        if self.get_trading_day_phase(taipei) == TradingDayPhase.POST_MARKET:
            return today
        return today - timedelta(days=1)

    def verify_trading_hours(self, now: datetime, quote_time: datetime) -> None:
        """Raise OutsideSessionError if now or quote_time is outside the regular session."""
        _require_aware(now)
        _require_aware(quote_time)
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
