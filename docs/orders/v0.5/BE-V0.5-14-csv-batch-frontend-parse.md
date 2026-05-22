# BE-V0.5-14：CSV 前端解析 + 後端 Batch Create（無 Preview/Draft 層）

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：12h
- 依賴：BE-V0.5-07, BE-V0.5-13
- 交付版本：V0.5

## 背景

V1-10 規劃了完整 CSV preview → draft → confirm 流程：前端解析後呼叫後端 preview，後端做一次完整 validation + 產生 draft，user 確認後 confirm 再重 validate 寫入。那是 V1 完整版需要的（含 corporate action snapshot、idempotency table、CSV_PREVIEW_MISMATCH 等）。

V0.5 demo 階段不值得做這層：

- V0.5 尚未有 corporate action snapshot（BE-V1-09 才補），preview 與 confirm 之間的 effective price 不會變動。
- V0.5 沒有正式 auth、admin、CSV history 90 天等需求。
- BE-V0.5-13 的 Shioaji demo 配額僅 5 檔，CSV 一次能建立的有意義筆數本來就少，不需要 100 筆的 batch 設計。
- V0.5 BE-V0.5-07 已經有 `CreateTradeIntent` 完整 validation chain（symbol / tick / session / duplicate / subscription quota）。

因此 V0.5 的 CSV 採最小組合：**前端解析 → 後端單一 batch endpoint 在一個 transaction 內 loop 既有 `CreateTradeIntent` command → 全成功才 commit**。不另寫 preview / draft / 重 validate 層；任何 row error 直接 rollback 整批，per-row error 沿用 single create 的 error code。

V1-10 在本工單之上補：

- Preview endpoint + draft TTL。
- Confirm 重 validate。
- Idempotency key + 24h 保留（BE-V1-16）。
- Corporate action effective price preview（BE-V1-09）。
- Bracket template（BE-V1-08）。
- Batch limit 拉高到 100。

## 目標

- 新增 `POST /trade-intents/batch` endpoint。
- Body 接受前端解析後的 normalized rows（buy/sell price alert 範本，與 single create 同欄位）。
- 後端在同一 DB transaction 內 loop 既有 `CreateTradeIntent`。
- 任一 row 失敗 → 整批 rollback，回傳 row-level error。
- 全成功 → commit，回傳建立的 intent ids（依 row 順序）。
- 沿用 BE-V0.5-13 subscription quota 行為：batch 後總訂閱數會 > 5 → batch 直接拒絕，不留下任何 row。
- 不新增 DB table；不寫 `batch_import_id` / `source_row_number`（這兩個欄位在 V1-10 才正式啟用）。
- 不寫 idempotency key 機制；同樣 payload 重 POST 兩次會建兩批（V0.5 接受，V1-16 才補）。

## 非目標

- 不做 preview / draft / TTL（V1-10）。
- 不做 idempotency key（V1-16）。
- 不做 corporate action snapshot 套用（V1-09）。
- 不做 bracket / take_profit / stop_loss CSV row（V1-07 / V1-08）。
- 不做 CSV history / list API（V1-10 補 `GET /trade-intents/csv/batches`）。
- 不做後端 CSV 檔案上傳 / parsing；前端負責讀檔、欄位 trim、空白移除、欄位 mapping，後端只收 JSON array。
- 不做 row hash / file name 保存。
- 不做 batch 100 row 上限；V0.5 batch 上限 20 row（理由見下）。

## API

### `POST /trade-intents/batch`

Auth: V0.5 local user context（同既有 single create）。

Body：

```json
{
  "rows": [
    {
      "symbol": "2330",
      "strategy": "buy_price_alert",
      "quantityLots": 1,
      "targetPrice": "600.0"
    },
    {
      "symbol": "2317",
      "strategy": "sell_price_alert",
      "quantityLots": 2,
      "targetPrice": "200.0"
    }
  ]
}
```

欄位語意：

- `symbol` / `strategy` / `quantityLots` / `targetPrice`：與 BE-V0.5-07 single create body 完全一致。
- 不接受 `tradingDate`、`status` 等由後端推導的欄位（同 single create）。
- `strategy` 只接受 `buy_price_alert` / `sell_price_alert`（V0.5 範圍）。

