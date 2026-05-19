# Dev Console: 視覺化測試介面

> **2026-05-19 更新**：dev console 前端已抽離成獨立專案 [`ai-stock-dev-console`](../../../../../ai-stock-dev-console)（sibling 目錄）。
> 本 spec 的 §5（UI 佈局）與相關前端設計記錄此次規劃，**仍然是 dev console UI 的設計依據**；
> 但 §3（檔案配置 / FastAPI 整合）的「console route + StaticFiles mount」部分已從 ai-stock 移除。
> ai-stock 保留的部分：`GET /dev/quotes/{symbol}` endpoint、`QUOTE_NOT_FOUND` error code、`DevQuoteIngestService.get_snapshot()`，
> 並在 `LOCAL_MODE` block 新增 `CORSMiddleware` 讓 standalone dev console 跨 origin 呼叫。

- 狀態：Draft
- 撰寫日期：2026-05-19
- 適用版本：V0.5（LOCAL_MODE 開發階段）
- 相關 work order：BE-V0.5-08（PR #7，提供 `POST /dev/quotes` 與 `InMemoryQuoteStore`）
- 後續銜接：BE-V0.5-09~12（quote 評估、trigger、notification dispatch）

> **實作前置條件**：BE-V0.5-08（PR #7）必須先 merge 進 main。本 spec 假設 `POST /dev/quotes`、`InMemoryQuoteStore`、`DevQuoteIngestService`、`schemas/dev_quote.py` 已存在。實作分支應 base 在 BE-V0.5-08 merge 後的 main。

## 1. 動機

ai-stock V0.5 階段的本地測試目前依賴 Swagger UI 與 curl，在以下情境體驗不佳：

1. `POST /dev/quotes` 規定 `Decimal` 必須以 string 傳遞、`quoteTime` 必須帶時區，Swagger 預設行為不會提醒，容易誤觸 400。
2. In-memory quote store 沒有對外查詢方式，灌完 quote 後無法「看見」目前狀態，只能透過後續 evaluator 行為間接推測。
3. Swagger UI 沒有 request 歷史，重整即消失；錯誤排查時要重新組 payload。
4. 無法把 `lookup symbol → upsert quote → create intent` 串成可視化流程，理解整體行為要在多個面板之間切換。

Dev Console 的目的是提供一支**本地專用、零建置、可直接看見系統狀態**的視覺化介面，補上 Swagger UI 的不足。

## 2. 範圍

### In scope（V0.5）

- 一支單檔 HTML（含 vendor 進來的 Alpine.js 與手寫 CSS），由 FastAPI 在 `LOCAL_MODE=true` 時透過 `GET /dev/console` 提供。
- 涵蓋既有 5 個 endpoint 的表單：`/health`、`/symbols`、`/symbols/{symbol}`、`/intents`、`/dev/quotes`。
- 新增一支 `GET /dev/quotes/{symbol}` endpoint，供 console 顯示 in-memory store 的目前 snapshot。
- Request / response 歷史保存（localStorage，最多 50 筆）。
- 錯誤 envelope（`code / message / details / requestId`）格式化呈現。
- Dev Quote 表單的 live JSON preview，凸顯 camelCase 對應與字串型別。

### Out of scope（V0.5 不做）

| 項目 | 原因 |
|---|---|
| Scenario runner（自動串多步驟） | 等 BE-V0.5-09 evaluator endpoint 上線再規劃 |
| Auth header 設定 UI | V0.5 沒有 auth，等 V1 再加 |
| SSE / WebSocket 即時推送 | 後端目前無此 capability |
| 客戶端 schema validation | 仰賴後端錯誤 envelope 即可，不重複實作 |
| 美術風格化 / 多主題 | 此頁面是 dev tool，可用即可 |
| 多語系 | 僅本地開發者使用，中文介面即可 |

## 3. 系統架構

### 3.1 檔案配置

