"""Allowlist enforcement tests.

These exercise `ShioajiQuoteProvider.subscribe` without ever importing the
`shioaji` SDK — the provider takes a duck-typed client in its constructor, and
allowlist rejection short-circuits before any client method is called.
"""

from unittest.mock import MagicMock

import pytest

from app.services.quote.shioaji_demo.allowlist import (
    DEFAULT_DEMO_ALLOWED_SYMBOLS,
    SymbolNotAvailableInDemo,
    parse_allowlist_override,
)
from app.services.quote.shioaji_demo.provider import ShioajiQuoteProvider


def _make_provider(*, allowed: frozenset[str] = DEFAULT_DEMO_ALLOWED_SYMBOLS) -> tuple[ShioajiQuoteProvider, MagicMock]:
    client = MagicMock()
    provider = ShioajiQuoteProvider(client=client, allowed_symbols=allowed, max_subscriptions=5)
    # Skip the real `startup()` path (which would call shioaji login) so
    # `subscribe` can proceed to the client call when warranted.
    provider.mark_started_for_tests()
    return provider, client


def test_subscribe_allowed_symbol_calls_client() -> None:
    provider, client = _make_provider()
    provider.subscribe("2330")
    client.subscribe.assert_called_once_with("2330")
    assert provider.active_subscriptions() == {"2330"}


def test_current_price_allowed_symbol_calls_client_snapshot() -> None:
    provider, client = _make_provider()
    snapshot = MagicMock()
    client.get_stock_snapshot.return_value = snapshot

    assert provider.get_current_price("2330") is snapshot
    client.get_stock_snapshot.assert_called_once_with("2330")


def test_subscribe_disallowed_symbol_rejects_without_calling_client() -> None:
    provider, client = _make_provider()
    with pytest.raises(SymbolNotAvailableInDemo) as exc:
        provider.subscribe("1101")
    assert exc.value.symbol == "1101"
    assert sorted(exc.value.allowed) == sorted(DEFAULT_DEMO_ALLOWED_SYMBOLS)
    client.subscribe.assert_not_called()
    assert provider.active_subscriptions() == set()


def test_current_price_disallowed_symbol_rejects_without_calling_client() -> None:
    provider, client = _make_provider()
    with pytest.raises(SymbolNotAvailableInDemo):
        provider.get_current_price("1101")
    client.get_stock_snapshot.assert_not_called()


def test_subscribe_with_custom_allowlist() -> None:
    provider, client = _make_provider(allowed=frozenset({"6505"}))
    provider.subscribe("6505")
    with pytest.raises(SymbolNotAvailableInDemo):
        provider.subscribe("2330")
    assert provider.active_subscriptions() == {"6505"}


def test_parse_allowlist_override_uses_default_when_empty() -> None:
    assert parse_allowlist_override(None) == DEFAULT_DEMO_ALLOWED_SYMBOLS
    assert parse_allowlist_override("") == DEFAULT_DEMO_ALLOWED_SYMBOLS
    assert parse_allowlist_override("   ") == DEFAULT_DEMO_ALLOWED_SYMBOLS


def test_parse_allowlist_override_splits_and_trims() -> None:
    result = parse_allowlist_override(" 2330 , 1101 ,, 0050 ")
    assert result == frozenset({"2330", "1101", "0050"})
