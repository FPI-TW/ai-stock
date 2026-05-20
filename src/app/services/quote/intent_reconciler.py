"""Bridge between intent CRUD and quote provider subscriptions.

Lives in the provider-agnostic `quote` package so commands can call it without
reaching into any specific provider. Provider-specific exceptions (allowlist,
quota, etc.) bubble up as the common `QuoteProviderError` base; the command
catches that base, rolls back the transaction, and lets the exception handler
in `api/errors.py` translate it to a client envelope.
"""

from app.services.quote.base import QuoteProvider


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