```
src/app/
├── api/routes/
│   ├── dev_console.py            # 新：GET /dev/console
│   └── dev_quotes.py             # 既有 + 加 GET /dev/quotes/{symbol}
├── schemas/
│   └── dev_quote.py              # 既有 + 加 DevQuoteFetchResponse
├── services/
│   └── (既有 DevQuoteIngestService 不動，可能新增 query 方法或重用 store)
└── static/
    ├── dev_console.html          # 新：主頁面
    ├── dev_console.css           # 新：手寫樣式（~150 行）
    ├── dev_console.js            # 新：Alpine.js components 與 fetch 封裝
    └── vendor/
        └── alpine.min.js         # 新：vendor 進 repo（~15KB）
```

### 3.2 FastAPI 整合

修改 `src/app/main.py`，在既有 `if settings.local_mode:` 區塊加入 console router。靜態檔案改用 `StaticFiles` 掛在 `/dev/console/static`，方便 HTML 引用 CSS/JS/vendor。

```python
if settings.local_mode:
    from fastapi.staticfiles import StaticFiles
    from app.api.routes.dev_console import router as dev_console_router
    from app.api.routes.dev_quotes import router as dev_quotes_router

    app.include_router(dev_quotes_router, prefix="/dev", tags=["dev"])
    app.include_router(dev_console_router, prefix="/dev", tags=["dev"])
    app.mount(
        "/dev/console/static",
        StaticFiles(directory="src/app/static"),
        name="dev_console_static",
    )
```

`dev_console.py` 僅一個路由，使用 `FileResponse` 並以模組路徑解析絕對位置（避免 uvicorn 啟動 cwd 不同造成路徑錯誤）：

```python
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

@router.get("/console", include_in_schema=False)
def serve_console() -> FileResponse:
    return FileResponse(_STATIC_DIR / "dev_console.html", media_type="text/html")
```

`main.py` 中 `StaticFiles(directory=...)` 也用同樣的 `Path(__file__)` 解析方式，不依賴 cwd。

### 3.3 Gating 行為

- `LOCAL_MODE=true`：`/dev/console` 與 `/dev/quotes/*` 都掛載。
- `LOCAL_MODE=false`：router 與 StaticFiles mount 完全不註冊 → 訪問回 404。
- 與既有 `POST /dev/quotes` 同一條 gating，不引入新環境變數。
- 此頁面與後端共用 origin，無 CORS 議題。

### 3.4 前端 stack 選擇

| 選項 | 採用 | 理由 |
|---|---|---|
| Alpine.js 3.x（vendor） | ✅ | Form state、reactivity、條件渲染夠用；無建置；~15KB |
| Tailwind | ❌ | CDN 巨大或要 build；手寫 CSS 完全夠這頁規模 |
| React/Vue | ❌ | 引入 toolchain 不符 V0.5 範圍 |
| HTMX | ❌ | 後端需配合回 partial HTML，違背 JSON API 一致性 |
| 純 vanilla JS | ❌ | 表單與 reactive state 多到手寫會雜亂 |

**Vendor Alpine.js 進 repo**（不用 CDN）的理由：保證離線可用、避免外部資源異動、無 SRI 維護負擔。檔案 ~15KB 合理。

## 4. 後端變更

### 4.1 新增 `GET /dev/quotes/{symbol}`

**目的**：讓 console 能查 in-memory store 的目前 snapshot，是「視覺化」的關鍵 — 灌完 quote 要能立刻看見。

**契約**：

- Method / Path：`GET /dev/quotes/{symbol}`
- LOCAL_MODE 限定（與 `POST /dev/quotes` 同 router，同樣 gating）
- Path param：`symbol`（1..20 chars）
- 回傳成功：`200`，body：

  ```json
  {
    "data": {
      "symbol": "2330",
      "bidPrice": "590.0000",
      "askPrice": "591.0000",
      "lastPrice": "590.5000",
      "quoteTime": "2026-05-19T02:14:33+00:00"
    }
  }
  ```

