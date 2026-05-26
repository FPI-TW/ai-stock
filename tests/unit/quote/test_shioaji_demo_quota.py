"""Subscription quota tests for the Shioaji demo provider."""

from unittest.mock import MagicMock

import pytest

from app.services.quote.base import QuoteProviderUnavailableError
from app.services.quote.shioaji_demo.provider import ShioajiQuoteProvider
from app.services.quote.shioaji_demo.quota import QuoteSubscriptionLimitExceeded


def _make_provider(*, max_subs: int = 5) -> tuple[ShioajiQuoteProvider, MagicMock]:
    client = MagicMock()
    provider = ShioajiQuoteProvider(
        client=client,
        allowed_symbols=frozenset({"2330", "2317", "0050", "00878", "2603", "1101"}),
        max_subscriptions=max_subs,
    )
    provider.mark_started_for_tests()
    return provider, client


def test_fifth_subscription_succeeds() -> None:
    provider, _ = _make_provider()
    for symbol in ("2330", "2317", "0050", "00878", "2603"):
        provider.subscribe(symbol)
    assert len(provider.active_subscriptions()) == 5


def test_sixth_subscription_raises_quota_error() -> None:
    provider, client = _make_provider()
    for symbol in ("2330", "2317", "0050", "00878", "2603"):
        provider.subscribe(symbol)

    with pytest.raises(QuoteSubscriptionLimitExceeded) as exc:
        provider.subscribe("1101")
    assert exc.value.symbol == "1101"
    assert exc.value.limit == 5
    assert exc.value.current == 5
    # The 6th symbol must not be sent to Shioaji — quota guards before the client call.
    assert client.subscribe.call_count == 5


def test_unsubscribe_frees_quota() -> None:
    provider, _ = _make_provider()
    for symbol in ("2330", "2317", "0050", "00878", "2603"):
        provider.subscribe(symbol)

    provider.unsubscribe("2330")
    provider.subscribe("1101")  # quota slot reclaimed

    assert "2330" not in provider.active_subscriptions()
    assert "1101" in provider.active_subscriptions()
    assert len(provider.active_subscriptions()) == 5


def test_resubscribing_same_symbol_is_idempotent() -> None:
    provider, client = _make_provider()
    provider.subscribe("2330")
    provider.subscribe("2330")
    assert provider.active_subscriptions() == {"2330"}
    # Real broker subscribe should only be called once per unique symbol.
    client.subscribe.assert_called_once()


def test_failed_subscribe_attempt_cleans_up_remote_partial_subscription() -> None:
    provider, client = _make_provider()
    client.subscribe.side_effect = QuoteProviderUnavailableError("quote", "partial subscribe failed")

    with pytest.raises(QuoteProviderUnavailableError):
        provider.subscribe("2330")

    assert provider.active_subscriptions() == set()
    client.unsubscribe.assert_called_once_with("2330")
