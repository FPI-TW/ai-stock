# Dev Console Implementation Plan

> **2026-05-19 更新**：dev console 前端已抽離成獨立專案 [`ai-stock-dev-console`](../../../../../ai-stock-dev-console)；
> 本 plan 的 Task 4~10（前端 HTML / CSS / JS / Alpine.js vendor / 跨 section 跳轉等）已由那個 repo 接手。
> 本 plan 的 Task 1~3（service `get_snapshot`、`GET /dev/quotes/{symbol}`、`QUOTE_NOT_FOUND`）仍是 ai-stock 端的工作，
> 並追加：移除 dev_console route + StaticFiles + 對應測試，於 LOCAL_MODE 加 `CORSMiddleware`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 LOCAL_MODE 下提供 `/dev/console` 視覺化測試介面，加上後端配套 `GET /dev/quotes/{symbol}` 讓開發者能「看見」in-memory quote store。

**Architecture:** 單檔 HTML + 手寫 CSS + vendored Alpine.js（無 build step），由 FastAPI 在 `LOCAL_MODE=true` 時透過 `dev_console` router 與 `StaticFiles` 掛載，與既有 `POST /dev/quotes` 共用同一條 gating。後端新增的 GET endpoint 透過 `DevQuoteIngestService.get_snapshot()` 讀同一個 `InMemoryQuoteStore`。

**Tech Stack:** Python 3.13 + FastAPI + Pydantic v2（後端）；單檔 HTML + 手寫 CSS + Alpine.js 3.x vendored（前端）；pytest + TestClient（測試）

**Reference Spec:** `docs/superpowers/specs/2026-05-19-dev-console-design.md`

---

## File Structure

| File | Action | 責任 |
|---|---|---|
| `src/app/api/errors.py` | Modify | 加入 `QUOTE_NOT_FOUND` enum value |
| `src/app/services/quote.py`（或 BE-V0.5-08 同名檔案） | Modify | `DevQuoteIngestService` 增加 `get_snapshot()` 方法 |
| `src/app/schemas/dev_quote.py` | Modify | 加入 `DevQuoteFetchResponseData` / `DevQuoteFetchResponse` |
| `src/app/api/routes/dev_quotes.py` | Modify | 加入 `GET /quotes/{symbol}` handler |
| `src/app/api/routes/dev_console.py` | Create | `GET /console` 回 `FileResponse` |
| `src/app/main.py` | Modify | 在 `if settings.local_mode:` 區塊掛 console router 與 `StaticFiles` |
| `src/app/static/dev_console.html` | Create | 主頁面 |
| `src/app/static/dev_console.css` | Create | 樣式（~150 行） |
| `src/app/static/dev_console.js` | Create | Alpine.js components、fetch wrapper、log 邏輯 |
| `src/app/static/vendor/alpine.min.js` | Create | Vendored Alpine.js 3.14.1 |
| `.gitattributes` | Create or Modify | 標 `linguist-vendored` 排除 vendor |
| `tests/unit/test_dev_quote_service.py` | Create | 服務層 `get_snapshot()` 單元測試 |
| `tests/api/test_dev_quote_api.py` | Modify | 加入 `GET /dev/quotes/{symbol}` 的 API 測試 |
| `tests/api/test_dev_console.py` | Create | `GET /dev/console` 與 static mount 的 LOCAL_MODE gating 測試 |
| `tests/api/conftest.py` | Modify | 若需要新增 `mock_dev_quote_service` fixture |

> **Preconditions（必讀）**
>
> 1. BE-V0.5-08（PR #7）必須已 merge 進 `main`，本 plan 假設下列檔案存在：
>    - `src/app/api/routes/dev_quotes.py`（含 `POST /quotes`）
>    - `src/app/schemas/dev_quote.py`（含 `DevQuoteUpsertRequest/Response`）
>    - `src/app/services/quote.py`（或 BE-V0.5-08 採用的同名 service 檔），內含 `DevQuoteIngestService` 與 `InMemoryQuoteStore`
>    - `src/app/api/deps.py` 內含 `DevQuoteIngestServiceDep`
>    - 對應 test fixtures（在 `tests/api/conftest.py` 或 `tests/api/test_dev_quote_api.py`）
> 2. 實作分支應 base 在 BE-V0.5-08 merge 後的 `origin/main`。
> 3. **Task 0** 必做：驗證上述檔案的實際路徑與類別名稱，若有差異請對應調整後續 task 的 import 路徑（不需要改 plan 的結構）。

---

## Task 0: Preconditions Check & Branch Setup

**Files:** （只讀）

- [ ] **Step 1: 確認 BE-V0.5-08 已 merge**

Run: `git fetch origin && git log origin/main --oneline | head -10 | grep -i "dev.*quote\|BE-V0.5-08" || echo "NOT MERGED"`

Expected: 看到 BE-V0.5-08 相關 commit。若顯示 "NOT MERGED" → 停止實作，等 PR #7 merge。

- [ ] **Step 2: 同步分支**

Run: `git checkout main && git pull origin main && git checkout -b feat/be-v0-5-dev-console`

Expected: 已在 `feat/be-v0-5-dev-console` 分支，HEAD 是最新 main。

> 若 spec 與 plan 已寫在另一條 worktree 分支，這裡需要把 spec/plan 兩支檔案 cherry-pick 過來：
> Run: `git log --all --oneline -- docs/superpowers/specs/2026-05-19-dev-console-design.md docs/superpowers/plans/2026-05-19-dev-console.md`
> 找到對應 commit 後 `git cherry-pick <hash>`。

- [ ] **Step 3: 驗證假設的檔案存在**

Run:
```bash
test -f src/app/api/routes/dev_quotes.py && echo "OK routes"
test -f src/app/schemas/dev_quote.py && echo "OK schemas"
grep -rn "class DevQuoteIngestService" src/app/ && echo "OK service"
grep -rn "class InMemoryQuoteStore" src/app/ && echo "OK store"
grep -rn "DevQuoteIngestServiceDep" src/app/api/deps.py && echo "OK deps"
ls tests/api/test_dev_quote_api.py && echo "OK tests"
```

Expected: 全部印出 `OK …`。任一缺失 → 找對應檔案，記下實際路徑，後續 task 的 import 路徑要對應調整。

- [ ] **Step 4: 跑現有測試確認基準線乾淨**

Run: `uv run pytest tests/api/test_dev_quote_api.py -v`

Expected: 全部 PASS。若失敗 → 停止，先處理。

---

## Task 1: 在 service 層加入 `get_snapshot()`

**Files:**
- Modify: `src/app/services/quote.py`（或 Task 0 找到的實際路徑）
- Create: `tests/unit/test_dev_quote_service.py`

- [ ] **Step 1: 寫失敗測試 — 空 store 回 None**

Create `tests/unit/test_dev_quote_service.py`:

