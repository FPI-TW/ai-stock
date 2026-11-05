"""Unit tests for the §15 creation caps in TwapConfirmCommand.

TWAP confirm creates a real intent just like CreateTradeIntentCommand, so it must
enforce the same per-user / per-symbol caps. The cap check runs before the plan is
built, so the reject paths never touch build_plan / quote — these stay pure unit
tests driven by mock repo counts.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.commands.trade_intent_core import IntentLimits
from app.commands.twap import TwapConfirmCommand, TwapPlanInput
from app.domain.trade_intent import SymbolIntentLimitExceededError, UserIntentLimitExceededError


def _command(repo: MagicMock, *, limits: IntentLimits) -> TwapConfirmCommand:
    symbol_service = MagicMock()
    symbol_service.get_tradable_symbol.return_value = MagicMock(instrument_type="stock")
    return TwapConfirmCommand(
        symbol_service,
        MagicMock(),  # session_service — not reached on the reject paths
        repo,
        MagicMock(),  # quote_provider
        MagicMock(),  # db
        limits,
    )


def _input() -> TwapPlanInput:
    from datetime import time

    return TwapPlanInput(
        symbol="2330",
        position_side="buy",
        quantity_lots=10,
        interval_seconds=60,
        start_time=None,
        end_time=time(13, 0),
        owner_user_id=uuid4(),
    )


def test_user_cap_rejects_when_at_limit() -> None:
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 200
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20))

    with pytest.raises(UserIntentLimitExceededError) as exc:
        command.execute(_input())

    assert exc.value.limit == 200
    assert exc.value.current == 200
    repo.create_twap.assert_not_called()
    # user cap trips first → per-symbol count not even queried
    repo.count_active_or_scheduled_for_user_symbol.assert_not_called()


def test_symbol_cap_rejects_when_at_limit() -> None:
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 5
    repo.count_active_or_scheduled_for_user_symbol.return_value = 20
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20))

    with pytest.raises(SymbolIntentLimitExceededError) as exc:
        command.execute(_input())

    assert exc.value.symbol == "2330"
    assert exc.value.limit == 20
    assert exc.value.current == 20
    repo.create_twap.assert_not_called()


def test_no_limits_skips_cap_check() -> None:
    # limits=None (DB-less wiring): the cap helper is a no-op and the counts are
    # never queried. We stop right after by making build_plan blow up — the point is
    # only that _enforce_creation_caps did not query or raise.
    repo = MagicMock()
    command = _command(repo, limits=None)  # type: ignore[arg-type]

    command._enforce_creation_caps(_input())

    repo.count_active_or_scheduled_for_user.assert_not_called()
    repo.count_active_or_scheduled_for_user_symbol.assert_not_called()
