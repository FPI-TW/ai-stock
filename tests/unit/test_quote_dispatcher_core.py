"""Unit tests for TradeIntentCoreDispatcher (robot #2) — dispatch 編排邏輯。

以 Mock evaluator / repo / persist 驗行為；真實 DB 端到端由整合測試覆蓋。
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
from app.domain.trigger_event import DuplicateTriggerError
from app.services.quote.base import QuoteSnapshot
from app.services.quote_dispatcher_core import TradeIntentCoreDispatcher

TAIPEI = ZoneInfo("Asia/Taipei")
QUOTE_TIME = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)


def _intent(symbol: str = "2330") -> TradeIntentData:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
    return TradeIntentData(
        id=uuid4(),
        owner_user_id=uuid4(),
        symbol=symbol,
        strategy="buy_price_alert",
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
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)
    return session


def _dispatcher(
    *, mock_session: MagicMock, evaluator: MagicMock, kill_switch: MagicMock | None = None
) -> TradeIntentCoreDispatcher:
    return TradeIntentCoreDispatcher(
        session_factory=lambda: mock_session,
        evaluator=evaluator,
        session_service=TradingSessionService(),
        kill_switch=kill_switch,
    )


def test_dispatch_triggers_when_evaluator_says_so(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    intent = _intent()
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = [intent]
    persist = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher_core.persist_core_trigger", persist)

    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationResult(
        should_trigger=True,
        trigger_price=Decimal("99"),
        trigger_reference_price_type="ask",
        fallback_used=False,
    )

    _dispatcher(mock_session=mock_session, evaluator=evaluator).dispatch(_snapshot())

    mock_repo.system_list_active_by_symbols.assert_called_once_with(["2330"])
    persist.assert_called_once()
    mock_repo.commit.assert_called_once()
    trigger_input = persist.call_args[0][2]
    assert trigger_input.intent_id == intent.id
    assert trigger_input.trigger_price == Decimal("99")
    assert trigger_input.trigger_reference_price_type == "ask"


def test_dispatch_skips_when_no_active_intents(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = []
    persist = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher_core.persist_core_trigger", persist)
    evaluator = MagicMock()

    _dispatcher(mock_session=mock_session, evaluator=evaluator).dispatch(_snapshot())

    evaluator.evaluate.assert_not_called()
    persist.assert_not_called()


def test_dispatch_no_trigger_when_condition_not_met(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = [_intent()]
    persist = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher_core.persist_core_trigger", persist)
    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationResult(should_trigger=False)

    _dispatcher(mock_session=mock_session, evaluator=evaluator).dispatch(_snapshot())

    persist.assert_not_called()
    mock_repo.commit.assert_not_called()


def test_dispatch_skips_quietly_on_duplicate_trigger(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    """persist 拋 DuplicateTriggerError（UNIQUE 競態）→ dispatcher 安靜跳過、不外拋、不 commit。"""
    intent = _intent()
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = [intent]

    def raising_persist(db: object, _intent: object, inp: object) -> None:
        raise DuplicateTriggerError(intent.id)

    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher_core.persist_core_trigger", raising_persist)
    evaluator = MagicMock()
    evaluator.evaluate.return_value = EvaluationResult(
        should_trigger=True,
        trigger_price=Decimal("99"),
        trigger_reference_price_type="ask",
        fallback_used=False,
    )

    _dispatcher(mock_session=mock_session, evaluator=evaluator).dispatch(_snapshot())  # must not raise

    mock_repo.commit.assert_not_called()


def test_dispatch_skipped_when_kill_switch_halted(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    mock_repo = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)
    evaluator = MagicMock()
    kill_switch = MagicMock()
    kill_switch.is_halted.return_value = True

    _dispatcher(mock_session=mock_session, evaluator=evaluator, kill_switch=kill_switch).dispatch(_snapshot())

    kill_switch.is_halted.assert_called_once()
    mock_repo.system_list_active_by_symbols.assert_not_called()
    evaluator.evaluate.assert_not_called()


def test_dispatch_swallows_unexpected_exception(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    def raising_repo(db: object) -> MagicMock:
        repo = MagicMock()
        repo.system_list_active_by_symbols.side_effect = RuntimeError("db went away")
        return repo

    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", raising_repo)
    _dispatcher(mock_session=mock_session, evaluator=MagicMock()).dispatch(_snapshot())  # must not raise
