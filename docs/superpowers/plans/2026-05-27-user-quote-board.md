# User Quote Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用者模式 dashboard 上方加一個自選股報價看板，3 秒輪詢 `GET /quotes` 公開端點，watchlist 存 localStorage、per-user。

**Architecture:** 後端走既有 layered（route → service → existing `QuoteProvider` protocol）+ `app.state.quote_provider`；前端走既有的 vanilla ES module + `sendRequest` wrapper + AbortController + 模式 lifecycle。沒有新表、沒有 migration。

**Tech Stack:** FastAPI / Pydantic v2 / pytest + TestClient / ES modules + plain CSS / Playwright (UI smoke)。

**Spec:** `docs/superpowers/specs/2026-05-27-user-quote-board-design.md`（commit `9143f27`）。

**Spec → Plan 對齊調整：**

- Spec 寫 `INVALID_SYMBOL` 對應 400；專案既有 convention 是 `UNKNOWN_SYMBOL` 對應 404（透過 `UnknownSymbolError` → `app.api.errors`）。本 plan 用既有 convention，404 + `UNKNOWN_SYMBOL`，details `{"symbol": "..."}`。
- 新增兩個錯誤碼：`MISSING_SYMBOLS`、`TOO_MANY_SYMBOLS`，皆 400，details 帶上下文（限制值或 count）。

---

## File Structure

### 新檔

| Path | Responsibility |
|---|---|
| `src/app/schemas/quote.py` | `QuoteRead` / `QuoteListResponse` Pydantic schemas |
| `src/app/api/routes/quotes.py` | `GET /quotes` handler — parse `symbols`, validate count, lookup symbol metadata, query provider, assemble response |
| `src/app/services/quote_lookup.py` | `QuoteLookupService` — 把「validate + symbol meta + provider snapshot 組裝成 `QuoteRead`」這段邏輯放服務層，route 維持 thin |
| `src/app/static/test/quote-board.js` | 看板模組：watchlist localStorage、tile render、polling loop with AbortController、lifecycle hooks |
| `tests/api/test_quotes.py` | Backend unit tests for `GET /quotes`（happy / stale / missing / too-many / unknown） |

### 改檔

| Path | Reason |
|---|---|
| `src/app/api/errors.py` | 新增 `MISSING_SYMBOLS` / `TOO_MANY_SYMBOLS` enum values + default messages |
| `src/app/api/deps.py` | 新增 `QuoteLookupServiceDep` / `QuoteProviderDep` |
| `src/app/main.py` | mount `quotes_router` 在 `/quotes` |
| `src/app/static/test/index.html` | `.uv-section-grid` 上方新增 `<section id="uv-quote-board">` 容器 |
| `src/app/static/test/user-view.js` | 進入使用者模式時 `mount` 看板，離開時 `unmount` |
| `src/app/static/test/styles.css` | 新增 `.qb-*` 樣式（區塊、tile、add-tile、stale、direction arrow、moved-stuck error） |
| `src/app/static/test/endpoints.js` | 新增 `GET /quotes` 條目（Dev 模式可測試）|
| `tests/test_test_page.py` | 補 `/test-assets/quote-board.js` 在 LOCAL_MODE / non-LOCAL_MODE 的 status code 斷言；endpoints catalog vs `/openapi.json` parity 自然覆蓋 `GET /quotes` |
| `tests/ui/test_smoke.py` | 新增 `test_user_quote_board_polls_and_renders` |
| `docs/dev/test-page.md` | 「使用者模式」段補一句報價看板 |

---

## Task 1: 後端 — 錯誤碼擴充

**Files:**
- Modify: `src/app/api/errors.py`

- [ ] **Step 1: 加兩個 enum value + default message**

Open `src/app/api/errors.py`, locate `class ErrorCode(StrEnum):`. Add two new entries at the end of the enum:

```python
    MISSING_SYMBOLS = "MISSING_SYMBOLS"
    TOO_MANY_SYMBOLS = "TOO_MANY_SYMBOLS"
```

And add matching defaults in `DEFAULT_MESSAGES`:

```python
    ErrorCode.MISSING_SYMBOLS: "請帶上至少一個 symbol",
    ErrorCode.TOO_MANY_SYMBOLS: "symbols 數量超過上限",
```

- [ ] **Step 2: Run type check**

Run: `uv run mypy src tests`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add src/app/api/errors.py
git commit -m "feat(quotes): 新增 MISSING_SYMBOLS / TOO_MANY_SYMBOLS 錯誤碼"
```

---

## Task 2: 後端 — Pydantic schemas

**Files:**
- Create: `src/app/schemas/quote.py`

- [ ] **Step 1: 寫 schema 檔**

Create `src/app/schemas/quote.py`:

```python
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class QuoteRead(BaseModel):
    """Single symbol quote snapshot returned by GET /quotes."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    ask_price: Decimal | None = Field(default=None, serialization_alias="askPrice")
    bid_price: Decimal | None = Field(default=None, serialization_alias="bidPrice")
    last_price: Decimal | None = Field(default=None, serialization_alias="lastPrice")
    quote_time: datetime | None = Field(default=None, serialization_alias="quoteTime")
    stale: bool = False


class QuoteListResponse(BaseModel):
    data: list[QuoteRead]
```

`serialization_alias` per field is the existing convention used by `src/app/schemas/symbol.py`. Do **not** use `alias_generator` — keep style consistent.

When FastAPI serializes the response, `response_model=QuoteListResponse` needs `model_config` for the route to honour the aliases. The route registration in Task 5 uses `response_model=QuoteListResponse` which by default emits the alias on output (matches the existing SymbolResponse behaviour — no extra flag needed).

- [ ] **Step 2: Run type check**

Run: `uv run mypy src tests`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add src/app/schemas/quote.py
git commit -m "feat(quotes): 新增 QuoteRead / QuoteListResponse Pydantic schemas"
```

---

## Task 3: 後端 — QuoteLookupService

**Files:**
- Create: `src/app/services/quote_lookup.py`
- Modify: `src/app/api/deps.py`

- [ ] **Step 1: 寫 service 檔**

Create `src/app/services/quote_lookup.py`:

```python
"""Assembles `QuoteRead` records from the QuoteProvider snapshot cache.

