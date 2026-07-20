"""Unit tests for the minimal new-slice CreateTradeIntentCommand (commands/trade_intent_core).

只驗本 command 自己的職責：§15 限額邊界、策略驗證、以及用正確參數呼叫新 repo。
建單即觸發（PR3 補上）在此一律走「取不到報價」分支短路掉——那條路徑由
tests/test_create_immediate_trigger_integration.py（integration）覆蓋。
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.commands.trade_intent_core import CreateTradeIntentCommand, CreateTradeIntentInput, IntentLimits
from app.domain.trade_intent import SymbolIntentLimitExceededError, UserIntentLimitExceededError
from app.domain.trading_session import TradingSessionService

# Monday 10:00 Taipei (02:00 UTC) → 盤中，建單落 active
MONDAY = datetime(2026, 5, 11, 2, 0, tzinfo=UTC)


def _command(repo: MagicMock, *, limits: IntentLimits | None) -> CreateTradeIntentCommand:
    symbol_service = MagicMock()
    symbol_service.get_tradable_symbol.return_value = MagicMock(instrument_type="stock")
    quote_provider = MagicMock()
    quote_provider.get_quotes.return_value = []  # 無報價 → inline 觸發短路，本檔只看建單本身
    return CreateTradeIntentCommand(
        symbol_service,
        TradingSessionService(clock=lambda: MONDAY),
        repo,
        quote_provider,
        MagicMock(),  # evaluator（因無報價而不會被呼叫）
        MagicMock(),  # db
        None,  # kill_switch
        limits,
    )


def _input(strategy: str = "buy_price_alert", **kw: object) -> CreateTradeIntentInput:
    base: dict[str, object] = {
        "symbol": "2330",
        "strategy": strategy,
        "quantity_lots": 1,
        "owner_user_id": uuid4(),
        "target_price": "100",
    }
    base.update(kw)
    return CreateTradeIntentInput(**base)  # type: ignore[arg-type]


def test_user_cap_rejects_before_write() -> None:
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 200
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20))

    with pytest.raises(UserIntentLimitExceededError) as exc:
        command.execute(_input())

    assert exc.value.limit == 200
    repo.create.assert_not_called()
    repo.count_active_or_scheduled_for_user_symbol.assert_not_called()


def test_symbol_cap_rejects_before_write() -> None:
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 5
    repo.count_active_or_scheduled_for_user_symbol.return_value = 20
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20))

    with pytest.raises(SymbolIntentLimitExceededError) as exc:
        command.execute(_input())

    assert exc.value.symbol == "2330"
    repo.create.assert_not_called()


def test_allowed_just_below_caps_calls_create() -> None:
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 199
    repo.count_active_or_scheduled_for_user_symbol.return_value = 19
    repo.create.return_value = uuid4()
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20))

    command.execute(_input())

    repo.create.assert_called_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["strategy"] == "buy_price_alert"
    assert kwargs["target_price_effective"] == Decimal("100")
    assert kwargs["trigger_reference_price_type"] == "ask"


def test_market_order_creates_without_target_price() -> None:
    repo = MagicMock()
    repo.create.return_value = uuid4()
    command = _command(repo, limits=None)

    command.execute(_input(strategy="market_buy_order", target_price=None))

    kwargs = repo.create.call_args.kwargs
    assert kwargs["target_price_effective"] is None
    assert kwargs["trigger_reference_price_type"] == "ask"


def test_trailing_requires_trail_fields() -> None:
    repo = MagicMock()
    command = _command(repo, limits=None)

    with pytest.raises(ValueError, match="trail_mode and trail_value"):
        command.execute(_input(strategy="trailing_stop_alert", target_price=None))
    repo.create.assert_not_called()


def test_trailing_passes_trail_value_to_repo() -> None:
    repo = MagicMock()
    repo.create.return_value = uuid4()
    command = _command(repo, limits=None)

    command.execute(
        _input(strategy="trailing_stop_alert", target_price=None, trail_mode="percentage", trail_value=Decimal("5"))
    )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["trail_mode"] == "percentage"
    assert kwargs["trail_value"] == Decimal("5")
    assert kwargs["target_price_effective"] is None
