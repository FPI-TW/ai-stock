# 使用者端報價看板（User Quote Board）— 設計文件

- **狀態**：Draft，待 implementation plan
- **目標版本**：V0.5（搭配既有 `InMemoryQuoteProvider`；V1 上 shioaji 後參數可調）
- **影響範圍**：新增 `GET /quotes` 端點；前端 `/test` 使用者模式新增報價看板區塊
- **作者**：Kashionz
- **日期**：2026-05-27

## 1. 背景與動機

目前使用者模式 dashboard 只顯示「我的委託」與「最近通知」兩欄。使用者要看自己關注標的的目前價，必須切到 Dev 模式手刻 `GET /symbols` 或用 `POST /dev/push-quote` 反推，體感差且偏離真實 end-user 視角。

後端的 `QuoteProvider` protocol 已經有 `get_quotes(symbols)` 可取最新 snapshot，但對外沒有任何讀取端點 — 只有 `POST /dev/push-quote`（寫入）。

本次要：
1. 後端開一條公開的批次讀報價端點，作為前端 polling 來源
2. 使用者 dashboard 新增「報價看板」，自選股形式、自動更新

## 2. Scope

### 包含
- 新後端端點 `GET /quotes?symbols=...`
- 前端使用者模式新增報價看板區塊（位於 `.uv-section-grid` 上方、橫跨兩欄）
- 自選股使用 `localStorage` per-user 儲存
- 3 秒輪詢，依使用者切換 / 模式切換 / 看板摺疊 / 分頁可見性 控制生命週期

### 不包含
- ❌ Server-side watchlist 表（V0.5 用 localStorage 即可；V1 視需求補）
- ❌ SSE / WebSocket（polling 對 in-memory provider 已足夠）
- ❌ 將 active intents 的 symbol 自動塞進 watchlist（user 明確要手動選）
- ❌ 即時通知氣泡 / 桌面通知
- ❌ 在委託卡上顯示「目前價 vs 目標價」（與 watchlist 設計分離）
- ❌ 歷史 K 線 / 圖表
- ❌ 漲跌幅以「昨收」為基準（V0.5 沒有 EOD/前收資料；以 session-local first-seen 為基準）

## 3. 後端設計

### 3.1 端點

```
GET /quotes?symbols=2330,2317,2454
```

- **Auth / Gate**：不掛 `LOCAL_MODE` gate（V1 公開時不用改 URL，前端不必判斷）
- **Owner scope**：不需要 `X-Local-User-Id`（報價非 owner-scoped 資料）

### 3.2 回應

```json
{
  "data": [
    {
      "symbol": "2330",
      "displayName": "台積電",
      "askPrice": "599.00",
      "bidPrice": "598.50",
      "lastPrice": "599.00",
      "quoteTime": "2026-05-27T01:30:00+00:00",
      "stale": false
    },
    {
      "symbol": "2317",
      "displayName": "鴻海",
      "askPrice": null,
      "bidPrice": null,
      "lastPrice": null,
      "quoteTime": null,
      "stale": true
    }
  ]
}
```

- **價格欄位**：`Decimal` 序列化成字串（與既有 `/trade-intents` 一致）
- **`stale: true`**：當 `QuoteProvider.get_quotes` 對該 symbol 無資料時回；avoid 單顆失敗炸掉整批
- **`displayName`**：從 `symbol_repository` 取，方便前端不必再打 `/symbols/{symbol}`

### 3.3 錯誤

| 條件 | HTTP | `error.code` |
|---|---|---|
| 沒帶 `symbols` 參數 | 400 | `MISSING_SYMBOLS` |
| `symbols` 超過 50 個 | 400 | `TOO_MANY_SYMBOLS` |
| 任一 symbol 不符既有 symbol code 格式 | 400 | `INVALID_SYMBOL` |
| 任一 symbol 在 symbol registry 不存在 | 400 | `INVALID_SYMBOL`（reuse 既有 validator） |

回應沿用既有 error envelope：`{ "error": { "code": ..., "message": ..., "details": {...}, "requestId": ... } }`。

### 3.4 實作要點

