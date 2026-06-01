import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.commands.twap import TwapSliceWorkerCommand
from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteProvider

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class TwapSliceScheduler:
    """Periodically drives TWAP slice notifications in local V0.5 runtime."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        quote_provider: QuoteProvider,
        session_service: TradingSessionService,
        interval_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._quote_provider = quote_provider
        self._session_service = session_service
        self._interval_seconds = interval_seconds

    def run_once(self) -> None:
        self._run_worker_phase("due_slices")
        self._run_worker_phase("price_followups")

    async def run_forever(self) -> None:
        while True:
            await asyncio.to_thread(self.run_once)
            await asyncio.sleep(self._interval_seconds)

    def _run_worker_phase(self, phase: str) -> None:
        try:
            with self._session_factory() as db:
                worker = TwapSliceWorkerCommand(db, self._quote_provider, self._session_service)
                if phase == "due_slices":
                    worker.process_due_slices()
                elif phase == "price_followups":
                    worker.process_price_followups()
                else:
                    raise RuntimeError(f"unknown TWAP worker phase: {phase}")
        except Exception:
            logger.exception("twap scheduler phase failed: %s", phase)
