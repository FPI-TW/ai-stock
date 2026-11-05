"""Quote provider factory.

Only this module knows the full set of provider names. The `shioaji_demo` package
is **lazy-imported** — top-level `import` is forbidden so that:

- `QUOTE_PROVIDER=in_memory` (CI / unit tests) does not pull in the Shioaji SDK.
- V1 migration can `rm -rf shioaji_demo/` with only this file's mapping touched.
- A broken environment produces a clear actionable error, not a stray ImportError.
"""

from typing import TYPE_CHECKING

from app.core.config import Settings
from app.services.quote.base import QuoteProvider

if TYPE_CHECKING:
    from app.services.quote.fubon.client import FubonCredentials


def build_quote_provider(settings: Settings, *, credentials: "FubonCredentials | None" = None) -> QuoteProvider:
    """Build one provider instance.

    `credentials` is only meaningful for `fubon`: that provider is per-user, so
    the caller (session pool) passes the bound user's login material instead of
    reading anything from settings.
    """

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
                "Run `uv sync` to install required dependencies."
            ) from exc
        return ShioajiQuoteProvider.from_settings(settings)

    if name == "fubon":
        if credentials is None:
            raise ValueError("QUOTE_PROVIDER=fubon is per-user; build_quote_provider needs that user's credentials")
        from app.services.quote.fubon.client import FubonClient
        from app.services.quote.fubon.provider import FubonQuoteProvider

        client = FubonClient(
            personal_id=credentials.personal_id,
            password=credentials.password,
            cert_pfx=credentials.cert_pfx,
            cert_password=credentials.cert_password,
            ws_url=settings.fubon_ws_url,
        )
        return FubonQuoteProvider(client=client)

    # Settings validation (pydantic Literal) catches this before we get here,
    # but keep an explicit branch in case the Literal expands without updating the factory.
    raise ValueError(  # pragma: no cover - guarded by pydantic Literal
        f"Unknown QUOTE_PROVIDER={name!r}; expected one of: in_memory, shioaji_demo, fubon"
    )
