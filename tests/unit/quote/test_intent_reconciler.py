from unittest.mock import MagicMock

from app.services.quote.in_memory import InMemoryQuoteProvider
from app.services.quote.intent_reconciler import reconcile_after_terminal_transition


def test_terminal_transition_releases_subscription_when_no_active_intent_remains() -> None:
    provider = InMemoryQuoteProvider()
    provider.subscribe("2330")
    repo = MagicMock()
    repo.count_active_or_scheduled_for_symbol.return_value = 0

    reconcile_after_terminal_transition(provider, repo, "2330")

    assert provider.active_subscriptions() == set()
    repo.count_active_or_scheduled_for_symbol.assert_called_once_with("2330")


def test_terminal_transition_keeps_subscription_when_other_intents_remain() -> None:
    provider = InMemoryQuoteProvider()
    provider.subscribe("2330")
    repo = MagicMock()
    repo.count_active_or_scheduled_for_symbol.return_value = 1

    reconcile_after_terminal_transition(provider, repo, "2330")

    assert provider.active_subscriptions() == {"2330"}