```python
"""DevQuoteIngestService.get_snapshot() 單元測試。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.services.quote import DevQuoteIngestService, InMemoryQuoteStore


@pytest.fixture
def service() -> DevQuoteIngestService:
    return DevQuoteIngestService(store=InMemoryQuoteStore())


def test_get_snapshot_returns_none_when_symbol_not_seen(service: DevQuoteIngestService) -> None:
    assert service.get_snapshot("2330") is None


def test_get_snapshot_returns_latest_after_ingest(service: DevQuoteIngestService) -> None:
    quote_time = datetime(2026, 5, 19, 2, 14, 33, tzinfo=UTC)
    service.ingest(
        symbol="2330",
        bid_price=Decimal("590.0"),
        ask_price=Decimal("591.0"),
        last_price=Decimal("590.5"),
        quote_time=quote_time,
    )

    snapshot = service.get_snapshot("2330")

    assert snapshot is not None
    assert snapshot.symbol == "2330"
    assert snapshot.bid_price == Decimal("590.0")
    assert snapshot.ask_price == Decimal("591.0")
    assert snapshot.last_price == Decimal("590.5")
    assert snapshot.quote_time == quote_time


def test_get_snapshot_isolated_per_symbol(service: DevQuoteIngestService) -> None:
    quote_time = datetime(2026, 5, 19, 2, 14, 33, tzinfo=UTC)
    service.ingest(
        symbol="2330",
        bid_price=Decimal("590"),
        ask_price=Decimal("591"),
        last_price=Decimal("590.5"),
        quote_time=quote_time,
    )

    assert service.get_snapshot("2454") is None
    assert service.get_snapshot("2330") is not None
```

> **注意**：`DevQuoteIngestService` 的建構與 `InMemoryQuoteStore` 的 import 路徑要對應 Task 0 步驟 3 找到的實際路徑；若 BE-V0.5-08 用了不同檔名（如 `app.services.quote_ingest`），請對應修改。`QuoteSnapshot` 的欄位名稱也以實際定義為準（如果是 `bidPrice` camelCase 才需調整斷言）。

- [ ] **Step 2: 跑測試確認 fail**

Run: `uv run pytest tests/unit/test_dev_quote_service.py -v`

Expected: 兩個 `get_snapshot` 相關測試 FAIL（`AttributeError: ... has no attribute 'get_snapshot'`）。

- [ ] **Step 3: 在 service 加 method**

在 `DevQuoteIngestService` 類別內加入（具體寫法依該檔既有 style 為準）：

```python
def get_snapshot(self, symbol: str) -> QuoteSnapshot | None:
    """讀取目前 in-memory store 的 snapshot，無資料回 None。"""
    return self._store.get(symbol)
```

> 已驗證：`InMemoryQuoteStore.get(symbol)` 在 BE-V0.5-08 已存在（`src/app/services/quote.py:53`），可直接呼叫。`DevQuoteIngestService` 內部欄位是 `self._store`，所以實作就是 `return self._store.get(symbol)`。

- [ ] **Step 4: 跑測試確認 pass**

Run: `uv run pytest tests/unit/test_dev_quote_service.py -v`

Expected: 三個測試全 PASS。

- [ ] **Step 5: 跑 quality gate**

Run: `make check`

Expected: lint / format / typecheck / 全部 test 通過。

- [ ] **Step 6: Commit**

```bash
git add src/app/services/quote.py tests/unit/test_dev_quote_service.py
git commit -m "$(cat <<'EOF'
feat(quote): add get_snapshot to DevQuoteIngestService

提供讀取 in-memory store 的方法，供後續 GET /dev/quotes/{symbol}
endpoint 使用。Store 端對應加 get(symbol) 公開方法。
EOF
)"
```

---

## Task 2: 加入 `QUOTE_NOT_FOUND` error code

**Files:**
- Modify: `src/app/api/errors.py`

- [ ] **Step 1: 看一下既有 enum**

Run: `grep -n "class ErrorCode\|= \"[A-Z_]*\"" src/app/api/errors.py | head -20`

Expected: 看到 `INTERNAL_ERROR`、`VALIDATION_ERROR` … 等既有 codes 與其賦值風格。

- [ ] **Step 2: 加 enum value**

在 `ErrorCode` 類別內，仿既有寫法新增一行（建議放在 quote 相關附近，例如 `INVALID_PRICE` 之後）：

```python
QUOTE_NOT_FOUND = "QUOTE_NOT_FOUND"
```

- [ ] **Step 3: 跑 lint/typecheck**

Run: `uv run ruff check src/app/api/errors.py && uv run mypy src`

Expected: 通過。

- [ ] **Step 4: Commit（暫不單獨 commit，併入 Task 3）**

> Task 2 太小，與 Task 3 一併 commit。直接進入 Task 3，不在此處呼叫 `git commit`。

---

## Task 3: `GET /dev/quotes/{symbol}` endpoint

**Files:**
- Modify: `src/app/schemas/dev_quote.py`
- Modify: `src/app/api/routes/dev_quotes.py`
- Modify: `tests/api/test_dev_quote_api.py`

- [ ] **Step 1: 加 response schema**

在 `src/app/schemas/dev_quote.py` 既有檔案末尾加入：

```python
class DevQuoteFetchResponseData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    symbol: str
    bid_price: Decimal | None = Field(default=None, serialization_alias="bidPrice")
    ask_price: Decimal | None = Field(default=None, serialization_alias="askPrice")
    last_price: Decimal | None = Field(default=None, serialization_alias="lastPrice")
    quote_time: AwareDatetime = Field(serialization_alias="quoteTime")


class DevQuoteFetchResponse(BaseModel):
    data: DevQuoteFetchResponseData
```

> Decimal 預設 Pydantic 序列化為 string，符合 spec 對稱要求。確認 Pydantic v2 行為：`model_dump_json()` 時 Decimal → "123.45"。

- [ ] **Step 2: 寫失敗測試（先寫 200 與 404 兩個 case）**

在 `tests/api/test_dev_quote_api.py` 既有檔案末尾加入：

```python
from fastapi import status


def test_get_dev_quote_returns_404_when_symbol_absent(client_with_dev_quote_service: TestClient) -> None:
    response = client_with_dev_quote_service.get("/dev/quotes/UNKNOWN")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["error"]["code"] == "QUOTE_NOT_FOUND"
    assert "requestId" in body["error"]


def test_get_dev_quote_returns_snapshot_after_upsert(client_with_dev_quote_service: TestClient) -> None:
    upsert = client_with_dev_quote_service.post(
        "/dev/quotes",
        json={
            "symbol": "2330",
            "bidPrice": "590.0000",
            "askPrice": "591.0000",
            "lastPrice": "590.5000",
            "quoteTime": "2026-05-19T02:14:33+00:00",
        },
    )
    assert upsert.status_code == status.HTTP_200_OK

    response = client_with_dev_quote_service.get("/dev/quotes/2330")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["symbol"] == "2330"
    assert data["bidPrice"] == "590.0000"
    assert data["askPrice"] == "591.0000"
    assert data["lastPrice"] == "590.5000"
    assert data["quoteTime"] == "2026-05-19T02:14:33+00:00"


def test_get_dev_quote_serializes_decimal_as_string(client_with_dev_quote_service: TestClient) -> None:
    client_with_dev_quote_service.post(
        "/dev/quotes",
        json={
            "symbol": "2330",
            "lastPrice": "590.5",
            "quoteTime": "2026-05-19T02:14:33+00:00",
        },
    )

    response = client_with_dev_quote_service.get("/dev/quotes/2330")

    data = response.json()["data"]
    assert isinstance(data["lastPrice"], str), f"lastPrice 應為 string 但是 {type(data['lastPrice']).__name__}"
```

