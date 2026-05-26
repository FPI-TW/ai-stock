"""Optional provider capability for synchronous current-price lookup.

This is intentionally separate from `QuoteProvider`: most provider users only
need subscription-backed snapshots, while the V0.5 test endpoint needs a narrow
broker snapshot lookup capability.
"""

from typing import Protocol, runtime_checkable

from app.services.quote.base import QuoteSnapshot


@runtime_checkable
class CurrentPriceProvider(Protocol):
    def get_current_price(self, symbol: str) -> QuoteSnapshot: ...
