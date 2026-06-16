from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.orm import Session

from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteProvider
from app.services.twap_scheduler import TwapSliceScheduler


class FakeSession:
    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def test_scheduler_processes_due_slices_and_price_followups(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(
            self,
            _db: Session,
            _quote_provider: QuoteProvider,
            _session_service: TradingSessionService,
            _kill_switch: object = None,
        ) -> None:
            return None

        def process_due_slices(self) -> None:
            calls.append("due_slices")

        def process_price_followups(self) -> None:
            calls.append("price_followups")

    monkeypatch.setattr("app.services.twap_scheduler.TwapSliceWorkerCommand", FakeWorker)
    scheduler = TwapSliceScheduler(
        session_factory=lambda: cast(Session, FakeSession()),
        quote_provider=cast(QuoteProvider, object()),
        session_service=TradingSessionService(),
        interval_seconds=1.0,
    )

    scheduler.run_once()

    assert calls == ["due_slices", "price_followups"]


def test_scheduler_continues_to_followups_when_due_slice_phase_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeWorker:
        def __init__(
            self,
            _db: Session,
            _quote_provider: QuoteProvider,
            _session_service: TradingSessionService,
            _kill_switch: object = None,
        ) -> None:
            return None

        def process_due_slices(self) -> None:
            calls.append("due_slices")
            raise RuntimeError("boom")

        def process_price_followups(self) -> None:
            calls.append("price_followups")

    monkeypatch.setattr("app.services.twap_scheduler.TwapSliceWorkerCommand", FakeWorker)
    scheduler = TwapSliceScheduler(
        session_factory=lambda: cast(Session, FakeSession()),
        quote_provider=cast(QuoteProvider, object()),
        session_service=TradingSessionService(),
        interval_seconds=1.0,
    )

    scheduler.run_once()

    assert calls == ["due_slices", "price_followups"]
