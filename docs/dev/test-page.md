# `/test` — LOCAL_MODE 內建 API 測試頁

當 `LOCAL_MODE=true` 時，後端在 `GET /test` 提供一個單頁式的內建 API 測試控制台。
用途是供 PM / QA / FE 在交接與本地開發階段做快速的端對端冒煙測試，**不屬於正式環境的對外介面**。

## 快速上手

```bash
LOCAL_MODE=true QUOTE_PROVIDER=in_memory \
  DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock \
  uv run uvicorn app.main:app --app-dir src --reload
```

開啟 <http://127.0.0.1:8000/test>。

## 功能說明

頂部可在三種模式（tab role）間切換，每種模式針對不同使用情境：

### Dev 模式（預設）

工程師 / QA 直接操作 API 的控制台。

- **端點目錄**：列出後端已開放的所有 API（✅）以及在 `docs/orders/` 中規劃中的 API
  （📝 附工單編號，停用狀態）。點選任一列即可載入到請求建構器。
- **請求建構器**：可編輯 path params / query string / headers / JSON body 並按
  `Send`。每個端點都可透過 *Fill example* 下拉選單帶入預先準備好的範例 payload。
- **回應檢視器**：自動美化輸出 body、解析錯誤封套（`error.code` / `error.message`），
  並回顯伺服器發出的 `X-Request-Id`。最近 5 筆回應會留在歷史紀錄中，可重新送出。
- **示範流程**（下方面板）：三組預先腳本化的情境，串接多個 API 呼叫：
  1. *E2E：buy_price_alert 觸發* — 建立 intent、推送符合條件的 quote、輪詢通知。
  2. *E2E：limit_buy_order 觸發* — 同上，但改用 V0.5-15 策略與
     `partial_fill_allowed` 交易模式。
  3. *多使用者 owner-scoping* — alice 建立、bob 列出（看不到）、alice 列出（看得到自己的）。

  每個步驟都會把結果即時記錄在面板上，方便回放查看過程。

### 使用者模式

以終端使用者的視角操作，不直接暴露 raw API。

- **Dashboard**：兩欄式響應式 layout — 委託列表（左）+ 最近通知（右），sidebar
  sticky 對齊；切換 all / active / cancelled / triggered filter 時版位不會跑掉。
- **下單 sheet**：標的搜尋（內建 debounce）、quantityLots / targetPrice 輸入、
  策略卡選擇、自訂 confirm。送出時會自動帶上目前選定使用者的 `X-Local-User-Id`。
- **通知**：可直接從通知卡上標記已讀（呼叫 `POST /notifications/{id}/read`）。

### 服務端模式

模擬行情伺服器與時鐘的操作台，用於示範觸發行為。

- **推假行情**（`POST /dev/push-quote`）：選標的、選 ask / bid / last、填價格。
  Dispatcher 同步派發後會跨所有測試使用者聚合通知計數；若零觸發會自動診斷常見
  原因（盤外、推錯側、價格不符等）。
- **時鐘控制**（`POST /dev/set-clock`）：凍結 / 推進 / 重設，並顯示 `🧊` 與
  盤中 / 盤外狀態（由 `withinRegularSession` 推得）。
- **重評估委託**（`POST /dev/evaluate-quotes`）：用既有最後 quote 重新評估，
  用於補測既存 active 委託是否漏觸發。
- **Server 狀態**：每 5 秒輪詢 `/dev/server-state`，顯示 PID、provider、訂閱數。

### Topbar（共用）

- **使用者卡 / 切換選單**：可在 `default`、`alice`、`bob`、`charlie` 之間切換。
  選擇非預設身分時，每個請求都會帶上 `X-Local-User-Id`，後端會以該使用者身分處理
  ─ 用來示範 owner scoping，不必先架真正的 auth（auth 於 BE-V1-01 補上）。
- **健康指示燈**：每 5 秒輪詢 `/health`，DB 異常時轉紅。
- **時鐘膠囊**：顯示 Asia/Taipei 當前時間 + `🧊` 凍結指示。
- **流程圖捷徑**：開新分頁進 `/test/flows`。

## 架構說明

- **測試器不需 build 步驟**：純 HTML + ES modules + 純 CSS，透過 `StaticFiles`
  從 `src/app/static/test/` 提供。主控台 `/test` 完全可離線使用；`/test/flows`
  則從釘住版本並帶 SRI 的 CDN 載入 Mermaid，因為流程圖渲染器只用在文件用途。
- **端點目錄硬編在 `endpoints.js`**：當規劃中的端點實裝時，請在同一個 PR 內把
  該條目的 `implemented: false` 翻成 `true`；這個翻動本身就當作對應工單的 UI
  驗收檢查。
