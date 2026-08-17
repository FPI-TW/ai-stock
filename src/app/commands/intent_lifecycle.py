from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.domain.trading_session import TradingDayPhase, TradingSessionService


class DayIntentLifecycleStore(Protocol):
    """生命週期只用得到的三個 repo 方法。

    T1 cutover 期間兩軌並存：新軌 `TradeIntentCoreRepository`（API / robot #2）與舊軌
    `IntentRepository`（legacy dispatcher / TWAP）都要跑同一套啟用/到期規則，故參數型別
    取這個最小介面而非綁死其中一個 repo。舊軌退役後可直接改標新 repo 具體型別。
    """

    def system_expire_day_intents_through(self, cutoff_date: date, now: datetime) -> int: ...

    def system_activate_scheduled_day_intents(self, trading_date: date, now: datetime) -> int: ...

    def commit(self) -> None: ...


@dataclass(frozen=True)
class IntentLifecycleOutput:
    activated_count: int
    expired_count: int


class IntentLifecycleCommand:
    def __init__(
        self,
        intent_repo: DayIntentLifecycleStore,
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