Pure synchronous logic: validate the request shape, look up symbol metadata
through SymbolService (raises UnknownSymbolError for caller to map to 404),
then ask the QuoteProvider for snapshots. Missing snapshots become
`stale=True` rows instead of failing the whole batch — matches the spec's
batch read semantics.
"""

from app.api.errors import ApiError, ErrorCode
from app.schemas.quote import QuoteRead
from app.services.quote.base import QuoteProvider, QuoteUnavailableError
from app.services.symbol import SymbolService

MAX_SYMBOLS = 50


class QuoteLookupService:
    def __init__(self, symbols: SymbolService, provider: QuoteProvider) -> None:
        self._symbols = symbols
        self._provider = provider

    def lookup(self, raw_symbols: str | None) -> list[QuoteRead]:
        codes = self._parse(raw_symbols)
        # Validate each symbol exists in registry (raises UnknownSymbolError).
        meta = {code: self._symbols.get_by_symbol(code) for code in codes}
        snapshots = self._fetch_snapshots(codes)
        return [self._to_read(code, meta[code], snapshots.get(code)) for code in codes]

    @staticmethod
    def _parse(raw: str | None) -> list[str]:
        if raw is None or not raw.strip():
            raise ApiError(ErrorCode.MISSING_SYMBOLS, status_code=400)
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if not codes:
            raise ApiError(ErrorCode.MISSING_SYMBOLS, status_code=400)
        if len(codes) > MAX_SYMBOLS:
            raise ApiError(
                ErrorCode.TOO_MANY_SYMBOLS,
                status_code=400,
                details={"limit": MAX_SYMBOLS, "received": len(codes)},
            )
        return codes

    def _fetch_snapshots(self, codes: list[str]) -> dict[str, object]:
        # Provider raises QuoteUnavailableError on the first missing symbol; the
        # batch endpoint wants per-symbol stale flags, so query one at a time
        # and absorb that error into None.
        snapshots: dict[str, object] = {}
        for code in codes:
            try:
                [snap] = self._provider.get_quotes([code])
            except QuoteUnavailableError:
                snapshots[code] = None
            else:
                snapshots[code] = snap
        return snapshots

    @staticmethod
    def _to_read(code: str, meta, snapshot) -> QuoteRead:  # type: ignore[no-untyped-def]
        if snapshot is None:
            return QuoteRead(symbol=code, display_name=meta.display_name, stale=True)
        return QuoteRead(
            symbol=code,
            display_name=meta.display_name,
            ask_price=snapshot.ask_price,
            bid_price=snapshot.bid_price,
            last_price=snapshot.last_price,
            quote_time=snapshot.quote_time,
            stale=False,
        )
```

- [ ] **Step 2: 為 service 加 FastAPI dependency**

Open `src/app/api/deps.py`. Locate the existing `SymbolServiceDep` block (search `SymbolServiceDep`). Add at the bottom of the file:

```python
def get_quote_provider(request: Request) -> QuoteProvider:
    return request.app.state.quote_provider


QuoteProviderDep = Annotated[QuoteProvider, Depends(get_quote_provider)]


def get_quote_lookup_service(
    symbols: SymbolServiceDep,
    provider: QuoteProviderDep,
) -> QuoteLookupService:
    return QuoteLookupService(symbols, provider)


QuoteLookupServiceDep = Annotated[QuoteLookupService, Depends(get_quote_lookup_service)]
```

Add the imports at the top of `deps.py` (alongside existing imports):

```python
from app.services.quote.base import QuoteProvider
from app.services.quote_lookup import QuoteLookupService
```

(If `Request`, `Depends`, `Annotated` are not already imported, add them too.)

- [ ] **Step 3: Run type check**

Run: `uv run mypy src tests`
Expected: `Success: no issues found`

- [ ] **Step 4: Commit**

```bash
git add src/app/services/quote_lookup.py src/app/api/deps.py
git commit -m "feat(quotes): 新增 QuoteLookupService 與 DI 接線"
```

---

## Task 4: 後端 — 失敗的 endpoint 測試（TDD）

**Files:**
- Create: `tests/api/test_quotes.py`

- [ ] **Step 1: 寫測試（會全 fail，因為 endpoint 不存在）**

Create `tests/api/test_quotes.py`:

```python
"""Tests for GET /quotes batch read endpoint."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import status
from fastapi.testclient import TestClient

from app.services.quote.base import QuoteSnapshot
from tests.conftest import ClientFactory


def _push_quote(
    client: TestClient,
    symbol: str,
    ask: str = "599.00",
    bid: str = "598.50",
    last: str = "599.00",
) -> None:
    """Seed the InMemoryQuoteProvider directly via app.state."""
    provider = client.app.state.quote_provider
    provider.push_quote(
        QuoteSnapshot(
            symbol=symbol,
            ask_price=Decimal(ask),
            bid_price=Decimal(bid),
            last_price=Decimal(last),
            quote_time=datetime(2026, 5, 27, 1, 30, tzinfo=UTC),
            received_at=datetime(2026, 5, 27, 1, 30, tzinfo=UTC),
        )
    )


def test_get_quotes_single_symbol_with_snapshot(client_factory: ClientFactory) -> None:
    client = client_factory()
    _push_quote(client, "2330")
    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["symbol"] == "2330"
    assert row["askPrice"] == "599.00"
    assert row["bidPrice"] == "598.50"
    assert row["lastPrice"] == "599.00"
    assert row["stale"] is False
    assert row["displayName"]  # registry should have something