> Fixture 名 `client_with_dev_quote_service` 是 BE-V0.5-08 在 `tests/api/conftest.py` 或 `test_dev_quotes.py` 既有的；以該檔實際名稱為準。若不存在則 inline 建：
>
> ```python
> @pytest.fixture
> def client_with_dev_quote_service() -> Generator[TestClient]:
>     app = create_app()
>     yield TestClient(app)
>     app.dependency_overrides.clear()
> ```
>
> 這裡刻意「不」mock service — 透過 POST /dev/quotes 灌資料、再 GET 出來，達到端到端驗證。

- [ ] **Step 3: 跑測試確認 fail**

Run: `uv run pytest tests/api/test_dev_quote_api.py -v -k "get_dev_quote"`

Expected: 三個新測試 FAIL（404 Not Found：路由不存在）。

- [ ] **Step 4: 加 route handler**

在 `src/app/api/routes/dev_quotes.py` 既有檔案末尾加入：

```python
from app.api.errors import ApiError, ErrorCode
from app.schemas.dev_quote import DevQuoteFetchResponse, DevQuoteFetchResponseData


@router.get(
    "/quotes/{symbol}",
    response_model=DevQuoteFetchResponse,
    status_code=status.HTTP_200_OK,
    summary="查詢開發用 quote snapshot（local mode 限定）",
    description=(
        "讀取 in-memory store 的目前 snapshot。"
        "查無資料回 404。LOCAL_MODE=false 時 router 不會被掛載，呼叫會 404。"
    ),
)
def get_dev_quote(
    service: DevQuoteIngestServiceDep,
    symbol: str,
) -> DevQuoteFetchResponse:
    snapshot = service.get_snapshot(symbol)
    if snapshot is None:
        raise ApiError(ErrorCode.QUOTE_NOT_FOUND, status.HTTP_404_NOT_FOUND)
    return DevQuoteFetchResponse(
        data=DevQuoteFetchResponseData(
            symbol=snapshot.symbol,
            bid_price=snapshot.bid_price,
            ask_price=snapshot.ask_price,
            last_price=snapshot.last_price,
            quote_time=snapshot.quote_time,
        )
    )
```

> 上方既有 import 處要把 `status` 確認有 import；以及 ensure `DevQuoteIngestServiceDep` 有 import。若 `QuoteSnapshot` 欄位名與此處不同，依實際命名調整。

- [ ] **Step 5: 跑測試確認 pass**

Run: `uv run pytest tests/api/test_dev_quote_api.py -v`

Expected: 既有 + 新測試全 PASS。

- [ ] **Step 6: 跑 quality gate**

Run: `make check`

Expected: 通過。

- [ ] **Step 7: Commit**

```bash
git add src/app/api/errors.py src/app/schemas/dev_quote.py src/app/api/routes/dev_quotes.py tests/api/test_dev_quote_api.py
git commit -m "$(cat <<'EOF'
feat(quote): add GET /dev/quotes/{symbol} for snapshot lookup

提供 LOCAL_MODE 限定的 GET endpoint 讀取 in-memory store snapshot，
供 dev console 顯示目前狀態。新增 QUOTE_NOT_FOUND error code 與
DevQuoteFetchResponse schema（Decimal 序列化為 string）。
EOF
)"
```

---

## Task 4: `GET /dev/console` route + StaticFiles mount

**Files:**
- Create: `src/app/api/routes/dev_console.py`
- Create: `src/app/static/dev_console.html`（placeholder）
- Modify: `src/app/main.py`
- Create: `tests/api/test_dev_console.py`

- [ ] **Step 1: 寫 placeholder HTML**

Create `src/app/static/dev_console.html`:

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>ai-stock dev console</title>
</head>
<body>
  <p>dev console placeholder</p>
</body>
</html>
```

> 之後 Task 6 會覆蓋成真正內容；現在先讓 route 有東西可以回。

- [ ] **Step 2: 寫 route 失敗測試**

Create `tests/api/test_dev_console.py`:

```python
"""GET /dev/console 與 static mount 在 LOCAL_MODE gating 下的行為。"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture
def local_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LOCAL_MODE", "true")
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def non_local_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LOCAL_MODE", "false")
    get_settings.cache_clear()
    return TestClient(create_app())


def test_dev_console_returns_html_when_local_mode(local_client: TestClient) -> None:
    response = local_client.get("/dev/console")

    assert response.status_code == status.HTTP_200_OK
    assert response.headers["content-type"].startswith("text/html")
    assert "dev console" in response.text.lower()


def test_dev_console_returns_404_when_not_local_mode(non_local_client: TestClient) -> None:
    response = non_local_client.get("/dev/console")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_dev_console_static_returns_404_when_not_local_mode(non_local_client: TestClient) -> None:
    response = non_local_client.get("/dev/console/static/dev_console.html")

    assert response.status_code == status.HTTP_404_NOT_FOUND
```

- [ ] **Step 3: 跑測試確認 fail**

Run: `uv run pytest tests/api/test_dev_console.py -v`

Expected: 三個測試全 FAIL（路由不存在）。

- [ ] **Step 4: 建 route module**

Create `src/app/api/routes/dev_console.py`:

```python
"""GET /dev/console — local-only HTML test console.

Router is included by app.main.create_app() only when settings.local_mode
is true; when local_mode is false the route does not exist and FastAPI
returns 404 by default.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

# 用 module 路徑解析，避免 uvicorn 不同 cwd 啟動時 FileResponse 找不到檔案。
_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


@router.get(
    "/console",
    include_in_schema=False,
    summary="dev console 視覺化測試介面（local mode 限定）",
)
def serve_console() -> FileResponse:
    return FileResponse(_STATIC_DIR / "dev_console.html", media_type="text/html")
```

- [ ] **Step 5: 在 main.py 掛載 router 與 StaticFiles**

修改 `src/app/main.py` 中既有的 `if settings.local_mode:` 區塊，最終長這樣：

```python
if settings.local_mode:
    # Lazy import keeps dev router out of the dependency graph in non-local builds.
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    from app.api.routes.dev_console import router as dev_console_router
    from app.api.routes.dev_quotes import router as dev_quotes_router

    app.include_router(dev_quotes_router, prefix="/dev", tags=["dev"])
    app.include_router(dev_console_router, prefix="/dev", tags=["dev"])

    _static_dir = Path(__file__).resolve().parent / "static"
    app.mount(
        "/dev/console/static",
        StaticFiles(directory=_static_dir),
        name="dev_console_static",
    )
```

- [ ] **Step 6: 跑測試確認 pass**

Run: `uv run pytest tests/api/test_dev_console.py -v`

Expected: 三個測試全 PASS。

- [ ] **Step 7: 跑全套測試確保沒回歸**

Run: `make check`

Expected: 通過。

- [ ] **Step 8: Commit**

```bash
git add src/app/api/routes/dev_console.py src/app/static/dev_console.html src/app/main.py tests/api/test_dev_console.py
git commit -m "$(cat <<'EOF'
feat(api): expose GET /dev/console and static mount under LOCAL_MODE

