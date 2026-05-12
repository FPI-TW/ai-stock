# BE-V0.5-07：單筆 Buy/Sell Price Alert Create/Cancel/List APIs

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：30h
- 依賴：BE-V0.5-03, BE-V0.5-04, BE-V0.5-05, BE-V0.5-06
- 交付版本：V0.5

## 背景

這張工單建立 V0.5 的核心 user-facing API。它只支援單筆買進 / 賣出到價提醒，不做停利停損、OCO、CSV、Telegram、auth、admin。

此 API 後續 V1 會沿用，因此 request/response、error envelope、owner scope、Decimal price 都要按正式標準設計。

## 目標

- 建立 `buy_price_alert` / `sell_price_alert`。
- 取消 active/scheduled intent。
- 查詢列表與單筆 detail。
- 強制 owner scope。
- 執行 symbol、quantity、tick、trading session、duplicate validation。

## 非目標

- 不做 auth。
- 不做 idempotency table。
- 不做 CSV batch。
- 不做停利停損/OCO。
- 不做 corporate action adjustment。
- 不做 scheduled activation/expiry job。

## API

### `POST /trade-intents`

Request：

```json
{
  "symbol": "2330",
  "strategy": "buy_price_alert",
  "quantityLots": 1,
  "targetPrice": "600.00"
}
```

Rules：

- `strategy` 只接受 `buy_price_alert | sell_price_alert`。
- `quantityLots` 正整數。
- `targetPrice` 必須是合法 Decimal 與合法 tick。
- `symbol` 必須是 canonical symbol。
- `execution_mode` 由後端固定為 `notify_only`。
- `time_in_force` 由後端固定為 `day`。
- `owner_user_id` 從 local user context 取得，不接受 request payload。

Response：

```json
{
  "data": {
    "id": "...",
    "symbol": "2330",
    "strategy": "buy_price_alert",
    "quantityLots": 1,
    "targetPriceOriginal": "600.00",
    "targetPriceEffective": "600.00",
    "tradingDate": "2026-05-12",
    "timeInForce": "day",
    "executionMode": "notify_only",
    "status": "active"
  }
}
```

### `GET /trade-intents`

Query：

- `status` optional，可重複或逗號分隔。
- `cursor` optional。
- `pageSize` optional，預設 50，最大 100。

排序：

- active/scheduled：`trading_date asc, created_at desc`。
- terminal：`updated_at desc`。

### `GET /trade-intents/{id}`

只能查 local owner 的 intent。

### `POST /trade-intents/{id}/cancel`

Rules：

- 只允許 `active` / `scheduled` 取消。
- `triggered` 不可取消。
- 重複 cancel 可回目前狀態，不產生額外副作用。

## Duplicate Rule

V0.5 至少禁止同一 owner 在同一 trading date 建立完全相同的 active/scheduled intent：

- owner
- symbol
- strategy
- target_price_effective
- quantity_lots
- trading_date

可用 command transaction 查詢 + status guard 實作；若已建立 partial unique index 更好。

## Error Codes

- `UNKNOWN_SYMBOL`
- `UNSUPPORTED_INSTRUMENT`
- `INVALID_TICK_SIZE`
- `DUPLICATE_INTENT`
- `VALIDATION_ERROR`
- `FORBIDDEN`

## 驗收條件

- [ ] 可建立 `buy_price_alert` 與 `sell_price_alert`。
- [ ] `quantity_lots` 必填且為正整數。
- [ ] V0.5 固定 `time_in_force = day` 與 `execution_mode = notify_only`。
- [ ] 支援 active、scheduled、triggered、cancelled 查詢。
- [ ] Cancel 使用 status-guarded transaction update。
- [ ] 重複 active/scheduled intent 會被拒絕。

## 測試要求

- API test：建立 buy alert 成功。
- API test：建立 sell alert 成功。
- API test：payload 帶 owner id 被拒絕。
- API test：unknown symbol 回 `UNKNOWN_SYMBOL`。
- API test：invalid tick 回 `INVALID_TICK_SIZE`。
- API test：duplicate intent 回 `DUPLICATE_INTENT`。
- API test：cancel active 成功。
- API test：cancel triggered 失敗。
- API test：list 只回 local owner resources。

## 工程注意事項

- Route 不直接寫 DB；使用 command handler，例如 `CreateTradeIntentCommand`。
- Domain service 負責 validation 語意，repository 負責查詢。
- Response price 建議以 string 輸出。
- 不要在 V0.5 加入尚未使用的 notification channel override。