- **資料來源**：`request.app.state.quote_provider.get_quotes(symbols)`
- **symbol validation**：reuse `app.services.symbol`（與 `/trade-intents` 同一條 path）
- **displayName**：reuse `SymbolRepository.list_by_codes(...)` 一次查回，loop 組裝
- **`stale` 判斷**：`get_quotes` 回的 list 中，某 symbol 缺漏或 `last_price/ask_price/bid_price` 都 None → `stale = true`

## 4. 前端設計

### 4.1 版面

新區塊位於 `#user-view .uv-section-grid` **之上**、橫跨全寬：

```
┌─────────────────────────────────────────────────────────────────┐
│  報價看板                              重新整理 ↻   隱藏 ▼         │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────────────┐               │
│  │ 2330 │ │ 2317 │ │ 2454 │ │  + 加入自選         │               │
│  │ 台積電│ │ 鴻海 │ │ 聯發科│ │                   │               │
│  │599.00│ │205.50│ │ 1095 │ │                   │               │
│  │ ↑0.5%│ │ ↓0.2%│ │ ↑1.1%│ │                   │               │
│  └──────┘ └──────┘ └──────┘ └──────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 報價卡（quote-tile）

每張卡顯示：
- 代號（粗體）+ 中文名（次要色）
- **主價** = `lastPrice ?? askPrice ?? bidPrice ?? "—"`，大字
- 漲跌方向標 + 漲跌幅：
  - 基準價（baseline） = 第一次拿到的主價
  - 之後每次刷新算 `(current - baseline) / baseline`
  - 漲 → 紅 `↑`，跌 → 綠 `↓`，持平 → 灰 `─`（台股配色）
- `stale: true` → 卡片淺灰、價格顯示 `—`、底部小字 `等待行情`
- hover 右上角浮現 `×` 移除按鈕

### 4.3 加入卡片（add-tile）

永遠在最後一張；點開後 reuse 既有的 `searchSymbols` 流程（下單 sheet 用的同一條）。選中後：
1. push 到當前使用者的 watchlist
2. 寫回 localStorage
3. UI 立刻加上一張新卡，初始 `stale: true` loading
4. 下一輪 polling 結果到才顯示價格

### 4.4 摺疊 / 顯示

右上角 `隱藏 ▼` 切換摺疊；狀態存 `ai-stock-user-quote-board-collapsed`（per-user 不必，所有使用者共用此偏好）。摺起來時停止輪詢。

### 4.5 localStorage schema

```json
{
  "ai-stock-user-watchlist": {
    "alice": ["2330", "2317", "2454"],
    "bob": ["2330"],
    "default": []
  }
}
```

依當前 `getSelectedUser()` label 取對應陣列，與既有的 `ai-stock-test-selected-user` / `ai-stock-test-users` 模式一致。

### 4.6 空狀態

watchlist 為空時只顯示 add-tile + 一句 hint：「加入想關注的股票，看價格自動更新」。

## 5. 輪詢策略與生命週期

### 5.1 頻率
- **3 秒**一次
- V1 上 shioaji 後再視真實 tick 頻率（多半 100ms~1s batch）調整；調的是常數不是架構

### 5.2 觸發 / 停止

| 事件 | 動作 |
|---|---|
| 進入使用者模式且看板未摺疊 | 啟動 polling |
| 切到 Dev / 服務端模式 | 立即停止 |
| 看板摺疊 | 立即停止 |
| 切換使用者（alice→bob） | 停止舊輪詢、讀新 watchlist、立即拉一次再啟動新輪詢 |
| watchlist 為空 | 不發 request（省空查詢） |
| `document.visibilityState === "hidden"` | 暫停 |
| 回到前景 | 立即拉一次再恢復 |

### 5.3 請求取消

使用 `AbortController` 配 `sendRequest(signal)`（既有 wrapper 已支援），每輪 polling 取消上一輪未完成的請求，避免：
- 快速切使用者時舊 response 蓋掉新使用者畫面
- 同時跑兩個 polling timer

### 5.4 錯誤處理

| 錯誤 | 處理 |
|---|---|
| HTTP 5xx / network error | `console.warn`；卡片保留前一次價格；下緣多 `⚠ 連線中斷` 小字 |
| `400 INVALID_SYMBOL`（不應發生） | 從 watchlist 自動移除該 symbol + `console.warn` 原因 |
| 連續 3 次失敗 | 暫停 polling 30 秒後再試 |

### 5.5 非阻塞初始化

進入使用者模式時，看板**不 block** dashboard 主流程 — 委託列表、通知照舊立刻顯示；看板自己內部跑 loading skeleton。

## 6. 檔案規畫

### 新檔
- `src/app/api/routes/quotes.py` — `GET /quotes` route handler
- `src/app/schemas/quote.py` — `QuoteRead` / `QuoteListResponse` Pydantic schemas
- `src/app/static/test/quote-board.js` — 看板 render + polling + watchlist 管理
- `tests/api/test_quotes.py` — endpoint 單元測試

### 改檔
- `src/app/main.py` — mount 新 router
- `src/app/static/test/user-view.js` — mount 看板進 `#user-view`、掛 `onEnterUserMode` / `onLeaveUserMode` lifecycle
- `src/app/static/test/index.html` — 在 `.uv-section-grid` 上加 `<section id="uv-quote-board">` 容器
- `src/app/static/test/styles.css` — 新增 `.qb-*` 樣式（tile / add-tile / stale / direction arrow）
- `src/app/static/test/endpoints.js` — 新增 `GET /quotes` 條目供 Dev 模式測試
- `tests/test_test_page.py` — 補 `/test-assets/quote-board.js` 與 endpoints.js parity
- `docs/dev/test-page.md` — 「使用者模式」段補一句報價看板存在

