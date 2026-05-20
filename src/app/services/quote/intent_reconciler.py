"""Bridge between intent CRUD and quote provider subscriptions.

Lives in the provider-agnostic `quote` package so commands can call it without
reaching into any specific provider. Provider-specific exceptions (allowlist,
quota, etc.) bubble up as the common `QuoteProviderError` base; the command
catches that base, rolls back the transaction, and lets the exception handler
in `api/errors.py` translate it to a client envelope.
"""

from typing import Protocol

from app.services.quote.base import QuoteProvider


class IntentSubscriptionCounter(Protocol):
    def count_active_or_scheduled_for_symbol(self, symbol: str) -> int: ...


def reconcile_on_create(provider: QuoteProvider, symbol: str) -> None:
    """Ensure `symbol` is subscribed.

    No-op if already active. Provider-side rules (allowlist / quota) decide whether
    the subscribe call succeeds; this helper has no V0.5-specific knowledge.
    """

    if symbol in provider.active_subscriptions():
        return
    provider.subscribe(symbol)


def reconcile_on_terminal(
    provider: QuoteProvider,
    symbol: str,
    *,
    other_active_count: int,
) -> None:
    """Unsubscribe when no other active/scheduled intent still references `symbol`.

    `other_active_count` is the count of non-terminal intents on the same symbol
    **after** the current intent has moved to a terminal state. The caller (the
    intent command) supplies this from the DB inside the same transaction so the
    decision uses a consistent view.
    """

    if other_active_count > 0:
        return
    provider.unsubscribe(symbol)


def reconcile_after_terminal_transition(
    provider: QuoteProvider,
    intent_repo: IntentSubscriptionCounter,
    symbol: str,
) -> None:
    """Release provider subscription after any intent reaches a terminal state.

    Cancel and trigger transactions should call this after the DB status update has
    flushed, so the count sees only remaining active/scheduled intents for the
    symbol.
    """

    remaining = intent_repo.count_active_or_scheduled_for_symbol(symbol)
    reconcile_on_terminal(provider, symbol, other_active_count=remaining)
