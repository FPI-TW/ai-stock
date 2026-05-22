# BE-V1-10：CSV Preview、Draft、Confirm 與 Batch Metadata

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：15h
- 依賴：BE-V0.5-14, BE-V1-02, BE-V1-07, BE-V1-08, BE-V1-09
- 交付版本：V1

## 背景

V1 必須支援 CSV 批次建立 trade intent（domain-spec §7）。前端解析 CSV 後呼叫後端 validation/preview，後端回 normalized rows + 錯誤 + 調整資訊，使用者確認後同 transaction 建立。

BE-V0.5-14 已提供 V0.5 簡化版：`POST /trade-intents/batch`，前端解析、後端 loop `CreateTradeIntent`、無 preview / draft / idempotency、20 row 上限、buy/sell 範本。本工單在其上補：

- Preview / draft TTL 15 分鐘（讓 user 看過 effective price 再確認）。
- Confirm 重 validate（avoid trust draft；對 corporate action 套用後的價格保 source of truth）。
- Idempotency key 必填（與 BE-V1-16 統一）。
- Bracket template（依 BE-V1-07 / V1-08）。
- Batch limit 拉到 100。
- `batch_import_id` / `source_row_number` / `input_source = csv` 正式啟用（V0.5-14 未寫這些欄位）。

決策：V1 上線時棄用 `POST /trade-intents/batch`，統一改走 `/trade-intents/csv/preview` + `/trade-intents/csv/confirm`，避免兩條入口；前端 V0.5 暫用 batch endpoint 的 client code 在 V1 隨 preview/confirm flow 改寫。

V1 CSV 模板：

- 買賣到價提醒：`symbol, side, quantity_lots, target_price`
- 持倉停利 / 停損：`symbol, position_side, quantity_lots, take_profit_price, stop_loss_price`（兩價皆填 → OCO bracket；只填一邊 → 單腳）

規則：

- All-or-nothing。
- Draft 有效 15 分鐘。
- 確認時重 validate；任一 mismatch 拒絕。
- CSV 不接受公司名稱、模糊搜尋、`TWSE:2330`、`2330.TW`，只接受 `2330`。
- 不長期保存 CSV 原始檔，只存 batch metadata + normalized rows + row hash。

## 目標

- 新增 `csv_batch_drafts`、`csv_batch_imports`、`csv_batch_rows` tables。
- `Preview / Confirm` API 兩 endpoint。
- 重用 BE-V1-07 / BE-V1-08 的 `CreateTradeIntent` / `CreateTradeIntentBracket` command。
- Per-row error reporting（domain-spec §16 row error format）。
- Idempotency key 必填於 confirm。
- Batch limit 100 rows。

## 非目標

- 不解析 Telegram CSV（V1 不支援，domain-spec §7）。
- 不長期保存原始 CSV 檔（只存 file name + row hash）。
- 不做 CSV preview 排程 / 背景處理；preview 同步處理。
- 不做 admin CSV upload tool。

## DB Schema

### `csv_batch_drafts`

- `id uuid primary key`
- `owner_user_id uuid not null references users(id)`
- `template text not null check (template in ('price_alert', 'position_bracket'))`
- `source_file_name text not null`
- `total_rows integer not null`
- `valid_rows integer not null`
- `invalid_rows integer not null`
- `preview jsonb not null`（normalized rows + errors）
- `expires_at timestamptz not null`
- `consumed_at timestamptz null`
- `created_at timestamptz not null`

Indexes：`(owner_user_id, expires_at)`、`expires_at` for cleanup。

### `csv_batch_imports`

- `id uuid primary key`
- `owner_user_id uuid not null references users(id)`
- `draft_id uuid null references csv_batch_drafts(id)`
- `template text not null`
- `source_file_name text not null`
- `total_rows integer not null`
- `created_intent_ids uuid[] not null`
- `created_group_ids uuid[] null`
- `idempotency_key text not null`
- `correlation_id text not null`
- `created_at timestamptz not null`

Unique：`(owner_user_id, idempotency_key)`。

### `csv_batch_rows`

- `id uuid primary key`
- `batch_id uuid not null references csv_batch_imports(id)`
- `row_number integer not null`
- `raw_row_hash text not null`
- `trade_intent_id uuid null references trade_intents(id)`
- `trade_intent_group_id uuid null references trade_intent_groups(id)`
- `created_at timestamptz not null`

### `trade_intents` 變動

新欄位（部分 V0.5 預留）：

- `input_source text not null default 'ui'` check in `('ui', 'csv')`
- `batch_import_id uuid null references csv_batch_imports(id)`
- `source_row_number integer null`

## API

### `POST /trade-intents/csv/preview`

Auth: user。

Body：

```json
{
  "template": "price_alert",
  "fileName": "alerts.csv",
  "rows": [
    { "symbol": "2330", "side": "buy", "quantityLots": "1", "targetPrice": "600.0" },
    { "symbol": "2317", "side": "sell", "quantityLots": "2", "targetPrice": "200.0" }
  ]
}
```

Rules：

- Row 數 ≤ 100，否則 `CSV_BATCH_LIMIT_EXCEEDED` 422。
- 對每 row 走一次與 single create 相同的 validation 鏈：symbol / instrument / tick / session / corporate action / duplicate（含與既有 active intents 比對 + 同 batch 內互相比對）。
- 完整跑完，蒐集 errors。
- Response：

