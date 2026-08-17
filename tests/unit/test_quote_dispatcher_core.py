"""Unit tests for TradeIntentCoreDispatcher (robot #2) — dispatch 編排邏輯。

以 Mock evaluator / repo / persist 驗行為；真實 DB 端到端由整合測試覆蓋。
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.domain.quote_evaluation import EvaluationResult, QuoteEvaluator
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
    *,
    mock_session: MagicMock,
    evaluator: MagicMock | QuoteEvaluator,
    kill_switch: MagicMock | None = None,
    session_service: TradingSessionService | None = None,
) -> TradeIntentCoreDispatcher:
    return TradeIntentCoreDispatcher(
        session_factory=lambda: mock_session,
        evaluator=evaluator,
        session_service=session_service or TradingSessionService(),
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
    assert persist.call_args[0][1] is intent
    trigger_input = persist.call_args[0][2]
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


# --- trailing：同一 snapshot 同時抬 baseline 又觸發 -------------------------------
# 用真 evaluator（非 Mock）跑，順帶釘死「這情境真的到得了」：last 創高抬 baseline，
# 同一筆的 bid 已在新 dynamic 之下 → update + trigger 同時發生。

TRAILING_NOW = datetime(2026, 5, 12, 10, 0, tzinfo=TAIPEI)  # 週二盤中，quote_time 與 now 對齊


def _trailing_intent(*, baseline: Decimal | None, dynamic_trigger_price: Decimal | None) -> TradeIntentData:
    return TradeIntentData(
        id=uuid4(),
        owner_user_id=uuid4(),
        symbol="2330",
        strategy="trailing_stop_alert",
        execution_mode="notify_only",
        quantity_lots=1,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="bid",
        trading_date=date(2026, 5, 12),
        time_in_force="day",
        status="active",
        created_at=TRAILING_NOW,
        updated_at=TRAILING_NOW,
        trail_mode="fixed_amount",
        trail_value=Decimal("0.5"),
        baseline=baseline,
        dynamic_trigger_price=dynamic_trigger_price,
    )


def _trailing_snapshot() -> QuoteSnapshot:
    # last 120 創高 → baseline=120、dynamic=round_down(119.5)=119.5；bid 119.5 <= 119.5 → 觸發
    return QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal("119.5"),
        ask_price=Decimal("120.5"),
        last_price=Decimal("120"),
        quote_time=TRAILING_NOW,
        received_at=TRAILING_NOW,
    )


@pytest.mark.parametrize(
    "old_baseline, old_dynamic",
    [
        (Decimal("118"), Decimal("117.5")),  # 已有 baseline：舊值會讓稽核寫成「119.5 跌破 117.5」的矛盾紀錄
        (None, None),  # 新建單 baseline 尚未初始化：舊值會讓 persist 直接 RuntimeError
    ],
)
def test_dispatch_trailing_passes_updated_baseline_to_persist(
    monkeypatch: pytest.MonkeyPatch,
    mock_session: MagicMock,
    old_baseline: Decimal | None,
    old_dynamic: Decimal | None,
) -> None:
    intent = _trailing_intent(baseline=old_baseline, dynamic_trigger_price=old_dynamic)
    mock_repo = MagicMock()
    mock_repo.system_list_active_by_symbols.return_value = [intent]
    persist = MagicMock()
    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)
    monkeypatch.setattr("app.services.quote_dispatcher_core.persist_core_trigger", persist)

    session_service = TradingSessionService(clock=lambda: TRAILING_NOW)
    _dispatcher(
        mock_session=mock_session,
        evaluator=QuoteEvaluator(session_service),
        session_service=session_service,
    ).dispatch(_trailing_snapshot())

    # baseline 更新與觸發同一筆報價一起發生
    mock_repo.system_update_trailing_baseline.assert_called_once_with(
        intent.id, Decimal("120"), Decimal("119.5"), TRAILING_NOW
    )
    persist.assert_called_once()
    # persist 讀 intent 上的 trailing 狀態寫稽核/通知 → 必須是這筆報價算出的新值，不是舊值
    persisted_intent = persist.call_args[0][1]
    assert persisted_intent.baseline == Decimal("120")
    assert persisted_intent.dynamic_trigger_price == Decimal("119.5")
    assert persist.call_args[0][2].trigger_price == Decimal("119.5")
    mock_repo.commit.assert_called_once()


def test_dispatch_swallows_unexpected_exception(monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock) -> None:
    def raising_repo(db: object) -> MagicMock:
        repo = MagicMock()
        repo.system_list_active_by_symbols.side_effect = RuntimeError("db went away")
        return repo

    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", raising_repo)
    _dispatcher(mock_session=mock_session, evaluator=MagicMock()).dispatch(_snapshot())  # must not raise


def test_dispatch_runs_lifecycle_before_listing_actives(
    monkeypatch: pytest.MonkeyPatch, mock_session: MagicMock
) -> None:
    """scheduled 單只有先被啟用成 active 才進得了 system_list_active_by_symbols。

    生命週期不掛在這裡的話，沒人打 API 的日子開盤後單子會整天停在 scheduled。
    """

    mock_repo = MagicMock()
    mock_repo.system_activate_scheduled_day_intents.return_value = 1
    mock_repo.system_expire_day_intents_through.return_value = 0
    mock_repo.system_list_active_by_symbols.return_value = []
    monkeypatch.setattr("app.services.quote_dispatcher_core.TradeIntentCoreRepository", lambda db: mock_repo)

    # 盤中時刻，否則 lifecycle 不會走啟用分支
    session_service = TradingSessionService(clock=lambda: datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI))
    _dispatcher(mock_session=mock_session, evaluator=MagicMock(), session_service=session_service).dispatch(_snapshot())

    mock_repo.system_activate_scheduled_day_intents.assert_called_once()
    mock_repo.system_expire_day_intents_through.assert_called_once()
