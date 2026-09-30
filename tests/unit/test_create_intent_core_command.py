"""Unit tests for the minimal new-slice CreateTradeIntentCommand (commands/trade_intent_core).

只驗本 command 自己的職責：§15 限額邊界、策略驗證、以及用正確參數呼叫新 repo。
建單即觸發（PR3 補上）在此一律走「取不到報價」分支短路掉——那條路徑由
tests/test_create_immediate_trigger_integration.py（integration）覆蓋。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.commands.trade_intent_core import (
    CancelTradeIntentCommand,
    CancelTradeIntentInput,
    CreateTradeIntentCommand,
    CreateTradeIntentInput,
    IntentLimits,
)
from app.core.config import get_settings
from app.domain.broker_account import BrokerAccountNotBoundError
from app.domain.trade_intent import SymbolIntentLimitExceededError, UserIntentLimitExceededError
from app.domain.trading_session import TradingSessionService
from app.services.broker_session_pool import BrokerSessionPool
from app.services.quote.base import QuoteProviderUnavailableError

# Monday 10:00 Taipei (02:00 UTC) → 盤中，建單落 active
MONDAY = datetime(2026, 5, 11, 2, 0, tzinfo=UTC)


def _shared_pool(quote_provider: MagicMock) -> BrokerSessionPool:
    """in_memory 模式：所有人共用同一個 provider，與 conftest 的預設一致。"""
    return BrokerSessionPool(get_settings(), shared=quote_provider)


def _per_user_pool() -> BrokerSessionPool:
    return BrokerSessionPool(get_settings(), shared=None, provider_factory=lambda _creds: MagicMock())


def _command(
    repo: MagicMock, *, limits: IntentLimits | None, pool: BrokerSessionPool | None = None
) -> CreateTradeIntentCommand:
    symbol_service = MagicMock()
    symbol_service.get_tradable_symbol.return_value = MagicMock(instrument_type="stock")
    quote_provider = MagicMock()
    quote_provider.get_quotes.return_value = []  # 無報價 → inline 觸發短路，本檔只看建單本身
    return CreateTradeIntentCommand(
        symbol_service,
        TradingSessionService(clock=lambda: MONDAY),
        repo,
        pool or _shared_pool(quote_provider),
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


def test_provider_error_from_get_quotes_does_not_block_create() -> None:
    """報價端任何失敗都不該擋建單——委託照樣以 active 落地，等 robot #2 下次補觸發。

    只 catch QuoteUnavailableError 的話，別種 QuoteProviderError（provider 未啟動、
    demo allowlist、訂閱額度）會把整筆建單回滾，與 command docstring 的契約相反。
    """

    repo = MagicMock()
    repo.create.return_value = uuid4()
    symbol_service = MagicMock()
    symbol_service.get_tradable_symbol.return_value = MagicMock(instrument_type="stock")
    quote_provider = MagicMock()
    quote_provider.get_quotes.side_effect = QuoteProviderUnavailableError("shioaji", "provider not started")
    db = MagicMock()
    command = CreateTradeIntentCommand(
        symbol_service,
        TradingSessionService(clock=lambda: MONDAY),
        repo,
        _shared_pool(quote_provider),
        MagicMock(),  # evaluator（報價取不到 → 不會被呼叫）
        db,
        None,
        None,
    )

    command.execute(_input())

    repo.create.assert_called_once()
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


# --- per-user quote sessions（docs/architecture.md「交易意圖資料流」） -------------------------


def test_unbound_owner_is_refused_after_limits_and_before_write() -> None:
    """沒有本人 session 就沒有行情：409 BROKER_ACCOUNT_NOT_BOUND，不落列、不訂閱。
    限額先於綁定檢查——超限的人先看到超限。"""
    repo = MagicMock()
    repo.count_active_or_scheduled_for_user.return_value = 0
    repo.count_active_or_scheduled_for_user_symbol.return_value = 0
    command = _command(repo, limits=IntentLimits(per_user=200, per_symbol=20), pool=_per_user_pool())

    with pytest.raises(BrokerAccountNotBoundError):
        command.execute(_input())
    repo.create.assert_not_called()

    repo.count_active_or_scheduled_for_user.return_value = 200
    with pytest.raises(UserIntentLimitExceededError):
        command.execute(_input())


def test_create_subscribes_on_the_owners_own_session() -> None:
    pool = _per_user_pool()
    owner = uuid4()
    candidate = pool.prepare(owner, MagicMock())
    pool.activate(candidate)
    provider = cast(MagicMock, candidate.provider)
    provider.active_subscriptions.return_value = set()
    provider.get_quotes.return_value = []
    repo = MagicMock()
    repo.create.return_value = uuid4()

    _command(repo, limits=None, pool=pool).execute(_input(owner_user_id=owner))

    provider.subscribe.assert_called_once_with("2330")


def _cancel(repo: MagicMock, pool: BrokerSessionPool) -> CancelTradeIntentCommand:
    return CancelTradeIntentCommand(repo, pool, MagicMock())


def test_cancel_in_per_user_mode_unsubscribes_when_the_owner_has_no_other_intent_on_the_symbol() -> None:
    """跨 owner 計數會讓本人 session 永不退訂（別人還有這檔，但別人的單不在我的 session 上）。"""
    pool = _per_user_pool()
    owner = uuid4()
    candidate = pool.prepare(owner, MagicMock())
    pool.activate(candidate)
    repo = MagicMock()
    repo.cancel.return_value = MagicMock(symbol="2330", id=uuid4())
    repo.count_active_or_scheduled_for_user_symbol.return_value = 0
    repo.count_active_or_scheduled_for_symbol.return_value = 3  # other owners still watch it

    _cancel(repo, pool).execute(CancelTradeIntentInput(intent_id=uuid4(), owner_user_id=owner))

    repo.count_active_or_scheduled_for_user_symbol.assert_called_once_with(owner, "2330")
    cast(MagicMock, candidate.provider).unsubscribe.assert_called_once_with("2330")


def test_cancel_in_per_user_mode_keeps_the_subscription_while_the_owner_has_another_intent_on_it() -> None:
    """Two 2330 intents; cancelling the first leaves one — the owner's session must
    keep the symbol. The count is taken after the cancel is committed, so it is
    exactly "what is still open", not "what was open"."""
    pool = _per_user_pool()
    owner = uuid4()
    candidate = pool.prepare(owner, MagicMock())
    pool.activate(candidate)
    repo = MagicMock()
    repo.cancel.return_value = MagicMock(symbol="2330", id=uuid4())
    repo.count_active_or_scheduled_for_user_symbol.return_value = 1

    _cancel(repo, pool).execute(CancelTradeIntentInput(intent_id=uuid4(), owner_user_id=owner))

    cast(MagicMock, candidate.provider).unsubscribe.assert_not_called()


def test_cancel_in_shared_mode_keeps_the_subscription_while_anyone_still_needs_it() -> None:
    provider = MagicMock()
    repo = MagicMock()
    repo.cancel.return_value = MagicMock(symbol="2330", id=uuid4())
    repo.count_active_or_scheduled_for_user_symbol.return_value = 0
    repo.count_active_or_scheduled_for_symbol.return_value = 1

    _cancel(repo, _shared_pool(provider)).execute(CancelTradeIntentInput(intent_id=uuid4(), owner_user_id=uuid4()))

    provider.unsubscribe.assert_not_called()
    repo.count_active_or_scheduled_for_user_symbol.assert_not_called()


def test_cancel_without_a_session_still_cancels() -> None:
    """解綁後殘留的取消請求：DB 取消照做，沒有 session 可退訂就跳過，不 500。"""
    repo = MagicMock()
    repo.cancel.return_value = MagicMock(symbol="2330", id=uuid4())

    _cancel(repo, _per_user_pool()).execute(CancelTradeIntentInput(intent_id=uuid4(), owner_user_id=uuid4()))

    repo.cancel.assert_called_once()