Rules：

- `rows` 長度 1..20。超出回 `CSV_BATCH_LIMIT_EXCEEDED` 422。
- 對每 row 走一次與 `POST /trade-intents` 完全相同的 validation + create：
  - Symbol exists / instrument supported（BE-V0.5-04）。
  - Tick-size（BE-V0.5-05）。
  - Trading session / day intent rules（BE-V0.5-06）。
  - Duplicate check（BE-V0.5-07，含與既有 active 比對 + 與同 batch 內已成功 row 比對）。
  - Shioaji demo allowlist + subscription quota（BE-V0.5-13）。
- 同一 transaction 內依序執行；任一 row 失敗整批 rollback。
- 訂閱配額檢查時機：每 row 經過 `CreateTradeIntent` 自然觸發 reconcile；若第 N row 觸發 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`，整批 rollback，已成功 row 不會留下。
- 即時觸發（盤中 + condition 已成立）行為與 single create 一致：建立後立即進入 trigger transaction。V0.5 接受同 batch 內可能有部分 row 立即 triggered、部分仍 active 的混合結果。

Response（全成功 200）：

```json
{
  "data": {
    "createdRows": 2,
    "rows": [
      {
        "rowNumber": 1,
        "tradeIntentId": "...",
        "status": "active"
      },
      {
        "rowNumber": 2,
        "tradeIntentId": "...",
        "status": "scheduled"
      }
    ]
  }
}
```

Response（任一 row 失敗 422 / 409 / 視首個失敗 row 的 HTTP status）：

```json
{
  "error": {
    "code": "BATCH_ROW_REJECTED",
    "message": "批次中有資料不合法，整批未建立",
    "details": {
      "firstFailedRowNumber": 3,
      "rows": [
        { "rowNumber": 1, "status": "valid" },
        { "rowNumber": 2, "status": "valid" },
        {
          "rowNumber": 3,
          "status": "invalid",
          "errors": [
            { "field": "targetPrice", "code": "INVALID_TICK_SIZE", "message": "目標價不符合最小升降單位", "details": { "nearestPrices": ["598.0", "599.0"] } }
          ]
        }
      ]
    },
    "requestId": "..."
  }
}
```

設計要點：

- HTTP status 取**首個失敗 row 的 single create HTTP status**；envelope `code = BATCH_ROW_REJECTED` 統一給前端做 row-level UI。
- `details.rows` 對每個已 evaluate 過的 row 都回（valid / invalid）；未 evaluate 到的 row（首失敗之後 short-circuit）不需要含於 array。為簡化，V0.5 允許**遇第一個失敗即停止 evaluate 後續 row**，不要求把全部 row 都評估完才回。理由：前端對首次失敗即停在該 row UI 提示已足夠，V1-10 才需要完整 per-row preview。
- Row-level error format 與 V1-10 一致（rowNumber / field / code / message / details），讓 V1-10 接手時前端不必改格式。

### 既有 `POST /trade-intents`

- 不動。
- 內部把 single create 的 command 包成 `CreateTradeIntent` 共用 callable；batch endpoint 直接呼叫同一個 callable per row。

## Subscription Quota 與 BE-V0.5-13 互動

- 同 batch 內若新增 symbols 會讓總訂閱 > 5 → 走既有 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED` 路徑，整批 rollback。
- 同 batch 內若 row symbol 不在 Shioaji demo allowlist → 走既有 `SYMBOL_NOT_AVAILABLE_IN_DEMO` 路徑，整批 rollback。
- 不在本工單為 batch 加 pre-flight quota 計算（避免兩條路徑）；單一 row 觸發拒絕即可。

## Batch 上限

- 上限 20 row。
- 理由：
  - BE-V0.5-13 subscription quota 為 5 檔，超過必失敗。
  - 同一交易日同 symbol 重複 row 也會被 duplicate check 擋。
  - 20 row 足以涵蓋 demo 場景（5 檔 × 4 種價格情境），又能在單 transaction 內快速完成。
- V1-10 上限拉到 100。

## Error Codes

新增：

- `BATCH_ROW_REJECTED`：422 / 409（沿用首個失敗 row 的 status；envelope code 固定為此）。
- `CSV_BATCH_LIMIT_EXCEEDED`：422（沿用 V1-10 同名 code，V1 不需重新定義）。