新增 dev console router 與 /dev/console/static StaticFiles 掛載，
與既有 POST /dev/quotes 共用 LOCAL_MODE gating。HTML 目前為
placeholder，後續 commit 才會塞入真正內容。
EOF
)"
```

---

## Task 5: Vendor Alpine.js 並設定 lint 排除

**Files:**
- Create: `src/app/static/vendor/alpine.min.js`
- Create or Modify: `.gitattributes`
- Modify: `pyproject.toml`（若 ruff 有 file glob 需要排除）

- [ ] **Step 1: 下載 Alpine.js 3.14.1（pinned version）**

Run:
```bash
mkdir -p src/app/static/vendor
curl -fsSL -o src/app/static/vendor/alpine.min.js \
  https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js
```

Expected: 下載成功，檔案約 15KB。

- [ ] **Step 2: 驗證下載完整性**

Run: `wc -c src/app/static/vendor/alpine.min.js && head -c 200 src/app/static/vendor/alpine.min.js`

Expected: 檔案大小 ~15000 bytes；開頭看到 Alpine.js 的 banner 或壓縮過的 `(()=>{`。

- [ ] **Step 3: 建 .gitattributes 標 vendored**

Create `.gitattributes`:

```
# 統計工具忽略 vendored 第三方檔案
src/app/static/vendor/** linguist-vendored=true
src/app/static/vendor/** -diff
```

> 若 repo 已有 `.gitattributes`，append 上述兩行而不要覆蓋。

- [ ] **Step 4: 確認 ruff 不會掃 JS（理論上不會，但檢查 pyproject 設定）**

Run: `grep -A 30 "\[tool.ruff" pyproject.toml | head -40`

Expected: ruff 設定通常 `src = ["src"]` 或只看 `*.py`，不會掃 JS。若有不尋常設定造成誤掃 → 在 ruff config 的 `exclude` 加 `"src/app/static/vendor"`。

- [ ] **Step 5: Commit**

```bash
git add src/app/static/vendor/alpine.min.js .gitattributes
git commit -m "$(cat <<'EOF'
chore(static): vendor Alpine.js 3.14.1 for dev console

Vendor 而非 CDN：保證離線可用、避免 supply chain 風險。
.gitattributes 標記為 linguist-vendored 排除統計與 diff 噪音。
EOF
)"
```

---

## Task 6: HTML / CSS / JS 骨架 + Health section

**Files:**
- Modify: `src/app/static/dev_console.html`
- Create: `src/app/static/dev_console.css`
- Create: `src/app/static/dev_console.js`

- [ ] **Step 1: 寫 CSS**

Overwrite `src/app/static/dev_console.css`:

```css
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace;
  font-size: 13px;
  background: #1e1e1e;
  color: #d4d4d4;
  display: flex;
  flex-direction: column;
  height: 100vh;
}
header {
  background: #252526;
  padding: 8px 14px;
  border-bottom: 1px solid #333;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
header h1 { margin: 0; font-size: 14px; color: #4ec9b0; }
header .meta { color: #888; font-size: 12px; }
header .meta span { margin-left: 12px; }
main {
  flex: 1;
  display: flex;
  overflow: hidden;
}
nav {
  width: 180px;
  background: #252526;
  border-right: 1px solid #333;
  padding: 8px 0;
  overflow-y: auto;
}
nav button {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px 14px;
  background: transparent;
  border: none;
  color: #d4d4d4;
  cursor: pointer;
  font: inherit;
}
nav button:hover { background: #2a2d2e; }
nav button.active { background: #094771; color: #fff; }
section.panel {
  flex: 1;
  padding: 16px 20px;
  overflow-y: auto;
}
section.panel h2 { margin-top: 0; font-size: 14px; color: #4ec9b0; }
form { margin-bottom: 12px; }
form label {
  display: block;
  margin-bottom: 8px;
}
form label > span {
  display: inline-block;
  width: 100px;
  color: #888;
}
form input, form select {
  background: #1e1e1e;
  color: #d4d4d4;
  border: 1px solid #444;
  padding: 4px 6px;
  font: inherit;
  width: 260px;
}
form button, .btn {
  background: #0e639c;
  color: #fff;
  border: none;
  padding: 6px 14px;
  font: inherit;
  cursor: pointer;
  margin-right: 6px;
}
form button:hover, .btn:hover { background: #1177bb; }
.btn-secondary { background: #3a3d41; }
.btn-secondary:hover { background: #4a4d51; }
pre {
  background: #111;
  padding: 10px;
  border-radius: 3px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 6px 0;
}
.preview-label { color: #888; margin-top: 12px; font-size: 12px; }
.response { margin-top: 14px; }
.response .status { font-size: 12px; color: #888; margin-bottom: 4px; }
.response .status .ok { color: #4ec9b0; }
.response .status .err { color: #f48771; }
.error-envelope {
  background: #2d1414;
  border-left: 3px solid #f48771;
  padding: 10px 12px;
}
.error-envelope .err-code { color: #f48771; font-weight: bold; font-size: 14px; }
.error-envelope .err-msg { font-weight: bold; margin: 4px 0; }
.error-envelope .err-details { color: #aaa; }
.error-envelope .err-reqid { font-size: 11px; color: #888; margin-top: 4px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #333; }
th { color: #888; font-weight: normal; }
footer.log {
  border-top: 1px solid #333;
  background: #252526;
  max-height: 240px;
  overflow-y: auto;
}
footer.log .log-header {
  padding: 6px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  position: sticky;
  top: 0;
  background: #252526;
}
footer.log .log-header h3 { margin: 0; font-size: 12px; color: #888; }
footer.log ul { list-style: none; padding: 0; margin: 0; }
footer.log li {
  padding: 4px 14px;
  border-top: 1px solid #2d2d2d;
  cursor: pointer;
}
footer.log li:hover { background: #2a2d2e; }
footer.log li .ts { color: #888; margin-right: 8px; }
footer.log li .method { display: inline-block; width: 40px; color: #4ec9b0; }
footer.log li .path { margin-right: 8px; }
footer.log li .status-ok { color: #4ec9b0; }
footer.log li .status-err { color: #f48771; }
footer.log li .reqid { color: #888; font-size: 11px; margin-left: 6px; }
footer.log li .errcode { color: #f48771; font-size: 11px; margin-left: 6px; }
footer.log li.expanded pre { display: block; }
footer.log li pre { display: none; font-size: 11px; }
.hidden { display: none; }
.flash {
  background: #094771;
  padding: 6px 10px;
  border-radius: 3px;
  margin: 8px 0;
  display: inline-block;
}
```

- [ ] **Step 2: 寫 JS（先放 fetch wrapper、Alpine app skeleton、Health section）**

Create `src/app/static/dev_console.js`:

```javascript
// dev console - single-page Alpine.js app for ai-stock LOCAL_MODE testing.

const LOG_KEY = 'ai-stock-dev-console-log';
const LOG_LIMIT = 50;

function loadLog() {
  try {
    const raw = localStorage.getItem(LOG_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (_) {
    return [];
  }
}

function saveLog(entries) {
  try {
    localStorage.setItem(LOG_KEY, JSON.stringify(entries.slice(0, LOG_LIMIT)));
  } catch (e) {
    console.warn('failed to persist log', e);
  }
}

function nowTs() {
  return new Date().toTimeString().slice(0, 8);
}

// Build a log entry and prepend to a reactive list.
function recordLog(state, entry) {
  state.log.unshift(entry);
  if (state.log.length > LOG_LIMIT) state.log.length = LOG_LIMIT;
  saveLog(state.log);
}

// Generic fetch helper. Returns { status, headers, body, requestId, errorCode }.
async function apiCall(state, { method, path, body }) {
  const init = { method, headers: { 'Accept': 'application/json' } };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let resp, status = 0, responseBody = null, requestId = null;
  try {
    resp = await fetch(path, init);
    status = resp.status;
    requestId = resp.headers.get('X-Request-Id');
    const text = await resp.text();
    try { responseBody = text ? JSON.parse(text) : null; }
    catch (_) { responseBody = text; }
  } catch (e) {
    responseBody = { error: { code: 'NETWORK_ERROR', message: String(e), details: null, requestId: null } };
  }
  const errorCode = responseBody && responseBody.error && responseBody.error.code
    ? responseBody.error.code : null;
  const entry = {
    timestamp: nowTs(),
    method,
    path,
    status,
    requestId,
    requestBody: body ?? null,
    responseBody,
    errorCode,
  };
  recordLog(state, entry);
  return entry;
}

function devConsoleApp() {
  return {
    activeSection: 'health',
    sections: ['health', 'symbols', 'symbolDetail', 'intent', 'devQuote', 'quoteStore'],
    log: loadLog(),
    expanded: {},

    // ---- Health ----
    health: { lastResult: null },
    async runHealth() {
      this.health.lastResult = await apiCall(this, { method: 'GET', path: '/health' });
    },

    // Helpers used by templates
    isError(entry) { return entry.status >= 400 || entry.status === 0; },
    isErrorEnvelope(body) {
      return body && typeof body === 'object' && body.error && typeof body.error.code === 'string';
    },
    pretty(value) {
      try { return JSON.stringify(value, null, 2); }
      catch (_) { return String(value); }
    },
    shortReqId(id) { return id ? id.slice(0, 8) + '…' : ''; },
    toggleExpand(idx) { this.expanded[idx] = !this.expanded[idx]; },
    clearLog() { this.log = []; saveLog([]); },
    switchTo(section) { this.activeSection = section; },
  };
}

window.devConsoleApp = devConsoleApp;
```

- [ ] **Step 3: 寫主 HTML（包含 Header、Nav、Health section、空 placeholder for 其他 section、Log footer）**

Overwrite `src/app/static/dev_console.html`:

```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ai-stock dev console</title>
  <link rel="stylesheet" href="/dev/console/static/dev_console.css">
  <script defer src="/dev/console/static/vendor/alpine.min.js"></script>
  <script defer src="/dev/console/static/dev_console.js"></script>
</head>
<body x-data="devConsoleApp()">
  <header>
    <h1>ai-stock dev console</h1>
    <div class="meta">
      <span>env: local</span>
      <span>LOCAL_MODE</span>
    </div>
  </header>

  <main>
    <nav>
      <button :class="{ active: activeSection === 'health' }" @click="switchTo('health')">Health</button>
      <button :class="{ active: activeSection === 'symbols' }" @click="switchTo('symbols')">Symbols</button>
      <button :class="{ active: activeSection === 'symbolDetail' }" @click="switchTo('symbolDetail')">Symbol Detail</button>
      <button :class="{ active: activeSection === 'intent' }" @click="switchTo('intent')">Intent</button>
      <button :class="{ active: activeSection === 'devQuote' }" @click="switchTo('devQuote')">Dev Quote</button>
      <button :class="{ active: activeSection === 'quoteStore' }" @click="switchTo('quoteStore')">Quote Store</button>
    </nav>

    <!-- Health -->
    <section class="panel" x-show="activeSection === 'health'">
      <h2>GET /health</h2>
      <button class="btn" @click="runHealth()">Check</button>
      <template x-if="health.lastResult">
        <div class="response">
          <div class="status">
            status:
            <span :class="isError(health.lastResult) ? 'err' : 'ok'" x-text="health.lastResult.status"></span>
            <span x-show="health.lastResult.requestId">· X-Request-Id: <span x-text="health.lastResult.requestId"></span></span>
          </div>
          <template x-if="isErrorEnvelope(health.lastResult.responseBody)">
            <div class="error-envelope">
              <div class="err-code" x-text="health.lastResult.responseBody.error.code"></div>
              <div class="err-msg" x-text="health.lastResult.responseBody.error.message"></div>
              <div class="err-details"><pre x-text="pretty(health.lastResult.responseBody.error.details)"></pre></div>
              <div class="err-reqid">requestId: <span x-text="health.lastResult.responseBody.error.requestId"></span></div>
            </div>
          </template>
          <template x-if="!isErrorEnvelope(health.lastResult.responseBody)">
            <pre x-text="pretty(health.lastResult.responseBody)"></pre>
          </template>
        </div>
      </template>
    </section>

    <!-- 其他 section placeholders（Task 7-9 填入） -->
    <section class="panel" x-show="activeSection === 'symbols'"><h2>Symbols（待實作）</h2></section>
    <section class="panel" x-show="activeSection === 'symbolDetail'"><h2>Symbol Detail（待實作）</h2></section>
    <section class="panel" x-show="activeSection === 'intent'"><h2>Intent（待實作）</h2></section>
    <section class="panel" x-show="activeSection === 'devQuote'"><h2>Dev Quote（待實作）</h2></section>
    <section class="panel" x-show="activeSection === 'quoteStore'"><h2>Quote Store（待實作）</h2></section>
  </main>

  <footer class="log">
    <div class="log-header">
      <h3>Request log（最多 50 筆，存 localStorage）</h3>
      <button class="btn btn-secondary" @click="clearLog()">Clear</button>
    </div>
    <ul>
      <template x-for="(entry, idx) in log" :key="idx">
        <li :class="{ expanded: expanded[idx] }" @click="toggleExpand(idx)">
          <span class="ts" x-text="entry.timestamp"></span>
          <span class="method" x-text="entry.method"></span>
          <span class="path" x-text="entry.path"></span>
          <span :class="entry.status >= 400 || entry.status === 0 ? 'status-err' : 'status-ok'" x-text="entry.status"></span>
          <span class="reqid" x-text="shortReqId(entry.requestId)"></span>
          <span class="errcode" x-show="entry.errorCode" x-text="'❗ ' + entry.errorCode"></span>
          <pre x-text="'REQUEST:\n' + pretty(entry.requestBody) + '\n\nRESPONSE:\n' + pretty(entry.responseBody)"></pre>
        </li>
      </template>
    </ul>
  </footer>
</body>
</html>
```

- [ ] **Step 4: 手動 smoke test**

Run terminal 1: `make dev`
Run terminal 2 (or 開 browser):
- 打開 `http://127.0.0.1:8000/dev/console`
- 預期看到 dark theme 頁面，左側 nav 有 6 個 section
- 點「Health」section 中的「Check」按鈕
- 預期 response 區塊顯示 `data.database: "ok"`、log footer 多一筆 `GET /health 200`
- 重整頁面，預期 log 仍在

若 200 但 `database` 不是 "ok"：DB 沒起；先 `make up-db`。

- [ ] **Step 5: Commit**

```bash
git add src/app/static/dev_console.html src/app/static/dev_console.css src/app/static/dev_console.js
git commit -m "$(cat <<'EOF'
feat(dev-console): add HTML skeleton with Health section and request log

主頁面骨架包含左側 section nav、中央 panel、底部 request log
（localStorage 持久化、最多 50 筆）、error envelope 格式化呈現。
首個 section: Health（GET /health）。其餘 section 為 placeholder，
後續 commit 補上。
EOF
)"
```

---

## Task 7: Symbols / Symbol Detail / Intent sections

**Files:**
- Modify: `src/app/static/dev_console.html`
- Modify: `src/app/static/dev_console.js`

- [ ] **Step 1: 在 JS 加入三個 section 的狀態與動作**

在 `dev_console.js` 的 `devConsoleApp()` return object 中，於 Health section 之後加：

```javascript
    // ---- Symbols ----
    symbols: { q: '', limit: 20, lastResult: null },
    async runSymbols() {
      const params = new URLSearchParams();
      if (this.symbols.q) params.set('q', this.symbols.q);
      params.set('limit', String(this.symbols.limit));
      this.symbols.lastResult = await apiCall(this, {
        method: 'GET', path: '/symbols?' + params.toString()
      });
    },

    // ---- Symbol Detail ----
    symbolDetail: { symbol: '', lastResult: null },
    async runSymbolDetail() {
      const sym = encodeURIComponent(this.symbolDetail.symbol);
      this.symbolDetail.lastResult = await apiCall(this, {
        method: 'GET', path: '/symbols/' + sym
      });
    },

    // ---- Intent ----
    intent: { symbol: '', side: 'buy', quantity: 1, lastResult: null },
    async runIntent() {
      this.intent.lastResult = await apiCall(this, {
        method: 'POST', path: '/intents',
        body: {
          symbol: this.intent.symbol,
          side: this.intent.side,
          quantity: Number(this.intent.quantity),
        },
      });
    },
```

- [ ] **Step 2: 在 HTML 替換 Symbols section placeholder**

把 `<section class="panel" x-show="activeSection === 'symbols'">...</section>` 整段換成：

```html
    <section class="panel" x-show="activeSection === 'symbols'">
      <h2>GET /symbols</h2>
      <form @submit.prevent="runSymbols()">
        <label><span>q</span><input x-model="symbols.q" placeholder="2330 / 台積電"></label>
        <label><span>limit</span><input type="number" min="1" max="50" x-model="symbols.limit"></label>
        <button type="submit">Search</button>
      </form>
      <template x-if="symbols.lastResult">
        <div class="response">
          <div class="status">
            status:
            <span :class="isError(symbols.lastResult) ? 'err' : 'ok'" x-text="symbols.lastResult.status"></span>
          </div>
          <template x-if="isErrorEnvelope(symbols.lastResult.responseBody)">
            <div class="error-envelope">
              <div class="err-code" x-text="symbols.lastResult.responseBody.error.code"></div>
              <div class="err-msg" x-text="symbols.lastResult.responseBody.error.message"></div>
            </div>
          </template>
          <template x-if="!isErrorEnvelope(symbols.lastResult.responseBody) && symbols.lastResult.responseBody && symbols.lastResult.responseBody.data">
            <table>
              <thead><tr><th>symbol</th><th>displayName</th><th>market</th><th>type</th><th>status</th></tr></thead>
              <tbody>
                <template x-for="row in symbols.lastResult.responseBody.data" :key="row.symbol">
                  <tr>
                    <td x-text="row.symbol"></td>
                    <td x-text="row.displayName"></td>
                    <td x-text="row.market"></td>
                    <td x-text="row.instrumentType"></td>
                    <td x-text="row.tradableStatus"></td>
                  </tr>
                </template>
              </tbody>
            </table>
          </template>
        </div>
      </template>
    </section>
```

- [ ] **Step 3: 替換 Symbol Detail section placeholder**

```html
    <section class="panel" x-show="activeSection === 'symbolDetail'">
      <h2>GET /symbols/{symbol}</h2>
      <form @submit.prevent="runSymbolDetail()">
        <label><span>symbol</span><input x-model="symbolDetail.symbol" placeholder="2330"></label>
        <button type="submit">Get</button>
      </form>
      <template x-if="symbolDetail.lastResult">
        <div class="response">
          <div class="status">
            status:
            <span :class="isError(symbolDetail.lastResult) ? 'err' : 'ok'" x-text="symbolDetail.lastResult.status"></span>
            <span x-show="symbolDetail.lastResult.requestId">· X-Request-Id: <span x-text="symbolDetail.lastResult.requestId"></span></span>
          </div>
          <template x-if="isErrorEnvelope(symbolDetail.lastResult.responseBody)">
            <div class="error-envelope">
              <div class="err-code" x-text="symbolDetail.lastResult.responseBody.error.code"></div>
              <div class="err-msg" x-text="symbolDetail.lastResult.responseBody.error.message"></div>
            </div>
          </template>
          <template x-if="!isErrorEnvelope(symbolDetail.lastResult.responseBody)">
            <pre x-text="pretty(symbolDetail.lastResult.responseBody)"></pre>
          </template>
        </div>
      </template>
    </section>
```

- [ ] **Step 4: 替換 Intent section placeholder**

```html
    <section class="panel" x-show="activeSection === 'intent'">
      <h2>POST /intents</h2>
      <form @submit.prevent="runIntent()">
        <label><span>symbol</span><input x-model="intent.symbol" placeholder="2330"></label>
        <label><span>side</span>
          <select x-model="intent.side"><option value="buy">buy</option><option value="sell">sell</option></select>
        </label>
        <label><span>quantity</span><input type="number" min="1" x-model.number="intent.quantity"></label>
        <button type="submit">Send</button>
      </form>
      <template x-if="intent.lastResult">
        <div class="response">
          <div class="status">
            status:
            <span :class="isError(intent.lastResult) ? 'err' : 'ok'" x-text="intent.lastResult.status"></span>
            <span x-show="intent.lastResult.requestId">· X-Request-Id: <span x-text="intent.lastResult.requestId"></span></span>
          </div>
          <template x-if="isErrorEnvelope(intent.lastResult.responseBody)">
            <div class="error-envelope">
              <div class="err-code" x-text="intent.lastResult.responseBody.error.code"></div>
              <div class="err-msg" x-text="intent.lastResult.responseBody.error.message"></div>
              <div class="err-details"><pre x-text="pretty(intent.lastResult.responseBody.error.details)"></pre></div>
              <div class="err-reqid">requestId: <span x-text="intent.lastResult.responseBody.error.requestId"></span></div>
            </div>
          </template>
          <template x-if="!isErrorEnvelope(intent.lastResult.responseBody)">
            <pre x-text="pretty(intent.lastResult.responseBody)"></pre>
          </template>
        </div>
      </template>
    </section>
```

- [ ] **Step 5: 手動 smoke**

`make dev` 後在瀏覽器：
- Symbols：空 q + limit=5 送出 → 應看到表格（如果 DB 有 seed）
- Symbol Detail：填 `2330` 送出 → 應看到 card；填 `XXX` 應看到 `UNKNOWN_SYMBOL` 紅色 error envelope
- Intent：填 `2330` / buy / 1 送出 → 應看到 201 + `pending`；改 quantity=0 送出 → 應看到 422 / 400 error envelope（依後端實作）

- [ ] **Step 6: Commit**

```bash
git add src/app/static/dev_console.html src/app/static/dev_console.js
git commit -m "feat(dev-console): add Symbols / Symbol Detail / Intent sections"
```

---

## Task 8: Dev Quote section（含 live JSON preview 與 Now 按鈕）

**Files:**
- Modify: `src/app/static/dev_console.html`
- Modify: `src/app/static/dev_console.js`

- [ ] **Step 1: 在 JS 加 Dev Quote 狀態與動作**

在 `devConsoleApp()` return object 中（Intent 之後）加：

```javascript
    // ---- Dev Quote ----
    devQuote: {
      symbol: '',
      bidPrice: '',
      askPrice: '',
      lastPrice: '',
      quoteTime: '',
      lastResult: null,
    },
    devQuotePayload() {
      // 只把有填的欄位放進去，避免送 "" 給 Decimal 解析
      const p = { symbol: this.devQuote.symbol };
      if (this.devQuote.bidPrice !== '') p.bidPrice = this.devQuote.bidPrice;
      if (this.devQuote.askPrice !== '') p.askPrice = this.devQuote.askPrice;
      if (this.devQuote.lastPrice !== '') p.lastPrice = this.devQuote.lastPrice;
      if (this.devQuote.quoteTime !== '') p.quoteTime = this.devQuote.quoteTime;
      return p;
    },
    fillNowUtc() {
      this.devQuote.quoteTime = new Date().toISOString();
    },
    async runDevQuote() {
      this.devQuote.lastResult = await apiCall(this, {
        method: 'POST',
        path: '/dev/quotes',
        body: this.devQuotePayload(),
      });
    },
```

- [ ] **Step 2: 替換 Dev Quote section placeholder**

```html
    <section class="panel" x-show="activeSection === 'devQuote'">
      <h2>POST /dev/quotes</h2>
      <form @submit.prevent="runDevQuote()">
        <label><span>symbol</span><input x-model="devQuote.symbol" placeholder="2330"></label>
        <label><span>bidPrice</span><input x-model="devQuote.bidPrice" placeholder="590.0000 (string)"></label>
        <label><span>askPrice</span><input x-model="devQuote.askPrice" placeholder="591.0000 (string)"></label>
        <label><span>lastPrice</span><input x-model="devQuote.lastPrice" placeholder="590.5000 (string)"></label>
        <label>
          <span>quoteTime</span>
          <input x-model="devQuote.quoteTime" placeholder="2026-05-19T02:14:33Z" style="width:260px">
          <button type="button" class="btn btn-secondary" @click="fillNowUtc()">Now (UTC)</button>
        </label>
        <div class="preview-label">送出 payload (camelCase, Decimal as string):</div>
        <pre x-text="pretty(devQuotePayload())"></pre>
        <button type="submit">Send</button>
      </form>
      <template x-if="devQuote.lastResult">
        <div class="response">
          <div class="status">
            status:
            <span :class="isError(devQuote.lastResult) ? 'err' : 'ok'" x-text="devQuote.lastResult.status"></span>
            <span x-show="devQuote.lastResult.requestId">· X-Request-Id: <span x-text="devQuote.lastResult.requestId"></span></span>
          </div>
          <template x-if="isErrorEnvelope(devQuote.lastResult.responseBody)">
            <div class="error-envelope">
              <div class="err-code" x-text="devQuote.lastResult.responseBody.error.code"></div>
              <div class="err-msg" x-text="devQuote.lastResult.responseBody.error.message"></div>
              <div class="err-details"><pre x-text="pretty(devQuote.lastResult.responseBody.error.details)"></pre></div>
              <div class="err-reqid">requestId: <span x-text="devQuote.lastResult.responseBody.error.requestId"></span></div>
            </div>
          </template>
          <template x-if="!isErrorEnvelope(devQuote.lastResult.responseBody)">
            <pre x-text="pretty(devQuote.lastResult.responseBody)"></pre>
          </template>
        </div>
      </template>
    </section>
```

- [ ] **Step 3: 手動 smoke**

`make dev` 後：
- Dev Quote section：填 `2330`、各價填 `590` / `591` / `590.5`、按 `Now (UTC)` → 觀察 JSON preview 即時更新成 camelCase JSON
- 送出 → 200 / `data.updated: true`
- 故意把 `lastPrice` 填 `not-a-number` 送出 → 看到紅色 error envelope + `code` 顯示

- [ ] **Step 4: Commit**

```bash
git add src/app/static/dev_console.html src/app/static/dev_console.js
git commit -m "feat(dev-console): add Dev Quote section with live JSON preview"
```

---

## Task 9: Quote Store section + 跨 section 跳轉

**Files:**
- Modify: `src/app/static/dev_console.html`
- Modify: `src/app/static/dev_console.js`

- [ ] **Step 1: 在 JS 加 Quote Store 狀態與動作；同時加跨 section 跳轉的 helper**

在 `devConsoleApp()` return object 中加：

```javascript
    // ---- Quote Store ----
    quoteStore: { symbol: '', lastResult: null },
    async runQuoteStoreLookup() {
      const sym = encodeURIComponent(this.quoteStore.symbol);
      this.quoteStore.lastResult = await apiCall(this, {
        method: 'GET', path: '/dev/quotes/' + sym
      });
    },
    inspectInStore(symbol) {
      this.quoteStore.symbol = symbol;
      this.activeSection = 'quoteStore';
      this.runQuoteStoreLookup();
    },
    // 近期 POST /dev/quotes 的 symbol（從 log 抓 distinct）
    recentlyPushedSymbols() {
      const symbols = [];
      const seen = new Set();
      for (const entry of this.log) {
        if (entry.method === 'POST' && entry.path === '/dev/quotes' && entry.requestBody && entry.requestBody.symbol) {
          const s = entry.requestBody.symbol;
          if (!seen.has(s)) { seen.add(s); symbols.push(s); }
        }
        if (symbols.length >= 10) break;
      }
      return symbols;
    },
```

- [ ] **Step 2: 在 Dev Quote section 的 response 區（200 成功時）加「→ 查 store」按鈕**

在 Task 8 寫的 Dev Quote section 中，`<template x-if="!isErrorEnvelope(devQuote.lastResult.responseBody)">` 的內層 pre 之後（或上方）加：

```html
            <template x-if="devQuote.lastResult.status === 200">
              <button class="btn btn-secondary" @click="inspectInStore(devQuote.symbol)">→ 查 store</button>
            </template>
```

- [ ] **Step 3: 替換 Quote Store section placeholder**

```html
    <section class="panel" x-show="activeSection === 'quoteStore'">
      <h2>GET /dev/quotes/{symbol}</h2>
      <form @submit.prevent="runQuoteStoreLookup()">
        <label><span>symbol</span><input x-model="quoteStore.symbol" placeholder="2330"></label>
        <button type="submit">Lookup</button>
      </form>

      <template x-if="recentlyPushedSymbols().length > 0">
        <div>
          <div class="preview-label">最近 POST 過的 symbol:</div>
          <template x-for="s in recentlyPushedSymbols()" :key="s">
            <button class="btn btn-secondary" @click="quoteStore.symbol = s; runQuoteStoreLookup()" x-text="s"></button>
          </template>
        </div>
      </template>

      <template x-if="quoteStore.lastResult">
        <div class="response">
          <div class="status">
            status:
            <span :class="isError(quoteStore.lastResult) ? 'err' : 'ok'" x-text="quoteStore.lastResult.status"></span>
            <span x-show="quoteStore.lastResult.requestId">· X-Request-Id: <span x-text="quoteStore.lastResult.requestId"></span></span>
          </div>
          <template x-if="isErrorEnvelope(quoteStore.lastResult.responseBody)">
            <div class="error-envelope">
              <div class="err-code" x-text="quoteStore.lastResult.responseBody.error.code"></div>
              <div class="err-msg" x-text="quoteStore.lastResult.responseBody.error.message"></div>
              <div class="err-reqid">requestId: <span x-text="quoteStore.lastResult.responseBody.error.requestId"></span></div>
            </div>
          </template>
          <template x-if="!isErrorEnvelope(quoteStore.lastResult.responseBody)">
            <pre x-text="pretty(quoteStore.lastResult.responseBody)"></pre>
          </template>
        </div>
      </template>
    </section>
```

- [ ] **Step 4: 手動 smoke（全流程）**

`make dev` 後：
1. Dev Quote section：灌 `2330` quote → 200
2. 在 response 區應出現「→ 查 store」按鈕 → 點下去
3. 自動切到 Quote Store section、symbol 已填好、回 200 + snapshot
4. 改 symbol 為 `UNKNOWN` 送出 → 紅色 `QUOTE_NOT_FOUND` envelope
5. Quote Store section 應看到「最近 POST 過的 symbol」按鈕列出 `2330`，點下去能再次查

- [ ] **Step 5: Commit**

```bash
git add src/app/static/dev_console.html src/app/static/dev_console.js
git commit -m "feat(dev-console): add Quote Store section and cross-section jump"
```

---

## Task 10: 收尾驗證

**Files:** （只跑檢查與文件確認）

- [ ] **Step 1: 跑完整 quality gate**

Run: `make check`

Expected: 所有 lint、format、typecheck、test PASS。

- [ ] **Step 2: 跑完整 spec §9.3 手動 smoke**

照 spec `docs/superpowers/specs/2026-05-19-dev-console-design.md` §9.3 第 1~9 步完整跑一次。逐項勾。重點：

- 第 7 步「Intent quantity=0 → 看到 error envelope 格式化」要看到紅色 envelope，不是純 JSON
- 第 8 步「重整頁面 → request log 仍在」 — 確認 localStorage 持久化
- 第 9 步「LOCAL_MODE=false 重啟 → /dev/console 回 404」 — 修改 `.env` 並重啟 `make dev` 驗證

- [ ] **Step 3: 整理 commit history**

Run: `git log --oneline main..HEAD`

Expected: 看到 7~8 個 commit（Task 1 service / Task 3 GET endpoint / Task 4 console route / Task 5 vendor / Task 6 skeleton / Task 7 sections / Task 8 dev quote / Task 9 quote store）。風格符合專案 Conventional Commits。

- [ ] **Step 4: Push 與開 PR**

Run:
```bash
git push -u origin feat/be-v0-5-dev-console
gh pr create --base main --title "feat(dev-console): 視覺化測試介面 + GET /dev/quotes/{symbol}" --body "$(cat <<'EOF'
## Summary

- 新增 LOCAL_MODE-only 的 `/dev/console` 視覺化測試介面（單檔 HTML + Alpine.js vendored）
- 新增 `GET /dev/quotes/{symbol}` 讓 console 能查 in-memory quote store 目前狀態
- Section 涵蓋：Health / Symbols / Symbol Detail / Intent / Dev Quote / Quote Store
- Request log 持久化於 localStorage（最多 50 筆），錯誤 envelope 格式化呈現

## 設計依據

`docs/superpowers/specs/2026-05-19-dev-console-design.md`

## 動機

V0.5 階段本地開發以 Swagger UI / curl 為主，無法看見 in-memory quote store 狀態、Decimal-as-string 與 camelCase 對應容易誤觸 400、且無 request 歷史。Dev console 補上這些缺口。

## 變更摘要

- **後端**：`GET /dev/quotes/{symbol}` 路由與 `DevQuoteIngestService.get_snapshot()`；`QUOTE_NOT_FOUND` error code；`GET /dev/console` 與 StaticFiles 掛載（皆 LOCAL_MODE gating）
- **前端**：`src/app/static/` 下單檔 HTML + 手寫 CSS + 一支 JS + vendored Alpine.js 3.14.1
- **測試**：服務層單元測試 + API 端到端測試 + LOCAL_MODE gating 驗證

## 測試說明

- `make check` 通過
- 手動 smoke 流程依 spec §9.3 全項過

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL 印出。

---

## Self-Review

依 spec 章節對照覆蓋情況：

| Spec 章節 | 對應 Task |
|---|---|
| §3.1 檔案配置 | Task 0/3/4/5/6 |
| §3.2 FastAPI 整合（main.py 改） | Task 4 Step 5 |
| §3.3 Gating（LOCAL_MODE） | Task 4 測試 + Task 10 Step 2 第 9 步 |
| §3.4 前端 stack（vendor Alpine、無 Tailwind） | Task 5 + Task 6 |
| §4.1 GET /dev/quotes/{symbol} | Task 1（service）+ Task 3（API） |
| §4.2 GET /dev/console | Task 4 |
| §4.3 StaticFiles | Task 4 Step 5 |
| §5.1 佈局 | Task 6 HTML |
| §5.2 各 section | Task 6（Health）/ Task 7（Symbols, Symbol Detail, Intent）/ Task 8（Dev Quote）/ Task 9（Quote Store） |
| §5.3 視覺化關鍵點 | Live preview Task 8 / Request log Task 6 / Error envelope render Task 6 CSS+HTML / 跨 section 跳轉 Task 9 |
| §5.4 X-Request-Id 行為（顯示 header） | Task 6 onwards（response status 區） |
| §5.5 樣式原則（深色、monospace） | Task 6 CSS |
| §6 資料流 | Task 6 `apiCall` 實作 |
| §7 錯誤處理 | Task 6 `apiCall` try/catch + localStorage try/catch |
| §8 安全考量 | Task 4 gating tests |
| §9 測試策略 | Task 1/3/4 backend tests; §9.3 由 Task 10 手動跑 |
| §11 銜接 V0.5 後續 work order | 架構已預留（nav 加按鈕即可），無新 task |
| §12 實作分批 | Task 1-9 對應 spec 7 批 |

**Placeholder scan**：plan 內未見 "TBD" / "TODO" / "implement later" 等紅旗。所有 step 含具體 code、檔案路徑、指令。

**Type consistency**：
- `apiCall` 與 entry 結構在 Task 6 定義（`status / requestId / requestBody / responseBody / errorCode / timestamp / method / path`），後續 Task 7-9 使用相同欄位名
- `devConsoleApp()` 全域於 Task 6 定義，Task 7-9 都在同一個 object 內加 method/state
- `QuoteSnapshot` 欄位名（`symbol / bid_price / ask_price / last_price / quote_time`）以 BE-V0.5-08 實際定義為準（Task 0 step 3 驗證，Task 1/3 註記若不同要對應調整）

**Unknown 依賴點**（已在 Task 0 標註）：
- `DevQuoteIngestService` 與 `InMemoryQuoteStore` 的實際模組路徑
- `QuoteSnapshot` 的欄位名與構造方式
- `tests/api/test_dev_quote_api.py` 內既有 fixture 名稱

這些都在 Task 0 step 3 找到後對應微調。
