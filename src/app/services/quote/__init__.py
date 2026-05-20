"""Quote provider abstraction shared by all V0.5+ quote vendors."""

from app.services.quote.base import (
    QuoteProvider,
    QuoteProviderError,
    QuoteProviderUnavailableError,
    QuoteSnapshot,
    QuoteUnavailableError,
)
from app.services.quote.factory import build_quote_provider
from app.services.quote.in_memory import InMemoryQuoteProvider
from app.services.quote.intent_reconciler import (
    reconcile_after_terminal_transition,
    reconcile_on_create,
    reconcile_on_terminal,
)
from app.services.quote.validation import QuoteValidator, ValidatedQuote

__all__ = [
    "QuoteProvider",
    "QuoteProviderError",
    "QuoteProviderUnavailableError",
    "QuoteSnapshot",
    "QuoteUnavailableError",
    "QuoteValidator",
    "ValidatedQuote",
    "InMemoryQuoteProvider",
    "build_quote_provider",
    "reconcile_after_terminal_transition",
    "reconcile_on_create",
    "reconcile_on_terminal",
]