- 查無：`404`，標準 error envelope；需在 `app/api/errors.py::ErrorCode` 新增 `QUOTE_NOT_FOUND`（現有 enum 沒對應項）
- Decimal 序列化以 string，與 `POST /dev/quotes` 對稱
- `quoteTime` 以 ISO 8601 含時區序列化

**實作**：
- 在 `dev_quotes.py` 既有 router 加入 `GET /quotes/{symbol}`
- 不另起新 service；在 `DevQuoteIngestService` 加 `get_snapshot(symbol: str) -> QuoteSnapshot | None` 方法，內部讀同一個 `InMemoryQuoteStore`
- 在 `app/api/deps.py` 重用既有的 `DevQuoteIngestServiceDep`

**Schema**：新增 `DevQuoteFetchResponse` / `DevQuoteFetchResponseData`，欄位序列化 alias 同 upsert（camelCase）。

**測試**：

- 灌入後可查到，欄位值與序列化格式正確
- 查無回 404，error envelope 結構正確
- LOCAL_MODE=false 時整條路由不存在（沿用既有測試 pattern）
- Decimal 序列化為 string、不是 number

### 4.2 新增 `GET /dev/console`

**目的**：回 HTML 主頁。

**契約**：

- LOCAL_MODE 限定
- 回 `text/html`
- 不出現在 OpenAPI（`include_in_schema=False`）

**測試**：

- LOCAL_MODE=true：回 200，content-type `text/html`
- LOCAL_MODE=false：回 404

### 4.3 靜態檔案掛載

- 路徑 `/dev/console/static` 對應 `src/app/static/`
- 僅 LOCAL_MODE
- 不在 OpenAPI 顯示

## 5. UI 設計

### 5.1 整體佈局

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ai-stock dev console     env: local · user: 0000…0001 · LOCAL_MODE       │
├──────────────────┬───────────────────────────────────────────────────────┤
│ Sections         │                                                       │
│ ┌──────────────┐ │   [選中的 section content]                            │
│ │ Health       │ │                                                       │
│ │ Symbols      │ │                                                       │
│ │ Symbol Detail│ │                                                       │
│ │ Intent       │ │                                                       │
│ │ Dev Quote  ● │ │                                                       │
│ │ Quote Store  │ │                                                       │
│ └──────────────┘ │                                                       │
├──────────────────┴───────────────────────────────────────────────────────┤
│ Request log (last 50)                                       [clear]      │
│ 10:21:03  POST /dev/quotes        200  req-id 7c9e…           ▶ expand   │
│ 10:20:58  GET  /symbols/2330      200  req-id 4a1b…           ▶          │
│ 10:20:41  POST /intents           400  req-id 1f02…  ❗INVALID_AMOUNT ▶  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 5.2 各 Section 內容

| Section | Endpoint | 重點互動 |
|---|---|---|
| Health | `GET /health` | 「Check」按鈕；顯示 service / version / env / database 欄位 |
| Symbols | `GET /symbols?q=&limit=` | `q` 與 `limit` 欄位 → 結果表格（symbol、displayName、market、instrumentType、tradableStatus） |
| Symbol Detail | `GET /symbols/{symbol}` | symbol 欄位 → 結果 card |
| Intent | `POST /intents` | symbol / side（buy/sell radio）/ quantity；submit |
| Dev Quote | `POST /dev/quotes` | symbol / bidPrice / askPrice / lastPrice / quoteTime（含「Now (UTC)」按鈕）；下方 live JSON preview；成功後出現「→ 查 store」按鈕 |
| Quote Store | `GET /dev/quotes/{symbol}` | symbol 欄位；側邊列出最近 POST 過的 symbol（從 request log 抓） |

### 5.3 視覺化關鍵點

1. **Live JSON preview**（Dev Quote section）
   - 在 form 下方即時顯示 `JSON.stringify(payload, null, 2)`
   - 凸顯 camelCase 對應與 string 型別
   - 避免使用者把 Decimal 打成 number