- **目錄漂移偵測**：頁面載入時會 fetch `/openapi.json`，若任何 `implemented: true`
  的條目 method / path 不在 spec 內會以 `console.warn` 提示。本頁面**不會**從
  OpenAPI 自動生成表單（discriminated unions 渲染效果不佳）— 偏好用 examples 取代。
- **行程級全域狀態**：`/dev/set-clock` 會變更掛在 `app.state` 上的
  `TradingSessionService` instance，因此 API path、lifespan dispatcher 與
  `/dev/server-state` 都會看到一致的時鐘。一個 uvicorn worker 對應一個 instance ─
  LOCAL_MODE 只開 1 個 worker，此限制可以接受。`/dev/server-state` 會回報
  `workerPid` 讓這件事可見。

## 安全

兩道獨立的關卡確保這個頁面不會進到正式環境：

| 介面 | LOCAL_MODE=true | LOCAL_MODE=false |
|---|---|---|
| `GET /test` | 200 + HTML | 404 |
| `GET /test-assets/*` | 200 + 資源 | 404 |
| `POST /dev/*` | 200 | 404 |
| `X-Local-User-Id` header | 解析並覆寫身分 | **靜默忽略** |
| 時鐘覆寫 | `/dev/set-clock` 會變更 | 一律使用系統時間 |

在 production 模式下，header 會被靜默忽略，因此公開部署不會被人塞 header 假冒
使用者；`tests/test_deps_current_user.py` 覆蓋了這條回歸。

**請勿**在非 loopback 介面上以 `LOCAL_MODE=true` 對外暴露這個服務。Dev 介面
（`/dev/push-quote`、`/dev/set-clock`、`/dev/evaluate-quotes`）設計上未授權，
任何人都可以偽造報價、凍結時間、觸發委託。

## 新增一個端點到目錄

1. 在 `src/app/static/test/endpoints.js` 對應的分組中新增一筆條目（必要時可
   新建一個分組）。
2. 已實作的端點：將 `implemented` 設為 `true`，並可選擇性地提供 `examples`
   （label → request body 的物件）。
3. 規劃中的端點：將 `implemented` 設為 `false`，並補上 `ticket: "BE-V1-..."`。
4. 路徑參數使用 `{name}` 語法；UI 會從你貼到 *Path params* 欄位的 JSON 物件
   代入對應的值。

## 檔案

- `src/app/static/test/index.html` — 頁面 markup。
- `src/app/static/test/app.js` — Dev 模式、endpoint render、request/response、
  示範流程、topbar 共用元件，以及 `sendRequest` 共用 fetch wrapper（可選
  `AbortSignal`）。
- `src/app/static/test/user-view.js` — 使用者視角 dashboard / 下單 sheet。
- `src/app/static/test/service-view.js` — 服務端推假行情、時鐘、評估控制台。
- `src/app/static/test/flows.html` — 系統流程圖與列印版。
- `src/app/static/test/styles.css` — 淺色主題、grid layout。
- `src/app/static/test/endpoints.js` — 端點目錄。
- `src/app/api/routes/test_page.py` — 提供 `index.html` 與 `flows.html`。
- `src/app/api/routes/dev.py` — `/dev/push-quote`、`/dev/set-clock`、
  `/dev/server-state`（本頁面依賴的後端管線）。

## 自動化覆蓋

兩層測試守住這個頁面：

- `tests/test_test_page.py` — backend smoke：
  - `/test`、`/test/flows`、`/test-assets/*` 在 LOCAL_MODE 下回 200；
    LOCAL_MODE=false 一律 404（含 `service-view.js`）。
  - `endpoints.js` 中 `implemented: true` 的 method+path 與 `/openapi.json`
    完全對齊；無重複條目（catalog 漂移 / 重複防護）。
  - `flows.html` 釘住 `https://cdn.jsdelivr.net/npm/mermaid@11.<patch>` +
    `integrity="sha384-..." crossorigin="anonymous"`（CDN supply chain 防護）。

- `tests/ui/test_smoke.py` — Playwright 端對端 smoke：
  由 session fixture 啟動真實的 uvicorn 子行程接本地 PostgreSQL，再由 chromium
  對 `/test` 走 smoke flow。預設標 `ui` marker 並排除（pytest addopts
  `-m 'not integration and not ui'`）。觸發方式：
  ```bash
  uv run playwright install chromium   # 一次性
  make test-ui                          # 全部 UI smoke
  uv run pytest -m ui tests/ui/test_smoke.py::test_<name>  # 單一 case
  SKIP=pytest-ui git push               # 缺 prereq 時暫過 pre-push
  ```
  pre-push hook 會跑 `make pre-push-check`（= `test` + `test-ui`）。
