"""Quote provider abstraction shared by all V0.5+ quote vendors.

Layout split between V1-keep and V1-throwaway code:

- `base`, `validation`, `in_memory`, `factory`: provider-agnostic, kept on V1 migration.
- `shioaji_demo/` subpackage: demo-only hardcode (allowlist, 5-sub quota, Shioaji SDK
  glue). V1 migration removes the directory in one shot. The factory is the only
  place that mentions the demo provider by name.

Public re-exports here are the boundary the rest of the codebase (evaluator,
intent service, API layer) should import from. Avoid importing from
`shioaji_demo` outside the package itself.
"""

from app.services.quote.base import (
    QuoteProvider,
    QuoteProviderError,
    QuoteProviderUnavailableError,
    QuoteSnapshot,
    QuoteUnavailableError,
)
from app.services.quote.factory import build_quote_provider
from app.services.quote.in_memory import InMemoryQuoteProvider
from app.services.quote.intent_reconciler import reconcile_on_create, reconcile_on_terminal
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
    "reconcile_on_create",
    "reconcile_on_terminal",
]