2. **`Now (UTC)` 按鈕**
   - 點下去填入 `new Date().toISOString()`，產生形如 `2026-05-19T02:14:33.123Z`
   - 後端 `AwareDatetime` 接受此格式

3. **Request log**
   - Sticky 底部 panel，預設摺疊單列
   - 每筆 entry：timestamp（HH:mm:ss）、method、path、status、`X-Request-Id` 縮寫、（若錯誤）`error.code`
   - 點 expand 顯示：request body（JSON）、response body（JSON）、完整 `X-Request-Id`、status code
   - 存 localStorage（key `ai-stock-dev-console-log`），上限 50 筆，FIFO 淘汰
   - 「Clear」按鈕清空

4. **Error envelope 渲染**
   - 偵測 response body 為 `{ "error": { "code", "message", "details", "requestId" } }` 結構時：
     - `code` 紅字、放大
     - `message` 加粗
     - `details` 用 monospace 縮排
     - `requestId` 顯示於旁，可一鍵複製
   - 非錯誤情況用一般 JSON 呈現

5. **跨 section 跳轉**
   - `POST /dev/quotes` 成功後在 response panel 顯示「→ 查 store」按鈕
   - 按下後切到 Quote Store section，自動填 symbol 並觸發查詢

### 5.4 X-Request-Id 行為

- Console 每次發送 request 預設**不**自動填 `X-Request-Id`，讓後端產（這樣才能驗證後端 id 行為）
- 提供一個全域 toggle 「Send custom X-Request-Id」勾選後 form 旁出現欄位
- 後端回的 `X-Request-Id` 永遠顯示在 response panel 與 log

### 5.5 樣式原則

- 深色背景（避免長時間使用刺眼）
- Monospace 為主（JSON、code、id）
- 緊湊密度（不浪費螢幕）
- 不引入圖標庫，必要符號用 unicode（▶、●、❗）

## 6. 資料流

### 6.1 一般 request 流程

```
使用者填表單
   ↓
Alpine.js 蒐集 form state
   ↓
fetch(url, { method, headers, body }) 
   ↓
解析 response：嘗試 JSON.parse → 否則 text
   ↓
寫入 request log（包含 timestamp、status、X-Request-Id、payload）
   ↓
渲染 response panel（成功 / 錯誤 envelope 不同樣式）
   ↓
（若 POST /dev/quotes 成功）顯示「→ 查 store」捷徑
```

### 6.2 LocalStorage 結構

```ts
type LogEntry = {
  timestamp: string;        // ISO 8601
  method: 'GET' | 'POST';
  path: string;             // e.g. "/dev/quotes"
  status: number;
  requestId: string | null; // 來自 response header
  requestBody: unknown | null;
  responseBody: unknown | null;
  errorCode: string | null; // 若是 error envelope，抽出 code
};
type LogStore = LogEntry[];  // 最多 50 筆
```

## 7. 錯誤處理

| 情境 | 行為 |
|---|---|
| Network 失敗（後端未啟） | Response panel 顯示連線錯誤；log 記錄為 status=0 |
| Response 非 JSON | Panel 顯示原始 text，log responseBody 存 text |
| Response 是 error envelope | 格式化呈現（見 §5.3） |
| LocalStorage quota 超出 | catch 後跳過寫入，console.warn |
| Alpine.js 載入失敗（vendor 路徑錯） | 顯示 fallback 純 HTML 訊息，提示檢查 static mount |

## 8. 安全考量

- 完全 LOCAL_MODE gating；正式環境 router 不註冊。
- 此頁面預期在 `localhost` 使用，沒有對外暴露。
- 不引入 CDN 第三方資源，避免 supply chain 風險。
- 不接受任何 user-supplied URL（fetch 目標固定為 same-origin 已知路由）。
- LocalStorage 內容僅本機資料，不含敏感資訊。

## 9. 測試策略

### 9.1 後端

