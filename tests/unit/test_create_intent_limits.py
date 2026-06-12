"""Unit tests for the §15 creation caps in CreateTradeIntentCommand.

Covers the work-order requirement: "建立上限邊界（200/201、20/21）". Uses mock
repo counts to drive the boundary; the create / reconcile / trigger tail is kept
inert (reconcile patched to a no-op, kill switch halted so the inline trigger is
skipped) so these stay pure unit tests.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.commands.trade_intent import CreateTradeIntentCommand, CreateTradeIntentInput, IntentLimits
from app.domain.trade_intent import SymbolIntentLimitExceededError, UserIntentLimitExceededError
from app.domain.trading_session import TradingSessionService

# Monday 10:00 Taipei (02:00 UTC) → inside the regular session, so a create lands active.
MONDAY = datetime(2026, 5, 11, 2, 0, tzinfo=UTC)


def _command(repo: MagicMock, *, limits: IntentLimits, kill_switch: MagicMock | None) -> CreateTradeIntentCommand:
    symbol_service = MagicMock()
    symbol_service.get_tradable_symbol.return_value = MagicMock(instrument_type="stock")
    return CreateTradeIntentCommand(
        symbol_service,
        TradingSessionService(clock=lambda: MONDAY),
        repo,
        MagicMock(),  # quote_provider
        MagicMock(),  # evaluator
        MagicMock(),  # db
        kill_switch,
        limits,
    )


def _input() -> CreateTradeIntentInput:
    return CreateTradeIntentInput(
        symbol="2330", strategy="buy_price_alert", quantity_lots=1, owner_user_id=uuid4(), target_price="100"
    )


def test_user_cap_rejects_when_at_limit() -> None:
    """200 active/scheduled already held → the 201st is rejected before any write."""
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 200
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20), kill_switch=None)

    with pytest.raises(UserIntentLimitExceededError) as exc:
        command.execute(_input())

    assert exc.value.limit == 200
    assert exc.value.current == 200
    repo.create.assert_not_called()
    # user cap trips first → per-symbol count not even queried
    repo.count_active_or_scheduled_for_user_symbol.assert_not_called()


def test_symbol_cap_rejects_when_at_limit() -> None:
    """Under the user cap but 20 already held on this symbol → 21st rejected."""
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 5
    repo.count_active_or_scheduled_for_user_symbol.return_value = 20
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20), kill_switch=None)

    with pytest.raises(SymbolIntentLimitExceededError) as exc:
        command.execute(_input())

    assert exc.value.symbol == "2330"
    assert exc.value.limit == 20
    assert exc.value.current == 20
    repo.create.assert_not_called()


def test_allowed_just_below_both_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """199 user / 19 symbol → the 200th / 20th is allowed (create runs)."""
    monkeypatch.setattr("app.commands.trade_intent.reconcile_on_create", lambda provider, symbol: None)
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 199
    repo.count_active_or_scheduled_for_user_symbol.return_value = 19
    repo.create.return_value = uuid4()
    kill_switch = MagicMock()
    kill_switch.is_halted.return_value = True  # skip inline trigger tail

    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20), kill_switch=kill_switch)
    command.execute(_input())

    repo.create.assert_called_once()
