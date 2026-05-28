from dataclasses import dataclass

from app.domain.trading_session import TradingDayPhase, TradingSessionService
from app.repositories.intent_repository import IntentRepository


@dataclass(frozen=True)
class IntentLifecycleOutput:
    activated_count: int
    expired_count: int


class IntentLifecycleCommand:
    def __init__(
        self,
        intent_repo: IntentRepository,
        session_service: TradingSessionService,
    ) -> None:
        self._intent_repo = intent_repo
        self._session_service = session_service

    def run(self) -> IntentLifecycleOutput:
        now = self._session_service.now_taipei()
        cutoff = self._session_service.get_expirable_day_intent_cutoff(now)
        expired_count = _coerce_count(self._intent_repo.system_expire_day_intents_through(cutoff, now))

        activated_count = 0
        if self._session_service.get_trading_day_phase(now) == TradingDayPhase.REGULAR_SESSION:
            activated_count = _coerce_count(self._intent_repo.system_activate_scheduled_day_intents(now.date(), now))

        if expired_count or activated_count:
            self._intent_repo.commit()
        return IntentLifecycleOutput(
            activated_count=activated_count,
            expired_count=expired_count,
        )


def _coerce_count(value: object) -> int:
    return value if isinstance(value, int) else 0
