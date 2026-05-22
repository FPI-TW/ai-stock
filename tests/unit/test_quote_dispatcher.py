"""Unit tests for QuoteEvaluationDispatcher.

Covers the work-order requirement: "Unit：Shioaji callback 進來後會呼叫 evaluator
（以 fake callback 注入，不打網路）". We assert the dispatcher's behaviour with
a Mock evaluator and Mock repo/command — broker integration is exercised
separately by `tests/unit/quote/test_shioaji_demo_callback.py` (listener
plumbing) and the integration suite (real DB).
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.domain.quote_evaluation import EvaluationResult
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteSnapshot
from app.services.quote_dispatcher import QuoteEvaluationDispatcher

TAIPEI = ZoneInfo("Asia/Taipei")
QUOTE_TIME = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)


def _intent(symbol: str = "2330", strategy: str = "buy_price_alert") -> TradeIntentData:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
    return TradeIntentData(
        id=uuid4(),
        owner_user_id=uuid4(),
        symbol=symbol,
        strategy=strategy,
        execution_mode="notify_only",
        quantity_lots=1,
        target_price_original=Decimal("100.0000"),
        target_price_effective=Decimal("100.0000"),
        trigger_reference_price_type="ask",
        trading_date=date(2026, 5, 11),
        time_in_force="day",
        status="active",
        created_at=now,
        updated_at=now,
    )


def _snapshot(symbol: str = "2330", ask: Decimal | None = Decimal("99")) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=None,
        ask_price=ask,
        last_price=None,
        quote_time=QUOTE_TIME,
        received_at=QUOTE_TIME,
    )


@pytest.fixture
def mock_session() -> MagicMock:
    """Mock SQLAlchemy session that supports `with session as db:`."""
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


def _make_dispatcher(*, mock_session: MagicMock, evaluator: MagicMock) -> QuoteEvaluationDispatcher:
    return QuoteEvaluationDispatcher(
        session_factory=lambda: mock_session,
        evaluator=evaluator,
        session_service=TradingSessionService(),
    )


def test_dispatch_triggers_intent_when_evaluator_says_should_trigger(
    monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock
) -> None:
    """Acceptance #2: quote callback → evaluator → TriggerIntentCommand.

    Verifies the dispatcher loads active intents for the snapshot's symbol,
    runs the evaluator, and fires the trigger command on a positive verdict.
    """
    intent = _intent(symbol="2330")
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = [intent]
    mock_cmd = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher.IntentRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher.TriggerIntentCommand", lambda db: mock_cmd)

    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationResult(
        should_trigger=True,
        trigger_price=Decimal("99"),
        trigger_reference_price_type="ask",
        fallback_used=False,
    )

    dispatcher = _make_dispatcher(mock_session=mock_session, evaluator=evaluator)
    dispatcher.dispatch(_snapshot(symbol="2330"))

    mock_repo.system_list_active_by_symbols.assert_called_once_with(["2330"])
    evaluator.evaluate.assert_called_once()
    mock_cmd.execute.assert_called_once()
    trigger_input = mock_cmd.execute.call_args[0][0]
    assert trigger_input.intent_id == intent.id
    assert trigger_input.trigger_price == Decimal("99")
    assert trigger_input.trigger_reference_price_type == "ask"
    assert trigger_input.fallback_used is False


def test_dispatch_skips_when_no_active_intents(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    """No active intent on this symbol → evaluator must not run, no trigger fires."""
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = []
    mock_cmd = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher.IntentRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher.TriggerIntentCommand", lambda db: mock_cmd)

    evaluator = MagicMock()
    dispatcher = _make_dispatcher(mock_session=mock_session, evaluator=evaluator)
    dispatcher.dispatch(_snapshot())

    evaluator.evaluate.assert_not_called()
    mock_cmd.execute.assert_not_called()


def test_dispatch_does_not_trigger_when_condition_not_met(
    monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock
) -> None:
    intent = _intent()
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = [intent]
    mock_cmd = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher.IntentRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher.TriggerIntentCommand", lambda db: mock_cmd)

    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationResult(should_trigger=False)
    dispatcher = _make_dispatcher(mock_session=mock_session, evaluator=evaluator)
    dispatcher.dispatch(_snapshot())

    evaluator.evaluate.assert_called_once()
    mock_cmd.execute.assert_not_called()


def test_dispatch_swallows_unexpected_exception(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    """Broker callback thread must never see an exception escape the listener.

    Even if the repository blows up, dispatch returns None instead of raising.
    """

    def raising_repo(db: object) -> MagicMock:
        repo = MagicMock()
        repo.system_list_active_by_symbols.side_effect = RuntimeError("db went away")
        return repo

    monkeypatch.setattr("app.services.quote_dispatcher.IntentRepository", raising_repo)
    evaluator = MagicMock()
    dispatcher = _make_dispatcher(mock_session=mock_session, evaluator=evaluator)

    # Must not raise
    dispatcher.dispatch(_snapshot())