既有 codes（沿用 BE-V0.5-07 / 13，row-level 出現於 `details.rows[].errors[].code`）：

- `UNKNOWN_SYMBOL`
- `UNSUPPORTED_INSTRUMENT`
- `INVALID_TICK_SIZE`
- `DUPLICATE_INTENT`
- `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`
- `SYMBOL_NOT_AVAILABLE_IN_DEMO`
- `VALIDATION_ERROR`

## 驗收條件

- [ ] `POST /trade-intents/batch` 對 2 row 全成功 → DB 有兩筆 intent + 適當 status。
- [ ] 任一 row validation 失敗 → DB 無任何新 intent（rollback 完整）。
- [ ] 首個失敗 row 之前的 row evaluation 結果列於 `details.rows`。
- [ ] Row 數 > 20 → `CSV_BATCH_LIMIT_EXCEEDED`。
- [ ] Row 數 = 0 → `VALIDATION_ERROR`。
- [ ] 同 batch 內兩 row 條件相同 → 第二 row 拋 `DUPLICATE_INTENT`，整批拒絕。
- [ ] 同 batch 內新增 symbol 觸發 > 5 訂閱 → 整批拒絕，配額不殘留。
- [ ] Batch 內條件已立即成立 row → 同 transaction 完成 trigger，response 顯示 `status = triggered`。
- [ ] 既有 single create 的所有 integration tests 不受影響。
- [ ] Endpoint 也走 BE-V0.5-03 local user owner scope，與 single create 一致。

## 測試要求

- API test：2 row 全 valid → 200，DB 兩筆 intent。
- API test：第 2 row tick 不合法 → 422 `BATCH_ROW_REJECTED`，DB 0 筆。
- API test：第 3 row unknown symbol → 整批 rollback，response details 包含 row 1/2 valid + row 3 invalid。
- API test：同 batch 第 2 row duplicate 第 1 row → 拒絕。
- Integration：5 個不同 symbol 全新訂閱 + 第 6 row 不同 symbol → 整批拒絕 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`。
- Integration：batch 內 1 row 條件已成立 + 1 row 仍 active → response status 分別為 `triggered` / `active`，DB notification 已建立 1 筆。
- Integration：rows = 21 → 422。
- Integration：rows = 0 → 422。

## 工程注意事項

- 把 single create 的 service 層 callable 抽出（若尚未抽），不在 controller 重寫 validation chain。Batch endpoint 內以一個 `with session.begin():` block loop 呼叫。
- Transaction 邊界：整 batch 一個 transaction 是 V0.5 接受的簡化；row 數 ≤ 20 + 後端在 demo 場景下無高併發，鎖時間不會痛。V1-10 若要拉到 100 row，可保持單 transaction 但需評估 PostgreSQL statement timeout。
- Subscription quota 在 `CreateTradeIntent` 內已是 commit-前 reconcile；batch 沿用即可，不要在 endpoint 層另算一次（容易兩處 drift）。
- `BATCH_ROW_REJECTED` envelope 是新引入的 code；BE-V0.5-11 handoff 文件需要加入此 code 與一個 row error example。
- 不要在本工單預先加 `batch_import_id` / `source_row_number` 欄位到 `trade_intents`；V1-10 再做 migration 比較乾淨（避免 V0.5 殘留未填欄位）。
- 不要在本工單實作 CSV 解析；後端只收 JSON `rows`，避免引入 CSV parser dependency 與檔案上傳路徑。
- V1-10 對等的 `POST /trade-intents/csv/preview` 與 `/csv/confirm` 與本工單的 `POST /trade-intents/batch` 是**不同 endpoint**；V1 升級時保留 batch endpoint 作為 simple path，preview/confirm 為帶 draft 流程的進階版，或在 V1-10 決定統一棄用 batch endpoint（建議棄用，避免兩條入口）。本工單不替 V1 鎖死設計。
- Endpoint 路徑用 `/trade-intents/batch` 而非 `/trade-intents/csv/*`：強調「後端不知道也不需要知道是不是 CSV」，前端如果改用 Excel 或表格輸入也能直接重用。