def test_get_quotes_multiple_symbols_preserve_order(client_factory: ClientFactory) -> None:
    client = client_factory()
    _push_quote(client, "2330", ask="599")
    _push_quote(client, "2317", ask="205.5")
    response = client.get("/quotes", params={"symbols": "2317,2330"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert [r["symbol"] for r in body["data"]] == ["2317", "2330"]


def test_get_quotes_unseeded_symbol_returns_stale(client_factory: ClientFactory) -> None:
    client = client_factory()
    # Do NOT push any quote — provider has nothing for 2330.
    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
    row = response.json()["data"][0]
    assert row["symbol"] == "2330"
    assert row["stale"] is True
    assert row["askPrice"] is None
    assert row["bidPrice"] is None
    assert row["lastPrice"] is None
    assert row["quoteTime"] is None


def test_get_quotes_missing_symbols_param_returns_400(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/quotes")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "MISSING_SYMBOLS"


def test_get_quotes_empty_symbols_param_returns_400(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/quotes", params={"symbols": " "})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "MISSING_SYMBOLS"


def test_get_quotes_too_many_symbols_returns_400(client_factory: ClientFactory) -> None:
    client = client_factory()
    too_many = ",".join(f"99{i:02d}" for i in range(51))
    response = client.get("/quotes", params={"symbols": too_many})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["error"]["code"] == "TOO_MANY_SYMBOLS"
    assert body["error"]["details"]["limit"] == 50
    assert body["error"]["details"]["received"] == 51


def test_get_quotes_unknown_symbol_returns_404(client_factory: ClientFactory) -> None:
    client = client_factory()
    response = client.get("/quotes", params={"symbols": "ZZZZ"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["error"]["code"] == "UNKNOWN_SYMBOL"
    assert body["error"]["details"]["symbol"] == "ZZZZ"


def test_get_quotes_works_in_production_mode(
    client_factory: ClientFactory, monkeypatch  # noqa: ANN001
) -> None:
    """Spec: /quotes is NOT gated behind LOCAL_MODE; should 200 even when off."""
    monkeypatch.setenv("LOCAL_MODE", "false")
    client = client_factory()
    _push_quote(client, "2330")
    response = client.get("/quotes", params={"symbols": "2330"})

    assert response.status_code == status.HTTP_200_OK
```

- [ ] **Step 2: 跑測試確認 fail**

Run: `uv run pytest tests/api/test_quotes.py -v`
Expected: 8 FAILED with 404 (route not registered)

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/api/test_quotes.py
git commit -m "test(quotes): 加 GET /quotes 端點測試（尚未實作，預期 fail）"
```

---

## Task 5: 後端 — Route handler 與 mount

**Files:**
- Create: `src/app/api/routes/quotes.py`
- Modify: `src/app/main.py`

- [ ] **Step 1: 寫 handler**

Create `src/app/api/routes/quotes.py`:

```python
"""GET /quotes — batch read of latest QuoteProvider snapshots.

Not gated behind LOCAL_MODE: V1 will expose this publicly so the gate would
just churn frontends. Symbol validation reuses SymbolService.get_by_symbol
(raises UnknownSymbolError → 404 via existing handler). Per-symbol snapshot
fetch absorbs QuoteUnavailableError into stale=True so a cold cache for one
symbol does not kill the batch.
"""

from fastapi import APIRouter, Query

from app.api.deps import QuoteLookupServiceDep
from app.schemas.quote import QuoteListResponse

router = APIRouter()


@router.get("", response_model=QuoteListResponse)
def list_quotes(
    service: QuoteLookupServiceDep,
    symbols: str | None = Query(
        None, description="逗號分隔的 symbol 代號，最多 50 個（例：2330,2317）"
    ),
) -> QuoteListResponse:
    return QuoteListResponse(data=service.lookup(symbols))
```

- [ ] **Step 2: Mount router in main.py**

Open `src/app/main.py`. Add import alongside existing route imports:

```python
from app.api.routes.quotes import router as quotes_router
```

In the `include_router` block (search `include_router(symbols_router`), add **right after the symbols line**:

```python
    app.include_router(quotes_router, prefix="/quotes", tags=["quotes"])
```

- [ ] **Step 3: 跑 backend tests，期望全 pass**

Run: `uv run pytest tests/api/test_quotes.py -v`
Expected: 8 PASSED

- [ ] **Step 4: 跑全套確認沒回歸**

Run: `make check`
Expected: lint / format / mypy / pytest 全綠

- [ ] **Step 5: Commit**

```bash
git add src/app/api/routes/quotes.py src/app/main.py
git commit -m "feat(quotes): 實作 GET /quotes 端點"
```

---

## Task 6: 後端 — 測試頁 catalog 與 asset parity

**Files:**
- Modify: `src/app/static/test/endpoints.js`
- Modify: `tests/test_test_page.py`
- Create (空檔): `src/app/static/test/quote-board.js`

- [ ] **Step 1: 加 endpoints.js 條目**

Open `src/app/static/test/endpoints.js`. Locate the symbols group (search `// ---- symbols`). Insert immediately after the `GET /symbols/{symbol}` entry:

```javascript
  // ---- quotes ----------------------------------------------------------
  {
    group: "quotes",
    method: "GET",
    path: "/quotes",
    implemented: true,
    label: "批次讀報價",
    description: "依逗號分隔的 symbol 列表（最多 50 個），回每檔最新行情。沒有 snapshot 的會回 stale: true。/test 使用者模式的報價看板就是用這個端點輪詢。",
    query: { symbols: "2330,2317" },
    queryParamSpecs: {
      symbols: {
        description: "逗號分隔的 symbol 代號（例：2330,2317）",
        example: "2330,2317",
      },
    },
  },
```

- [ ] **Step 2: 建立空的 quote-board.js（之後 task 補實作）**

Create `src/app/static/test/quote-board.js`:

```javascript
// User-mode quote board — populated in subsequent plan tasks.
// Filled by Task 7 onwards.

export function mountQuoteBoard() {
  // stub — real wiring lands in Task 10
}

export function unmountQuoteBoard() {
  // stub
}
```

- [ ] **Step 3: 更新 test_test_page.py 的資產測試**

Open `tests/test_test_page.py`. Locate `test_test_assets_served_in_local_mode` (search `def test_test_assets_served_in_local_mode`). Append a line that asserts the new asset:

```python
    assert client.get("/test-assets/quote-board.js").status_code == 200
```

Then locate `test_test_page_404_when_local_mode_disabled`. Append:

```python
    assert client.get("/test-assets/quote-board.js").status_code == 404
```

- [ ] **Step 4: 跑 test-page 測試**

Run: `uv run pytest tests/test_test_page.py -v`
Expected: all PASSED — including `test_endpoint_catalog_covers_current_openapi` (because the new endpoint is in both `endpoints.js` and `/openapi.json`).

- [ ] **Step 5: Commit**

```bash
git add src/app/static/test/endpoints.js src/app/static/test/quote-board.js tests/test_test_page.py
git commit -m "feat(quotes): 測試頁 catalog 加 GET /quotes + 預留 quote-board.js"
```

---

## Task 7: 前端 — HTML container 與基礎 CSS

**Files:**
- Modify: `src/app/static/test/index.html`
- Modify: `src/app/static/test/styles.css`

- [ ] **Step 1: 在 index.html 加看板容器**

Open `src/app/static/test/index.html`. Locate `<div class="uv-section-grid">` inside `#user-view`. Insert **immediately before** that div:

```html
        <section id="uv-quote-board" class="qb-board" hidden>
          <header class="qb-head">
            <h2 class="qb-title">報價看板</h2>
            <div class="qb-actions">
              <button type="button" id="qb-refresh-btn" class="icon-btn" title="立即刷新" aria-label="立即刷新">↻</button>
              <button type="button" id="qb-toggle-btn" class="qb-toggle" aria-expanded="true" aria-controls="qb-tiles">
                <span class="qb-toggle-label">隱藏</span>
                <span class="qb-toggle-icon" aria-hidden="true">▼</span>
              </button>
            </div>
          </header>
          <div class="qb-tiles" id="qb-tiles" role="list"></div>
        </section>
```

- [ ] **Step 2: 加 CSS（接在 `/* ---- Notification card ----` 之前）**

Open `src/app/static/test/styles.css`. Search for `/* ---- Notification card --` and insert **before** that comment block:

```css
/* ---- Quote board -------------------------------------------------------- */

.qb-board {
  background: var(--bg-elevated);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-card);
  padding: 16px 18px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
}

.qb-board[hidden] {
  display: none;
}

.qb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.qb-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.005em;
  color: var(--text);
}

.qb-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.qb-toggle {
  background: transparent;
  border: 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
}

.qb-toggle:hover {
  background: var(--separator);
}

.qb-toggle .qb-toggle-icon {
  font-size: 9px;
  transition: transform 0.2s ease;
}

.qb-toggle[aria-expanded="false"] .qb-toggle-icon {
  transform: rotate(-90deg);
}

.qb-board[data-collapsed="true"] .qb-tiles {
  display: none;
}

.qb-tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.qb-tile {
  position: relative;
  min-width: 132px;
  background: var(--bg-card, #f7f7fb);
  border: 1px solid var(--separator);
  border-radius: 10px;
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.qb-tile-code {
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0;
}

.qb-tile-name {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}

.qb-tile-price {
  margin-top: 4px;
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.qb-tile-change {
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.qb-tile-change.up { color: #d93025; }
.qb-tile-change.down { color: #137333; }
.qb-tile-change.flat { color: var(--text-tertiary); }

.qb-tile-remove {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 18px;
  height: 18px;
  border: 0;
  background: rgba(0, 0, 0, 0.06);
  color: var(--text-secondary);
  border-radius: 50%;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.qb-tile:hover .qb-tile-remove { opacity: 1; }
.qb-tile-remove:hover { background: rgba(0, 0, 0, 0.12); color: var(--text); }

.qb-tile.stale {
  opacity: 0.65;
}

.qb-tile.stale .qb-tile-price {
  color: var(--text-tertiary);
}

.qb-tile-foot {
  margin-top: 2px;
  font-size: 10px;
  color: var(--text-tertiary);
}

.qb-tile-foot.error {
  color: #d93025;
}

.qb-add-tile {
  min-width: 132px;
  border: 1px dashed var(--separator);
  background: transparent;
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: var(--accent);
  font-weight: 600;
  cursor: pointer;
  font-size: 13px;
}

.qb-add-tile:hover {
  background: rgba(0, 122, 255, 0.05);
}

.qb-empty-hint {
  width: 100%;
  font-size: 12px;
  color: var(--text-tertiary);
  text-align: center;
  padding: 8px 0 2px;
}
```

- [ ] **Step 3: 重新整理 /test，肉眼確認 `#uv-quote-board` 仍隱藏（之後 mount 才取消）**

Run: `curl -s http://127.0.0.1:8002/test-assets/styles.css | grep -c '.qb-board'`
Expected: `>= 1`

(若 dev server 不在 8002，調整成自己的 port。)

- [ ] **Step 4: Commit**

```bash
git add src/app/static/test/index.html src/app/static/test/styles.css
git commit -m "feat(quote-board): 加 HTML 容器與 CSS 樣式骨架"
```

---

## Task 8: 前端 — quote-board.js 核心 render

**Files:**
- Modify: `src/app/static/test/quote-board.js`

- [ ] **Step 1: 全面重寫 quote-board.js**

Replace the file contents:

```javascript
// User-mode quote board.
//
// Responsibility split:
//   - watchlist storage: per-user list of symbol codes in localStorage
//   - render: tiles + add-tile + empty hint
//   - polling: 3s loop, AbortController, visibility-aware (Task 9)
//   - lifecycle: mount/unmount called by user-view.js (Task 10)

import { sendRequest, getSelectedUser } from "./app.js";

const POLL_MS = 3000;
const WATCHLIST_KEY = "ai-stock-user-watchlist";
const COLLAPSED_KEY = "ai-stock-user-quote-board-collapsed";

let pollTimer = null;
let inflightController = null;
let baselinePriceByCode = {};
let consecutiveErrors = 0;
let cooldownUntilMs = 0;
let onAddSymbolCallback = null;

// ---------------------------------------------------------------------------
// Watchlist storage
// ---------------------------------------------------------------------------

function readWatchlistMap() {
  try {
    return JSON.parse(localStorage.getItem(WATCHLIST_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeWatchlistMap(map) {
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(map));
}

function currentWatchlist() {
  const map = readWatchlistMap();
  const user = getSelectedUser();
  const list = map[user];
  return Array.isArray(list) ? list.slice() : [];
}

function persistWatchlist(list) {
  const map = readWatchlistMap();
  map[getSelectedUser()] = list;
  writeWatchlistMap(map);
}

export function addToWatchlist(code) {
  const list = currentWatchlist();
  if (list.includes(code)) return;
  list.push(code);
  persistWatchlist(list);
  baselinePriceByCode[code] = null; // reset baseline for new entry
  renderTiles({ optimistic: true });
}

export function removeFromWatchlist(code) {
  const list = currentWatchlist().filter((c) => c !== code);
  persistWatchlist(list);
  delete baselinePriceByCode[code];
  renderTiles();
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function tileHtml(row) {
  const main = row.lastPrice ?? row.askPrice ?? row.bidPrice;
  const baseline = baselinePriceByCode[row.symbol];
  let changeClass = "flat";
  let changeText = "─";
  if (main != null && baseline != null && baseline !== "0") {
    const cur = Number(main);
    const base = Number(baseline);
    const pct = ((cur - base) / base) * 100;
    if (Math.abs(pct) < 0.01) {
      changeClass = "flat";
      changeText = "─";
    } else if (pct > 0) {
      changeClass = "up";
      changeText = `↑${pct.toFixed(2)}%`;
    } else {
      changeClass = "down";
      changeText = `↓${Math.abs(pct).toFixed(2)}%`;
    }
  }
  if (main != null && baseline == null) {
    baselinePriceByCode[row.symbol] = String(main);
  }
  const priceDisplay = main != null ? Number(main).toFixed(2) : "—";
  const stale = row.stale ? " stale" : "";
  return `
    <article class="qb-tile${stale}" data-symbol="${escapeAttr(row.symbol)}" role="listitem">
      <button class="qb-tile-remove" type="button" data-action="remove" aria-label="移除 ${escapeAttr(row.symbol)}">×</button>
      <span class="qb-tile-code">${escapeText(row.symbol)}</span>
      <span class="qb-tile-name">${escapeText(row.displayName || "")}</span>
      <span class="qb-tile-price">${priceDisplay}</span>
      <span class="qb-tile-change ${changeClass}">${changeText}</span>
      ${row.stale ? `<span class="qb-tile-foot">等待行情</span>` : ""}
    </article>
  `;
}

function addTileHtml() {
  return `<button type="button" class="qb-add-tile" id="qb-add-btn">＋ 加入自選</button>`;
}

function emptyHintHtml() {
  return `<div class="qb-empty-hint">加入想關注的股票，看價格自動更新</div>`;
}

function renderTiles({ rows, optimistic } = {}) {
  const container = document.getElementById("qb-tiles");
  if (!container) return;
  const watchlist = currentWatchlist();
  if (rows == null) {
    // No fresh API data — render placeholders so UI still shows.
    rows = watchlist.map((code) => ({ symbol: code, stale: true, displayName: "" }));
  }
  // Preserve order strictly to match watchlist; ignore extras from API.
  const byCode = Object.fromEntries(rows.map((r) => [r.symbol, r]));
  const orderedRows = watchlist.map((code) => byCode[code] || { symbol: code, stale: true, displayName: "" });
  const tilesHtml = orderedRows.map(tileHtml).join("");
  const emptyState = watchlist.length === 0 ? emptyHintHtml() : "";
  container.innerHTML = emptyState + tilesHtml + addTileHtml();
  bindTileEvents(container);
  if (optimistic) {
    // Caller just added a symbol — kick polling immediately so the new tile gets data.
    void pollNow();
  }
}

function bindTileEvents(container) {
  for (const btn of container.querySelectorAll(".qb-tile-remove")) {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const code = ev.currentTarget.closest(".qb-tile").dataset.symbol;
      removeFromWatchlist(code);
    });
  }
  const addBtn = container.querySelector("#qb-add-btn");
  if (addBtn && onAddSymbolCallback) {
    addBtn.addEventListener("click", () => onAddSymbolCallback());
  }
}

// ---------------------------------------------------------------------------
// Escape helpers (inline; small footprint)
// ---------------------------------------------------------------------------

function escapeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeText(s).replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Polling — implemented in Task 9
// ---------------------------------------------------------------------------

export async function pollNow() {
  // Stub; filled in Task 9.
}

// ---------------------------------------------------------------------------
// Public lifecycle — wired by user-view.js in Task 10
// ---------------------------------------------------------------------------

export function setAddSymbolHandler(fn) {
  onAddSymbolCallback = fn;
}

export function mountQuoteBoard() {
  const board = document.getElementById("uv-quote-board");
  if (!board) return;
  board.hidden = false;
  const collapsed = localStorage.getItem(COLLAPSED_KEY) === "true";
  board.dataset.collapsed = collapsed ? "true" : "false";
  const toggleBtn = document.getElementById("qb-toggle-btn");
  if (toggleBtn) {
    toggleBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggleBtn.querySelector(".qb-toggle-label").textContent = collapsed ? "顯示" : "隱藏";
  }
  renderTiles();
}

export function unmountQuoteBoard() {
  const board = document.getElementById("uv-quote-board");
  if (board) board.hidden = true;
  if (inflightController) {
    inflightController.abort();
    inflightController = null;
  }
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  baselinePriceByCode = {};
  consecutiveErrors = 0;
  cooldownUntilMs = 0;
}
```

- [ ] **Step 2: Smoke test in browser**

Reload `/test`, switch to 使用者 mode. The quote board container should appear but be empty (you haven't wired user-view to call `mountQuoteBoard()` yet — that's Task 10). For now, manually verify via console:

```js
const m = await import('/test-assets/quote-board.js');
m.mountQuoteBoard();
```

Expected: 看板出現、含「加入想關注的股票，看價格自動更新」+ 一張 add-tile。

- [ ] **Step 3: Commit**

```bash
git add src/app/static/test/quote-board.js
git commit -m "feat(quote-board): watchlist localStorage + tile/add-tile render"
```

---

## Task 9: 前端 — Polling loop + AbortController + error handling

**Files:**
- Modify: `src/app/static/test/quote-board.js`

- [ ] **Step 1: 替換 polling 段（找 `// Polling — implemented in Task 9` 那一節到 `setAddSymbolHandler` 之前）**

Replace the body of `pollNow()` and add helper functions:

```javascript
// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

export async function pollNow() {
  const board = document.getElementById("uv-quote-board");
  if (!board || board.hidden) return;
  if (board.dataset.collapsed === "true") return;
  if (document.visibilityState === "hidden") return;
  const now = Date.now();
  if (now < cooldownUntilMs) return;
  const list = currentWatchlist();
  if (list.length === 0) {
    renderTiles({ rows: [] });
    scheduleNextPoll();
    return;
  }

  if (inflightController) inflightController.abort();
  inflightController = new AbortController();
  const signal = inflightController.signal;

  let resp;
  try {
    resp = await sendRequest({
      method: "GET",
      path: "/quotes",
      query: { symbols: list.join(",") },
      signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") return;
    onPollError(err);
    scheduleNextPoll();
    return;
  }

  if (resp.status === 200 && resp.body?.data) {
    consecutiveErrors = 0;
    cooldownUntilMs = 0;
    renderTiles({ rows: resp.body.data });
  } else if (resp.status === 404 && resp.body?.error?.code === "UNKNOWN_SYMBOL") {
    // Spec §5.4: auto-remove the offending symbol.
    const bad = resp.body.error?.details?.symbol;
    if (bad) {
      console.warn(`[quote-board] dropping unknown symbol ${bad} from watchlist`);
      removeFromWatchlist(bad);
    }
  } else {
    onPollError(new Error(`HTTP ${resp.status}`));
  }
  scheduleNextPoll();
}

function onPollError(err) {
  consecutiveErrors += 1;
  console.warn("[quote-board] poll failed:", err?.message || err);
  if (consecutiveErrors >= 3) {
    cooldownUntilMs = Date.now() + 30_000;
    consecutiveErrors = 0;
    console.warn("[quote-board] backing off 30s after repeated failures");
  }
  // Mark all currently-rendered tiles with error foot.
  const container = document.getElementById("qb-tiles");
  if (!container) return;
  for (const tile of container.querySelectorAll(".qb-tile")) {
    if (!tile.querySelector(".qb-tile-foot.error")) {
      const foot = document.createElement("span");
      foot.className = "qb-tile-foot error";
      foot.textContent = "⚠ 連線中斷";
      tile.appendChild(foot);
    }
  }
}

function scheduleNextPoll() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => void pollNow(), POLL_MS);
}
```

- [ ] **Step 2: 把 polling 串到 mount/unmount**

Modify `mountQuoteBoard()` — at the very end of that function, after `renderTiles();`, add:

```javascript
  void pollNow();
```

Modify `unmountQuoteBoard()` — already clears the timer, no change needed.

- [ ] **Step 3: 手動 smoke**

Browser console:
```js
const m = await import('/test-assets/quote-board.js');
m.addToWatchlist('2330');
m.mountQuoteBoard();
```

In another tab, push a quote via `/dev/push-quote`. Within 3 seconds, the tile should show the price and `─` direction (first sample sets baseline). Push a higher price → next render should show `↑x.xx%` in red.

- [ ] **Step 4: Commit**

```bash
git add src/app/static/test/quote-board.js
git commit -m "feat(quote-board): 3 秒輪詢 + AbortController + 錯誤退避"
```

---

## Task 10: 前端 — 串接 user-view lifecycle / collapse / refresh

**Files:**
- Modify: `src/app/static/test/user-view.js`
- Modify: `src/app/static/test/quote-board.js`

- [ ] **Step 1: 在 quote-board.js 補 toggle/refresh wiring**

Open `src/app/static/test/quote-board.js`, locate the bottom (after `unmountQuoteBoard`). Append a helper that installs DOM event handlers, and call it from `mountQuoteBoard`:

```javascript
function installBoardControls() {
  const toggleBtn = document.getElementById("qb-toggle-btn");
  const refreshBtn = document.getElementById("qb-refresh-btn");
  const board = document.getElementById("uv-quote-board");
  if (toggleBtn && !toggleBtn.dataset.bound) {
    toggleBtn.dataset.bound = "true";
    toggleBtn.addEventListener("click", () => {
      const wasCollapsed = board.dataset.collapsed === "true";
      const next = !wasCollapsed;
      board.dataset.collapsed = next ? "true" : "false";
      localStorage.setItem(COLLAPSED_KEY, next ? "true" : "false");
      toggleBtn.setAttribute("aria-expanded", next ? "false" : "true");
      toggleBtn.querySelector(".qb-toggle-label").textContent = next ? "顯示" : "隱藏";
      if (next) {
        if (pollTimer) clearTimeout(pollTimer);
        if (inflightController) inflightController.abort();
      } else {
        void pollNow();
      }
    });
  }
  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "true";
    refreshBtn.addEventListener("click", () => void pollNow());
  }
}
```

Then in `mountQuoteBoard()`, **before** the final `void pollNow();`, call:

```javascript
  installBoardControls();
```

Also at the bottom of the module, install a one-time `visibilitychange` listener:

```javascript
let visibilityBound = false;
function bindVisibility() {
  if (visibilityBound) return;
  visibilityBound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void pollNow();
    }
  });
}
```

Call `bindVisibility()` from `mountQuoteBoard()` after `installBoardControls();`.

- [ ] **Step 2: user-view.js 串 lifecycle**

Open `src/app/static/test/user-view.js`. Add at the top alongside existing imports:

```javascript
import { mountQuoteBoard, unmountQuoteBoard, addToWatchlist, setAddSymbolHandler } from "./quote-board.js";
```

Find the existing function that runs when entering user mode (search for `refreshIntents` + `refreshNotifications` calls together — they sit in an exported `onEnterUserMode` or similar). Add at the end of that block:

```javascript
  setAddSymbolHandler(openSymbolPickerForWatchlist);
  mountQuoteBoard();
```

Find the matching exit function (e.g. `onLeaveUserMode`). Add at the start:

```javascript
  unmountQuoteBoard();
```

(If those entry/exit hooks don't exist as exports, scan `app.js` for how user-view is mounted/unmounted — likely `service-view.js` shows the pattern with `onEnterServiceMode`/`onLeaveServiceMode`. Mirror that.)

- [ ] **Step 3: 寫 openSymbolPickerForWatchlist（簡單版：reuse existing search sheet 或 prompt fallback）**

In `user-view.js`, add a small function near other dialog helpers:

```javascript
function openSymbolPickerForWatchlist() {
  // Minimal: reuse the symbol search component already used by the order sheet.
  // Easiest first cut — prompt() the user for a code and validate via /symbols.
  // A later polish task can swap this for a proper search modal.
  const code = window.prompt("加入自選 — 輸入股票代號（例如 2330）");
  if (!code) return;
  const trimmed = code.trim();
  if (!trimmed) return;
  sendRequest({ method: "GET", path: `/symbols/${encodeURIComponent(trimmed)}` })
    .then((resp) => {
      if (resp.status === 200) {
        addToWatchlist(trimmed);
      } else {
        alert(resp.body?.error?.message || `找不到 ${trimmed}`);
      }
    })
    .catch(() => alert("加入失敗，請稍候再試"));
}
```

(`sendRequest` is already imported from `app.js` at the top of `user-view.js`. Confirm by `grep -nE "sendRequest" src/app/static/test/user-view.js` before assuming.)

- [ ] **Step 4: 重新整理 /test，切到使用者模式，肉眼確認**

- 看板出現在 dashboard 上方
- 「加入自選」按鈕 prompt 一個 code（例如 `2330`），加進去後出現一張 tile
- push 一筆 quote 後 3 秒內 tile 顯示價格
- 按「隱藏」摺起來、再按「顯示」展開、刷新立即重打一次

- [ ] **Step 5: Commit**

```bash
git add src/app/static/test/quote-board.js src/app/static/test/user-view.js
git commit -m "feat(quote-board): 串 user-view lifecycle / collapse / refresh / 加入自選"
```

---

## Task 11: 前端 — 切換使用者要 reset baseline

**Files:**
- Modify: `src/app/static/test/app.js`
- Modify: `src/app/static/test/quote-board.js`

**Why:** 切 alice→bob 時 watchlist 整批換，baseline 應該歸零，舊的 inflight 請求要中止。

- [ ] **Step 1: app.js 在 setSelectedUser 中發 event**

Open `src/app/static/test/app.js`, locate `export function setSelectedUser(label)`. Replace it with:

```javascript
export function setSelectedUser(label) {
  const prev = localStorage.getItem(SELECTED_USER_KEY);
  localStorage.setItem(SELECTED_USER_KEY, label);
  if (prev !== label) {
    window.dispatchEvent(new CustomEvent("ai-stock:user-changed", { detail: { from: prev, to: label } }));
  }
}
```

- [ ] **Step 2: quote-board.js 聽事件**

In `quote-board.js`, in `mountQuoteBoard()` after `bindVisibility();` add:

```javascript
  bindUserChange();
```

Add the helper at the bottom of the module:

```javascript
let userChangeBound = false;
function bindUserChange() {
  if (userChangeBound) return;
  userChangeBound = true;
  window.addEventListener("ai-stock:user-changed", () => {
    if (inflightController) inflightController.abort();
    baselinePriceByCode = {};
    renderTiles();
    void pollNow();
  });
}
```

- [ ] **Step 3: 手動 smoke**

切 alice→bob：bob 自己的 watchlist 立刻顯示、alice 的價格 baseline 不會殘留。

- [ ] **Step 4: Commit**

```bash
git add src/app/static/test/app.js src/app/static/test/quote-board.js
git commit -m "feat(quote-board): 切換使用者時重設 baseline 並重啟 polling"
```

---

## Task 12: Playwright UI smoke

**Files:**
- Modify: `tests/ui/test_smoke.py`

- [ ] **Step 1: 寫測試**

Open `tests/ui/test_smoke.py`. Append at the end of the file:

```python
@pytest.mark.ui
def test_user_quote_board_polls_and_renders(
    page: Page,
    live_server_url: str,
) -> None:
    """進使用者模式後，預塞的 watchlist symbol 應該在 5 秒內出現非 "—" 的價格。"""
    import httpx

    # Seed a quote so /quotes returns real data.
    httpx.post(
        f"{live_server_url}/dev/push-quote",
        json={"symbol": "2330", "askPrice": "599.00", "bidPrice": "598.50", "lastPrice": "599.00"},
        timeout=5.0,
    )

    page.goto(f"{live_server_url}/test")
    # Pre-seed alice's watchlist directly via localStorage
    page.evaluate(
        """() => {
            const users = JSON.parse(localStorage.getItem("ai-stock-test-users") || "{}");
            users.alice = "11111111-1111-1111-1111-111111111111";
            localStorage.setItem("ai-stock-test-users", JSON.stringify(users));
            localStorage.setItem("ai-stock-test-selected-user", "alice");
            localStorage.setItem(
                "ai-stock-user-watchlist",
                JSON.stringify({ alice: ["2330"] })
            );
        }"""
    )
    page.reload()
    page.click('.mode-toggle button[data-mode="user"]')

    tile = page.locator('.qb-tile[data-symbol="2330"]')
    expect(tile).to_be_visible(timeout=10_000)
    expect(tile).not_to_have_class(re.compile(r"\bstale\b"), timeout=8_000)
    price = tile.locator(".qb-tile-price")
    expect(price).not_to_have_text("—", timeout=8_000)
```

(`re` and `expect` are already imported at the top of the file from the prior `test_service_quote_side_chips_use_animated_indicator` work.)

- [ ] **Step 2: 跑 UI smoke**

Run: `make test-ui`
Expected: all tests pass, including the new one.

- [ ] **Step 3: Commit**

```bash
git add tests/ui/test_smoke.py
git commit -m "test(ui): 加報價看板 polling 渲染 smoke 測試"
```

---

## Task 13: 文件同步

**Files:**
- Modify: `docs/dev/test-page.md`

- [ ] **Step 1: 更新「使用者模式」段**

Open `docs/dev/test-page.md`. Locate the `### 使用者模式` heading. After the existing `**Dashboard**:` bullet, insert a new bullet **above** it (so 報價看板 is mentioned first):

```markdown
- **報價看板**：dashboard 上方橫跨兩欄的自選股看板，3 秒輪詢 `GET /quotes`；
  watchlist 存 localStorage（per-user）、可摺疊；切換使用者會立即套用該使用者的
  watchlist 並重設漲跌幅基準。
```

- [ ] **Step 2: 跑 backend test 確認 docs sample 沒影響 test 套件**

Run: `make check`
Expected: 全綠

- [ ] **Step 3: Commit**

```bash
git add docs/dev/test-page.md
git commit -m "docs(test-page): 使用者模式段補報價看板說明"
```

---

## Task 14: Final 驗收

- [ ] **Step 1: 全套品質門檻**

Run: `make check`
Expected: ruff / format / mypy / pytest 全綠

- [ ] **Step 2: UI smoke**

Run: `make test-ui`
Expected: 全綠（含新加的 `test_user_quote_board_polls_and_renders`）

- [ ] **Step 3: 手動端到端**

1. 開瀏覽器到 `/test`
2. 進使用者模式 → 看板出現、empty hint + add-tile
3. 點「加入自選」、輸入 `2330` → tile 出現、stale 狀態
4. 切「服務端」mode → push ask=599 for 2330
5. 切回使用者模式 → 3 秒內 tile 顯示價格 + 基準
6. 服務端再 push 600 → tile 顯示 `↑0.17%`（紅）
7. 服務端再 push 598 → tile 顯示 `↓0.17%`（綠）
8. 切 alice→bob → bob 的 watchlist（空或不同）立刻生效
9. hover tile → `×` 出現、點下後 tile 消失
10. 按「隱藏」→ 看板收起、polling 停止（DevTools Network 確認）
11. 切回 Dev / 服務端模式 → unmount，polling 停止

- [ ] **Step 4: Push 到 origin**

```bash
git push
```

---

## Self-Review

**Spec coverage**：

| Spec 段 | Plan task |
|---|---|
| §3.1 端點 `GET /quotes` | Task 5 |
| §3.2 回應 schema | Task 2 |
| §3.3 錯誤 (MISSING/TOO_MANY/UNKNOWN) | Task 1 + Task 3 + Task 5（測試 Task 4） |
| §3.4 實作要點（reuse provider、symbol service） | Task 3 |
| §4.1 版面 | Task 7 (HTML) |
| §4.2 quote-tile | Task 8 (render) + Task 7 (CSS) |
| §4.3 add-tile + 搜尋 | Task 8 (add-tile) + Task 10 (picker) |
| §4.4 摺疊 | Task 10 |
| §4.5 localStorage schema | Task 8 (watchlist) |
| §4.6 空狀態 | Task 8 (emptyHintHtml) |
| §5.1 3 秒輪詢 | Task 9 |
| §5.2 觸發 / 停止（mode/visibility/user） | Task 9（visibility 在 Task 10）+ Task 10 + Task 11 |
| §5.3 AbortController | Task 9 |
| §5.4 錯誤處理（5xx 退避、UNKNOWN_SYMBOL 自動 drop） | Task 9 |
| §5.5 非阻塞初始化 | Task 10（mount 在 dashboard 已 render 之後） |
| §6 檔案規畫 | 全 plan |
| §7.1 backend tests（8 cases） | Task 4 |
| §7.2 test-page parity | Task 6 |
| §7.3 Playwright smoke | Task 12 |

**Placeholder scan**：每一個 step 都有完整 code 或具體指令；沒有 TBD / TODO / "implement later"。

**Type consistency**：
- Backend：`QuoteRead` 用 `Field(serialization_alias="displayName")` per-field，對齊 `src/app/schemas/symbol.py` 既有 convention（不用 `alias_generator`）✓
- Frontend：`addToWatchlist` / `removeFromWatchlist` / `setAddSymbolHandler` / `mountQuoteBoard` / `unmountQuoteBoard` / `pollNow` 在 Task 8/9/10/11 都用同一組名字 ✓
- `WATCHLIST_KEY` `"ai-stock-user-watchlist"` 與 spec §4.5 一致 ✓
- `COLLAPSED_KEY` `"ai-stock-user-quote-board-collapsed"` 與 spec §4.4 一致 ✓
- `POLL_MS = 3000` 與 spec §5.1 一致 ✓
- `MAX_SYMBOLS = 50` 與 spec §3.3 一致；details 字段 `{"limit": 50, "received": N}` 與 test (`tests/api/test_quotes.py::test_get_quotes_too_many_symbols_returns_400`) 完全對齊 ✓
