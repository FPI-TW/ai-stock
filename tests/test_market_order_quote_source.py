from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.commands.trade_intent import CreateTradeIntentCommand, CreateTradeIntentInput
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.services.quote.base import QuoteProvider, QuoteSnapshot, QuoteUnavailableError
from app.services.symbol import SymbolService

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _snapshot(symbol: str = "2330") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=Decimal("599"),
        ask_price=Decimal("600"),
        last_price=Decimal("599.5"),
        quote_time=datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI),
        received_at=datetime(2026, 5, 11, 2, 0, tzinfo=UTC),
    )


class CurrentPriceOnlyProvider:
    def __init__(self, snapshot: QuoteSnapshot) -> None:
        self.snapshot = snapshot
        self.current_price_symbols: list[str] = []

    def get_current_price(self, symbol: str) -> QuoteSnapshot:
        self.current_price_symbols.append(symbol)
        return self.snapshot

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        raise QuoteUnavailableError(symbols[0])


class CacheOnlyProvider:
    def __init__(self, snapshot: QuoteSnapshot) -> None:
        self.snapshot = snapshot

    def get_quotes(self, _symbols: list[str]) -> list[QuoteSnapshot]:
        return [self.snapshot]


class CurrentPriceUnavailableProvider(CacheOnlyProvider):
    def __init__(self, snapshot: QuoteSnapshot) -> None:
        super().__init__(snapshot)
        self.current_price_symbols: list[str] = []

    def get_current_price(self, symbol: str) -> QuoteSnapshot:
        self.current_price_symbols.append(symbol)
        raise QuoteUnavailableError(symbol)


class AllUnavailableProvider(CurrentPriceUnavailableProvider):
    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        raise QuoteUnavailableError(symbols[0])


class RecordingUnavailableProvider(AllUnavailableProvider):
    def __init__(self) -> None:
        super().__init__(_snapshot())
        self.subscribed_symbols: list[str] = []
        self.quote_symbols: list[str] = []

    def subscribe(self, symbol: str) -> None:
        self.subscribed_symbols.append(symbol)

    def active_subscriptions(self) -> set[str]:
        return set(self.subscribed_symbols)

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        self.quote_symbols.extend(symbols)
        raise QuoteUnavailableError(symbols[0])


class NoUsablePriceProvider(CacheOnlyProvider):
    def __init__(self) -> None:
        super().__init__(
            QuoteSnapshot(
                symbol="2330",
                bid_price=None,
                ask_price=None,
                last_price=None,
                quote_time=datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI),
                received_at=datetime(2026, 5, 11, 2, 0, tzinfo=UTC),
            )
        )


class FakeSymbolService:
    def get_tradable_symbol(self, _symbol: str) -> SimpleNamespace:
        return SimpleNamespace(instrument_type="stock")


class FakeIntentRepository:
    def __init__(self) -> None:
        self.intent_id = uuid4()
        self.created: dict[str, object] | None = None

    def create(self, **kwargs: object) -> UUID:
        self.created = kwargs
        return self.intent_id

    def find_by_id(self, intent_id: UUID, _owner_user_id: UUID) -> SimpleNamespace:
        assert self.created is not None
        return SimpleNamespace(id=intent_id, status=self.created["status"])


class FakeDb:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def execute(self, _stmt: object) -> object:
        raise AssertionError("DB execute should not be called when no market quote is usable")


def _command_with_provider(provider: object) -> CreateTradeIntentCommand:
    command = CreateTradeIntentCommand.__new__(CreateTradeIntentCommand)
    command._quote_provider = cast(QuoteProvider, provider)
    return command


def _full_command(
    provider: object,
    *,
    now: datetime,
    repo: FakeIntentRepository | None = None,
    db: FakeDb | None = None,
) -> tuple[CreateTradeIntentCommand, FakeIntentRepository, FakeDb]:
    repo = repo or FakeIntentRepository()
    db = db or FakeDb()
    return (
        CreateTradeIntentCommand(
            cast(SymbolService, FakeSymbolService()),
            TradingSessionService(clock=lambda: now),
            cast(IntentRepository, repo),
            cast(QuoteProvider, provider),
            cast(QuoteEvaluator, object()),  # evaluator is not used by market_order tests below
            cast(Session, db),
        ),
        repo,
        db,
    )


def _market_order_input(strategy: str = "market_buy_order") -> CreateTradeIntentInput:
    return CreateTradeIntentInput(
        symbol="2330",
        strategy=strategy,
        quantity_lots=2,
        owner_user_id=OWNER_ID,
        transaction_mode="partial_fill_allowed",
        notification_mode="single",
    )


def test_market_order_quote_uses_current_price_when_stream_cache_is_cold() -> None:
    provider = CurrentPriceOnlyProvider(_snapshot())
    command = _command_with_provider(provider)

    quote = command._get_market_order_quote("2330")

    assert quote.ask_price == Decimal("600")
    assert provider.current_price_symbols == ["2330"]


def test_market_order_quote_falls_back_to_stream_cache_without_current_price_provider() -> None:
    command = _command_with_provider(CacheOnlyProvider(_snapshot()))

    quote = command._get_market_order_quote("2330")

    assert quote.ask_price == Decimal("600")


def test_market_order_quote_falls_back_to_stream_cache_when_current_price_is_unavailable() -> None:
    provider = CurrentPriceUnavailableProvider(_snapshot())
    command = _command_with_provider(provider)

    quote = command._get_market_order_quote("2330")

    assert quote.ask_price == Decimal("600")
    assert provider.current_price_symbols == ["2330"]


def test_market_order_quote_raises_when_all_sources_are_unavailable() -> None:
    command = _command_with_provider(AllUnavailableProvider(_snapshot()))

    with pytest.raises(QuoteUnavailableError):
        command._get_market_order_quote("2317")


def test_market_order_create_before_open_schedules_without_quote_lookup() -> None:
    provider = RecordingUnavailableProvider()
    command, repo, db = _full_command(
        provider,
        now=datetime(2026, 5, 11, 8, 30, tzinfo=TAIPEI),
    )

    result = command.execute(_market_order_input("market_sell_order"))

    assert result.status == "scheduled"
    assert repo.created is not None
    assert repo.created["status"] == "scheduled"
    assert repo.created["strategy"] == "market_sell_order"
    assert provider.subscribed_symbols == ["2330"]
    assert provider.current_price_symbols == []
    assert provider.quote_symbols == []
    assert db.commits == 1
    assert db.rollbacks == 0


def test_market_order_create_inside_session_stays_active_when_quote_unavailable() -> None:
    provider = RecordingUnavailableProvider()
    command, repo, db = _full_command(
        provider,
        now=datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI),
    )

    result = command.execute(_market_order_input())

    assert result.status == "active"
    assert repo.created is not None
    assert repo.created["status"] == "active"
    assert provider.subscribed_symbols == ["2330"]
    assert provider.current_price_symbols == ["2330"]
    assert provider.quote_symbols == ["2330"]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_market_order_inline_trigger_skips_when_quote_has_no_usable_price() -> None:
    command = _command_with_provider(NoUsablePriceProvider())
    command._db = cast(Session, FakeDb())

    command._apply_inline_market_order_trigger(uuid4(), "2330")