## 7. 測試計畫

### 7.1 Backend 單元測試（`tests/api/test_quotes.py`）

1. `GET /quotes?symbols=2330` → 200，回單筆 valid snapshot（不 stale）
2. `GET /quotes?symbols=2330,2317` → 200，回兩筆，順序與 query 一致
3. `GET /quotes?symbols=2330,UNKNOWN` → 200，2330 fully populated、UNKNOWN 應該被 400 擋（INVALID_SYMBOL）
4. `GET /quotes?symbols=2330`（provider 無資料）→ 200，回 `stale: true` + null fields，不 500
5. `GET /quotes`（沒帶 `symbols`）→ 400 `MISSING_SYMBOLS`
6. 51 個 symbol → 400 `TOO_MANY_SYMBOLS`
7. 非法 symbol code 格式 → 400 `INVALID_SYMBOL`
8. `LOCAL_MODE=false` → 200（**故意不擋**）

### 7.2 Test-page parity（`tests/test_test_page.py`）

- `/test-assets/quote-board.js` 在 LOCAL_MODE 下回 200，false 回 404
- `endpoints.js` 中 `GET /quotes` 條目存在且 `implemented: true`，與 `/openapi.json` 對齊

### 7.3 Playwright UI smoke（`tests/ui/test_smoke.py`）

新增 1 case：`test_user_quote_board_polls_and_renders`
- 預塞 localStorage watchlist `["2330"]` 給 alice
- 進使用者模式，等 5 秒
- 斷言 `.qb-tile[data-symbol="2330"] .qb-price` 有非 `—` 的價格、卡片不是 `qb-tile-stale`

## 8. 安全 / 隱私

- `GET /quotes` 不需 auth：V0.5 LOCAL_MODE 已限定 loopback，V1 公開後報價本身不是敏感資料（多數券商 API 也是 public quote feed）
- 不存任何使用者隱私到 localStorage，僅 symbol code 列表
- 不引入新外部依賴

## 9. 開放問題

無。本設計與既有 `QuoteProvider` protocol、symbol validation、`sendRequest` AbortSignal、user switcher、模式 lifecycle 等 building block 完全相容。

## 10. Out of Scope（V1 再評估）

- Server-side watchlist 表（多裝置同步）
- WebSocket / SSE 推送（取代 polling）
- 在委託卡上顯示「目前價 vs 目標價」（與本案分開設計）
- 漲跌幅以「昨收」為基準（需 EOD 資料管線）
- 桌面通知 / 即時聲音