| 測試對象 | 覆蓋 |
|---|---|
| `GET /dev/quotes/{symbol}`（有資料） | 200、payload 結構、Decimal 為 string、quoteTime ISO 8601 含時區 |
| `GET /dev/quotes/{symbol}`（無資料） | 404、error envelope 結構 |
| `GET /dev/console` LOCAL_MODE=true | 200、`text/html` |
| `GET /dev/console` LOCAL_MODE=false | 404 |
| StaticFiles 掛載 LOCAL_MODE=false | 404 |
| In-memory store 隔離 | 既有 multi-symbol isolation 測試已覆蓋 |

### 9.2 前端

- 不引入瀏覽器自動化測試（成本高於收益）
- 手動 smoke 流程見下方 §9.3
- HTML / JS 不受 ruff / mypy 管轄；保持單檔小規模，code review 時人工檢視
- 為避免 vendor 的 `alpine.min.js` 觸發 lint，將 `src/app/static/vendor/` 加入 `.gitattributes`（標記為 `linguist-vendored`）並排除於任何 lint glob

### 9.3 Smoke 流程（手動）

1. `make dev` 啟動後端（預設 `http://127.0.0.1:8000`）
2. 開 `http://127.0.0.1:8000/dev/console`
3. 在 Health section 按 Check → 應看到 `database: ok`
4. 在 Symbols section 查詢 → 看到表格
5. 在 Dev Quote section 填 `2330`、`590` 三筆價、按 Now → 觀察 JSON preview → Send → 看到 200
6. 點「→ 查 store」→ 切到 Quote Store → 看到剛灌的 snapshot
7. 在 Intent section 故意送 quantity=0 → 看到 error envelope 格式化呈現
8. 重整頁面 → request log 仍在
9. 設 `LOCAL_MODE=false` 重啟 → `/dev/console` 回 404

## 10. 風險與決策紀錄

| 決策 | 替代方案 | 採用理由 |
|---|---|---|
| 單檔 HTML + Alpine.js | React/Vue + Vite | 零建置、不擴大 toolchain；V0.5 需求簡單 |
| Vendor Alpine.js | CDN | 離線可用、無 supply chain 風險 |
| 手寫 CSS | Tailwind CDN / vendor | 規模小（~150 行）；不引入大檔案 |
| 新增 `GET /dev/quotes/{symbol}` | 從 service 直接 inject 給 HTML | API 一致性、可被其他 dev 工具重用 |
| LocalStorage 存 log | 後端持久化 | 純 client 行為、避免後端負擔 |
| 預設不自動填 X-Request-Id | 自動填 | 讓開發者觀察後端原生產 id 的行為 |

## 11. 銜接 V0.5 後續 work order

- **BE-V0.5-09（quote 評估）**：新增「Evaluator」section，提供觸發評估的按鈕；結果顯示 trigger record
- **BE-V0.5-10/11（trigger / notification）**：新增「Triggers」、「Notifications」section，列出最近紀錄
- 主頁面架構（左側 nav + 中央 panel + 底部 log）已可容納，加 section 為純加法

## 12. 實作分批建議（給 writing-plans 參考）

依照可獨立 review、可獨立 ship 的原則：

1. **Backend：`GET /dev/quotes/{symbol}` 與測試** — 後續 console 依賴此 endpoint，必須先到位
2. **Backend：`GET /dev/console` 與 StaticFiles 掛載** — 純骨架，回靜態檔
3. **Frontend：HTML 基底（佈局、Alpine.js vendor、CSS）+ Health section** — 最小可驗證骨架
4. **Frontend：Symbols / Symbol Detail / Intent 三個 section**
5. **Frontend：Dev Quote section（含 live preview、Now 按鈕）**
6. **Frontend：Quote Store section + 跨 section 跳轉**
7. **Frontend：Request log（localStorage、expand、error envelope 格式化）**

每批通過 `make check` 後 commit。

## 13. 開放問題（要在實作前釐清）

無 — 設計已收斂。若實作中發現新疑問再回頭更新此 spec。
