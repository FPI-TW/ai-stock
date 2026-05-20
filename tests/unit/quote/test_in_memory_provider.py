from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.services.quote.base import QuoteSnapshot, QuoteUnavailableError
from app.services.quote.in_memory import InMemoryQuoteProvider

UTC = ZoneInfo("UTC")
TAIPEI = ZoneInfo("Asia/Taipei")


def _snap(symbol: str = "2330", last: str = "590") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        bid_price=Decimal("589"),
        ask_price=Decimal("591"),
        last_price=Decimal(last),
        quote_time=datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI),
        received_at=datetime.now(tz=UTC),
    )


def test_push_and_get() -> None:
    provider = InMemoryQuoteProvider()
    provider.push_quote(_snap())
    [snap] = provider.get_quotes(["2330"])
    assert snap.symbol == "2330"


def test_get_unsubscribed_symbol_raises() -> None:
    provider = InMemoryQuoteProvider()
    with pytest.raises(QuoteUnavailableError) as exc:
        provider.get_quotes(["2330"])
    assert exc.value.symbol == "2330"


def test_subscribe_unsubscribe_tracks_active_set() -> None:
    provider = InMemoryQuoteProvider()
    provider.subscribe("2330")
    provider.subscribe("0050")
    assert provider.active_subscriptions() == {"2330", "0050"}
    provider.unsubscribe("2330")
    assert provider.active_subscriptions() == {"0050"}


def test_shutdown_clears_state() -> None:
    provider = InMemoryQuoteProvider()
    provider.push_quote(_snap())
    provider.subscribe("0050")
    provider.shutdown()
    assert provider.active_subscriptions() == set()
    with pytest.raises(QuoteUnavailableError):
        provider.get_quotes(["2330"])


def test_in_memory_has_no_quota() -> None:
    """The 5-subscription limit is a demo-only concern; tests get unlimited symbols."""

    provider = InMemoryQuoteProvider()
    for symbol in ("2330", "2317", "0050", "00878", "9999", "1101"):
        provider.subscribe(symbol)
    assert len(provider.active_subscriptions()) == 6
