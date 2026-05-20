"""Shioaji-backed demo quote provider — V0.5 only, removed wholesale at V1 migration.

Everything Shioaji-specific lives in this directory:

- `provider.py` — `ShioajiQuoteProvider`, implements the `QuoteProvider` protocol.
- `client.py`   — the only module that imports the `shioaji` SDK.
- `allowlist.py` — 5-symbol demo allowlist + `SymbolNotAvailableInDemo` error.
- `quota.py`   — 5-subscription quota + `QuoteSubscriptionLimitExceeded` error.

The rest of the codebase must not import from here. V1 migration:
1. Add the new licensed provider module / package.
2. Register it in `app/services/quote/factory.py`.
3. `rm -rf` this directory.
4. Drop `SHIOAJI_*` env vars from settings and `.env.example`.
5. Drop the demo-only error codes from `api/errors.py`.
No upper-layer code should require changes.
"""