```json
{
  "data": {
    "draftId": "...",
    "expiresAt": "2026-05-22T05:15:00Z",
    "summary": {
      "totalRows": 2,
      "validRows": 2,
      "invalidRows": 0
    },
    "rows": [
      {
        "rowNumber": 1,
        "status": "valid",
        "normalized": {
          "symbol": "2330",
          "strategy": "buy_price_alert",
          "quantityLots": 1,
          "targetPriceOriginal": "600.0",
          "targetPriceEffective": "595.0",
          "tradingDate": "2026-05-22",
          "corporateActionAdjustmentAmount": "5.0"
        },
        "errors": []
      }
    ]
  }
}
```

- Error row format：

```json
{
  "rowNumber": 3,
  "status": "invalid",
  "errors": [
    { "field": "symbol", "code": "UNKNOWN_SYMBOL", "message": "找不到標的代號" }
  ]
}
```

Errors 與 single API error code 對齊（`UNKNOWN_SYMBOL` / `INVALID_TICK_SIZE` / `STALE_PRICE_CONTEXT` 等）。

### `POST /trade-intents/csv/confirm`

Auth: user。

Body：

```json
{
  "draftId": "...",
  "idempotencyKey": "uuid-v4",
  "rows": [
    { "rowNumber": 1, "rawRowHash": "..." }
  ]
}
```

Rules：

- Draft 必須 exists、未 consumed、未 expired、`owner_user_id` match。
- Idempotency key check：
  - 已存在 same key + same payload → 回原 result。
  - same key + different payload → 409。
- 重新對每 row validate（不可只信 draft）；任一 row 失敗 → 整批不建立並回 row errors。
- 全部 valid → 同 transaction：
  - 對 single rows 呼叫 `CreateTradeIntent`。
  - 對 bracket rows 呼叫 `CreateTradeIntentBracket`。
  - 寫 `csv_batch_imports` + `csv_batch_rows`。
  - draft.consumed_at = now()。
- Response：

```json
{
  "data": {
    "batchId": "...",
    "tradeIntentIds": ["..."],
    "tradeIntentGroupIds": [],
    "summary": { "totalRows": 2, "createdRows": 2 }
  }
}
```

### `GET /trade-intents/csv/batches`

- Cursor pagination。
- Filter by template、`createdAt` range。
- 預設 30 天，最大 90 天（domain-spec §7）。

## Error Codes

新增：

- `CSV_BATCH_LIMIT_EXCEEDED`：422。
- `CSV_BATCH_EXPIRED`：410。
- `CSV_BATCH_CONSUMED`：409。
- `CSV_PREVIEW_MISMATCH`：409（confirm 重 validate 結果與 preview 不一致）。
- `IDEMPOTENCY_KEY_REQUIRED`：400。
- `IDEMPOTENCY_KEY_CONFLICT`：409（同 key 不同 payload）。

## 驗收條件

- [ ] CSV preview 對 valid / invalid mixed 正確回 row-level errors。
- [ ] Draft 過期回 410。
- [ ] Draft 已 consumed 回 409。
- [ ] Confirm 任一 row validation 失敗 → 整批不建立。
- [ ] Idempotency key 缺失回 400；重複同 payload 回原結果；不同 payload 回 409。
- [ ] CSV bracket template 兩價皆填走 `CreateTradeIntentBracket`，只填一邊走單腳 take_profit / stop_loss。
- [ ] 同 batch 內重複 row → row-level duplicate error。
- [ ] 100 + 1 rows → 422。

## 測試要求

- Unit：CSV row normalization（symbol uppercase、quantity_lots parse、price Decimal）。
- Unit：dedup within batch + against existing active。
- Integration：preview → confirm 全流程；DB 有 2 個 intent + batch metadata。
- Integration：confirm 後重 confirm 用同 idempotency key → 回原結果，不建第二批。
- Integration：confirm 用不同 idempotency key + same draft → 第二次 410（draft consumed）。
- Integration：bracket template 兩腳建立 + OCO group。
- Integration：100 + 1 rows → 422。
- Integration：corporate action 影響 row 的 preview 回 `targetPriceEffective` 已套用。

## 工程注意事項

- 不要在 confirm 對 draft.preview 結果太信任；DB / market / corporate action snapshot 隨時可能變，必須重 validate。
- 100 row 的 confirm 在同 transaction 內可能花 1–2s；給合理 timeout（pg statement_timeout 不可低於此）。
- Idempotency key 機制統一在 BE-V1-16 的 idempotency table 接管前，本工單先在 `csv_batch_imports.idempotency_key unique` 上實現。
- CSV preview 不要保存原始檔 binary；只存 normalized rows + raw_row_hash。
- 對 bracket template，只填一邊 price 走單腳：需要明確 row-level 規則（兩邊都填 = bracket、只填一邊 = single take_profit_alert / stop_loss_alert，依 position_side + 哪邊有價推導 strategy）。
- 同 user 多份 active draft 可共存，但 cleanup job（BE-V1-15 / 16）定期清理 expired。
- error code 對齊 single create，前端就可共用錯誤展示。
