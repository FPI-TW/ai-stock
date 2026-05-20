"""Quote provider factory.

Only this module knows the full set of provider names. The `shioaji_demo` package
is **lazy-imported** — top-level `import` is forbidden so that:

- `QUOTE_PROVIDER=in_memory` (CI / unit tests) does not pull in the Shioaji SDK.
- V1 migration can `rm -rf shioaji_demo/` with only this file's mapping touched.
- Missing Shioaji SDK produces a clear actionable error, not a stray ImportError.
"""

from app.core.config import Settings
from app.services.quote.base import QuoteProvider


def build_quote_provider(settings: Settings) -> QuoteProvider:
    name = settings.quote_provider

    if name == "in_memory":
        from app.services.quote.in_memory import InMemoryQuoteProvider

        return InMemoryQuoteProvider()

    if name == "shioaji_demo":
        try:
            from app.services.quote.shioaji_demo.provider import ShioajiQuoteProvider
        except ImportError as exc:  # pragma: no cover - dependency-missing branch
            raise RuntimeError(
                "QUOTE_PROVIDER=shioaji_demo but the `shioaji` package is not installed. "
                "Run `uv sync --extra shioaji_demo` (or switch to QUOTE_PROVIDER=in_memory for CI)."
            ) from exc
        return ShioajiQuoteProvider.from_settings(settings)

    # Settings validation (pydantic Literal) catches this before we get here,
    # but keep an explicit branch in case the Literal expands without updating the factory.
    raise ValueError(  # pragma: no cover - guarded by pydantic Literal
        f"Unknown QUOTE_PROVIDER={name!r}; expected one of: in_memory, shioaji_demo"
    )
