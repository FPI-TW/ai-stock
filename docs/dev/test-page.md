# `/test` — LOCAL_MODE API tester

A single-page in-app API tester served at `GET /test` when `LOCAL_MODE=true`.
Designed for PM / QA / FE handoff and quick end-to-end smoke tests during
development. Not part of the production surface.

## TL;DR

```bash
LOCAL_MODE=true QUOTE_PROVIDER=in_memory \
  DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock \
  uv run uvicorn app.main:app --app-dir src --reload
```

Open <http://127.0.0.1:8000/test>.

## What it does

- **Endpoint catalog**: every API the backend exposes (✅) and every API
  planned in `docs/orders/` (📝 with ticket number, disabled). Click a row
  to load it into the request builder.
- **Request builder**: edit path params / query string / headers / JSON body
  and `Send`. Each endpoint can supply pre-canned example payloads via the
  *Fill example* dropdown.
- **Response viewer**: pretty-prints the body, decodes the error envelope
  (`error.code` / `error.message`), and echoes the server-issued
  `X-Request-Id`. The last 5 responses sit in the history list and can be
  replayed.
- **User switcher** (top bar): swap among `default`, `alice`, `bob`,
  `charlie`. Non-default labels send `X-Local-User-Id` on every request so
  the backend treats the session as that user — used to demo owner scoping
  without standing up real auth (which arrives in BE-V1-01).
- **Clock control** (top bar): freeze / advance / reset via
  `POST /dev/set-clock`. Shows `🧊` when frozen and a 盤中 / 盤外 hint
  derived from `withinRegularSession`.
- **Demo flows** (bottom panel): three pre-scripted scenarios that string
  together multiple API calls:
  1. *E2E: buy_price_alert 觸發* — create intent, push matching quote, poll
     notifications.
  2. *E2E: limit_buy_order 觸發* — same but with the V0.5-15 strategy and
     `partial_fill_allowed` transaction mode.
  3. *Multi-user owner-scoping* — alice creates, bob lists (sees nothing),
     alice lists (sees her own).

Each step logs its outcome inline so you can scrub through what happened.

## Architecture notes

- **No build step for the tester**. Vanilla HTML + ES modules + plain CSS,
  served via `StaticFiles` from `src/app/static/test/`. The main `/test`
  console works offline; `/test/flows` loads Mermaid from a version-pinned CDN
  with SRI because the flowchart renderer is documentation-only.
- **Endpoint catalog is hardcoded** in `endpoints.js`. When a planned
  endpoint lands, flip its `implemented: false` to `true` in the same PR;
  treat the flip as a UI acceptance check for the work order.
- **Catalog drift detection**: on load the page fetches `/openapi.json` and
  `console.warn`s for any `implemented: true` entry whose method/path is
  not in the spec. It does **not** auto-generate forms from OpenAPI
  (discriminated unions render poorly) — examples are preferred.
- **Process-global state**: `/dev/set-clock` mutates the
  `TradingSessionService` instance stored on `app.state` so the API path,
  lifespan dispatcher, and `/dev/server-state` agree on the clock. This is
  one instance per uvicorn worker — LOCAL_MODE runs 1 worker, so the
  caveat is acceptable. `/dev/server-state` reports `workerPid` to make
  this visible.

## Safety

Two independent gates keep this off production:

| Surface | LOCAL_MODE=true | LOCAL_MODE=false |
|---|---|---|
| `GET /test` | 200 + HTML | 404 |
| `GET /test-assets/*` | 200 + asset | 404 |
| `POST /dev/*` | 200 | 404 |
| `X-Local-User-Id` header | parsed + override | **silently ignored** |
| Clock override | `/dev/set-clock` mutates | always system clock |

In production mode the header is silently ignored so a public deployment
cannot impersonate users by sending it; `tests/test_deps_current_user.py`
covers this regression.

**Do not** expose the app on a non-loopback interface with `LOCAL_MODE=true`.
The dev surface (`/dev/push-quote`, `/dev/set-clock`, `/dev/evaluate-quotes`)
is unauthenticated by design and lets any caller fabricate quotes / freeze
time / trigger intents.

## Adding a new endpoint to the catalog

1. Add an entry to `src/app/static/test/endpoints.js` in the relevant group
   (or invent a new group).
2. Implemented endpoint: set `implemented: true`, optionally supply
   `examples` (object mapping label → request body).
3. Planned endpoint: set `implemented: false` and include `ticket:
   "BE-V1-..."`.
4. Path parameters use `{name}` syntax; the UI substitutes from a JSON
   object you paste into the *Path params* field.

## Files

- `src/app/static/test/index.html` — page markup.
- `src/app/static/test/app.js` — endpoint render, request/response, demos.
- `src/app/static/test/user-view.js` — 使用者視角 dashboard / 下單 sheet。
- `src/app/static/test/service-view.js` — 服務端推行情、時鐘、評估控制台。
- `src/app/static/test/flows.html` — 系統流程圖與列印版。
- `src/app/static/test/styles.css` — light theme, grid layout.
- `src/app/static/test/endpoints.js` — catalog of endpoints.
- `src/app/api/routes/test_page.py` — serves `index.html` and `flows.html`.
- `src/app/api/routes/dev.py` — `/dev/push-quote`, `/dev/set-clock`,
  `/dev/server-state` (the plumbing the page relies on).
