"""Demo symbol allowlist — V0.5-only constraint, removed at V1 migration."""

from fastapi import status

from app.services.quote.base import QuoteProviderError

DEFAULT_DEMO_ALLOWED_SYMBOLS: frozenset[str] = frozenset({"2330", "2317", "0050", "00878"})


class SymbolNotAvailableInDemo(QuoteProviderError):
    """The symbol exists in the symbol master but the Shioaji demo tier won't serve it.

    Demo-only: V1 migration removes this error class along with the entire
    `shioaji_demo/` directory; licensed vendors expose every symbol in the master.
    """

    error_code = "SYMBOL_NOT_AVAILABLE_IN_DEMO"
    http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
    default_message = "此標的不在 Shioaji demo 白名單，無法訂閱"

    def __init__(self, symbol: str, allowed: frozenset[str]) -> None:
        super().__init__(symbol)
        self.symbol = symbol
        self.allowed = allowed

    def details(self) -> dict[str, object]:
        return {"symbol": self.symbol, "allowed": sorted(self.allowed)}


def parse_allowlist_override(raw: str | None) -> frozenset[str]:
    """Parse the comma-separated `SHIOAJI_DEMO_ALLOWED_SYMBOLS` env into a frozenset.

    Empty / unset → use the built-in 4-symbol default. Whitespace is stripped per token.
    """

    if not raw:
        return DEFAULT_DEMO_ALLOWED_SYMBOLS
    tokens = {tok.strip() for tok in raw.split(",") if tok.strip()}
    return frozenset(tokens) if tokens else DEFAULT_DEMO_ALLOWED_SYMBOLS
