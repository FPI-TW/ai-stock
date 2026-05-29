# BE-V0.5-15：限價買/賣單 Alert（Limit Buy/Sell Order）

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：18h
- 依賴：BE-V0.5-07, BE-V0.5-09, BE-V0.5-13
- 交付版本：V0.5

## 背景

V0.5 既有 `buy_price_alert` / `sell_price_alert`（BE-V0.5-07）以「到價即觸發」為語意，UI 上偏向「到價提醒」。產品方希望前端能同時提供「限價單」入口，語意上更接近真實券商委託：

- **限價買單**：價格 ≤ target 時買入。
- **限價賣單**：價格 ≥ target 時賣出。
- 交易模式：支援部分成交（partial fill）。
- 通知模式：單次通知（single notification）。

V0.5 系統為 notify-only，後端不串接券商、不送單。因此「部分成交」與「限價」在 V0.5 仍是**通知層的語意與 metadata**，不是真實執行：

- Trigger 條件與既有 buy/sell_price_alert 完全相同（ask ≤ target / bid ≥ target，可 fallback last）。
- 「部分成交」在 V0.5 只在 schema 預留欄位 + 通知文案表達，**V0.5 不模擬跨多筆 quote 累積成交**。Trigger 視為一次完成（`filled_quantity_lots = quantity_lots`）。
- 「單次通知」= 同既有 alert 行為（觸發一次後 intent 進 terminal）。
- V2 接券商後，schema 預留的 `transaction_mode` / `filled_quantity_lots` / `notification_mode` 才會被真實執行語意填上。

本工單**不引入券商**、不引入跨 quote 累積成交模擬、不引入 OCO（V1-08）。

## 目標

- 擴 `trade_intents.strategy` enum：加 `limit_buy_order` / `limit_sell_order`。
- 新增 `transaction_mode`、`notification_mode`、`filled_quantity_lots`、`last_fill_at` 欄位（schema 預留 V2 partial fill）。
- 擴 `CreateTradeIntent` command：新 strategy 走與 buy/sell_price_alert 相同 trigger 邏輯。
- 擴 evaluator：新 strategy 沿用 ask/bid + last fallback 規則。
- Trigger transaction：新增 limit_buy_order / limit_sell_order 兩種 strategy 的 trigger 行為（V0.5 等同 alert：fill 全部 quantity_lots、單次通知、進 terminal）。
- Notification template 新增：限價買/賣單觸發文案（標題與 body 與 price_triggered 不同，需明示「限價單」、`filled_quantity_lots`、「僅通知、未下單、不保證成交」）。

## 非目標

- 不模擬跨多筆 quote 的累積部分成交（V2 接券商後再做）。
- 不做時間加權 / TWAP（domain-spec 明示 V1 不做）。
- 不做 OCO bracket（V1-08）。
- 不做 take-profit / stop-loss 持倉型策略（V1-07）。
- 不做新 status；沿用 `scheduled` / `active` / `triggered` / `cancelled`。
- 不做 partial fill 對應的新 notification type；沿用單一 `limit_order_triggered` type。

## DB Schema

### `trade_intents` 變動

新欄位：

- `transaction_mode text not null default 'single_notification'`
  - check in `('single_notification', 'partial_fill_allowed')`
  - V0.5：buy/sell_price_alert / limit_buy_order / limit_sell_order 一律可填 `single_notification` 或 `partial_fill_allowed`，但 V0.5 evaluator 行為相同（trigger 即視為全 fill）。
  - V0.5 預設 `single_notification`；前端建立 limit_*_order 時建議顯式填 `partial_fill_allowed` 對齊未來 V2 語意。
- `notification_mode text not null default 'single'`
  - check in `('single', 'per_fill')`
  - V0.5 evaluator 僅支援 `single`；schema 層用 `Literal['single']` 限制，client 填 `per_fill` 由 Pydantic 自動回 422 `VALIDATION_ERROR`（不另定 strategy-specific code）。
  - 預留欄位讓 V2 接券商後可逐筆成交發通知。
- `filled_quantity_lots integer not null default 0`
  - V0.5：trigger 時 evaluator 寫入 = `quantity_lots`。
  - 未觸發前為 0。
  - V2 partial fill 才會累加。
- `last_fill_at timestamptz null`
  - V0.5：trigger 時 evaluator 寫入 = `triggered_at`。

### `trade_intents.strategy` check constraint 擴充

migration 改 check constraint，新增 `limit_buy_order` / `limit_sell_order`。

對應 `order_side`（沿用 BE-V1-07 將補的 `order_side` 欄位邏輯；本工單在 V0.5 階段直接 derive 寫入而不另開 schema column，V1-07 接手時再 promote 為 column）：

- `limit_buy_order` → buy
- `limit_sell_order` → sell

### `trigger_events` 變動

新欄位：

- `filled_quantity_lots integer not null`
  - V0.5：寫入 = intent.quantity_lots。
  - 對 buy/sell_price_alert，migration 給既有 row backfill = quantity_lots。

### Duplicate check

既有 partial unique index 加上 strategy 欄位區分（已包含）。本工單不需新增 index，但測試需確認：

- 同 user / symbol / target / quantity / trading_date 下，`buy_price_alert` 與 `limit_buy_order` **被視為不同 intent**，不撞 duplicate。
- 同 user / symbol / target / quantity / trading_date / strategy = `limit_buy_order` 兩筆重複 → 撞 `DUPLICATE_INTENT`。

## Strategy 語意

### `limit_buy_order`

Trigger 條件（與 `buy_price_alert` 相同）：

- 優先：`ask_price <= target_price_effective`。
- Fallback：`ask` 缺失且 `last` 存在 → `last_price <= target_price_effective`，標 `fallback_used = true`、`trigger_reference_price_type = last_fallback`。

Trigger 行為：

1. 同 BE-V0.5-09 的 trigger transaction（lock / status guard / 寫 trigger_events / 更新 intent / 建立 notification）。
2. `intent.status = triggered`、`triggered_at = now()`。
3. `intent.filled_quantity_lots = intent.quantity_lots`、`last_fill_at = now()`。
4. `trigger_events.filled_quantity_lots = intent.quantity_lots`。
5. 建立 notification，type = `limit_order_triggered`（見下）。

### `limit_sell_order`

Trigger 條件（與 `sell_price_alert` 相同）：

- 優先：`bid_price >= target_price_effective`。
- Fallback：`bid` 缺失且 `last` 存在 → `last_price >= target_price_effective`，標 `fallback_used = true`、`trigger_reference_price_type = last_fallback`。

Trigger 行為：同 `limit_buy_order`，順向反向。

### Session Guard

- 同 BE-V0.5-09：evaluator 執行時 `now` 與 `quote_time` 都必須在 regular session。

### 即時觸發

- 同 BE-V0.5-07 的 immediate trigger：盤中建立時若 current quote 已符合條件，同 `CreateTradeIntent` transaction 完成 trigger。

## Notification

新增 type：`limit_order_triggered`。

Template 要求（in_app body 與 title）：

- Title：`2330 限價買單已觸發` / `2330 限價賣單已觸發`。
- Body 必含：
  - strategy（限價買單 / 限價賣單）。
  - target price。
  - trigger price。
  - `filled_quantity_lots` / `quantity_lots`（V0.5 兩者相等；但文案表達上要寫成「成交 1 張 / 委託 1 張」以對齊使用者心智）。
  - quote time。
  - 「僅通知、未下單、不保證成交」。

Telegram 正式 binding、settings 與 delivery attempts 在 V1-11 / V1-12 補；V0.5 只做 env-based 最小同步，Telegram 文字沿用站內 `rendered_title` / `rendered_body`。

## API

### `POST /trade-intents`（既有，schema 重構為 Pydantic discriminated union）

本工單把 `POST /trade-intents` 的 request body 改為 Pydantic discriminated union（以 `strategy` 為 discriminator），每 strategy 對應獨立子 schema、各自 `extra=forbid`。新增 strategy 不影響其他 strategy 的 schema；跨 strategy 欄位錯放由 Pydantic 自動 422 拒絕，不需另寫 cross-field validation 或 strategy-specific error code。

這是 V0.5 階段的 schema policy 轉向（從 nullable union schema 改 discriminated union）。BE-V0.5-16 一併套用，BE-V0.5-14 batch endpoint 的 row schema 隨之沿用同一份子 schema（V0.5-14 文件不需改寫）。

Body 範例（limit_buy_order）：

```json
{
  "strategy": "limit_buy_order",
  "symbol": "2330",
  "quantityLots": 1,
  "targetPrice": "600.0",
  "transactionMode": "partial_fill_allowed",
  "notificationMode": "single"
}
```

Schema 規則（由 Pydantic 強制，無需 controller 額外 validation）：

- `strategy = 'limit_buy_order' | 'limit_sell_order'` 子 schema：
  - 必填：`symbol`, `quantityLots`, `targetPrice`
  - 選填：`transactionMode: Literal['single_notification', 'partial_fill_allowed']`，缺失預設 `single_notification`
  - 選填：`notificationMode: Literal['single']`，缺失預設 `single`
  - 不在欄位清單的 key（如錯填 `trailValue` / `trailMode`）由 `extra=forbid` 拒絕
- 既有 `buy_price_alert` / `sell_price_alert` 子 schema 不含 `transactionMode` / `notificationMode` 欄位；client 多帶會被 `extra=forbid` 拒絕（既有 behavior 不變，因為沒帶仍正常）
- 違反 schema 一律回 422 `VALIDATION_ERROR`，envelope `details.loc` 指明錯誤欄位（沿用 BE-V0.5-01 envelope）；不再為 schema 組合錯誤新增 strategy-specific error code

`notificationMode = 'per_fill'` 在 V0.5 由 `Literal['single']` 自動拒絕（Pydantic 回 `VALIDATION_ERROR` + `details.loc = ['notificationMode']`）；V2 接券商實作 per-fill 後把 Literal 擴為 `Literal['single', 'per_fill']` 即可，不需移除 error code。

Response 加：

```json
{
  "data": {
    "transactionMode": "partial_fill_allowed",
    "notificationMode": "single",
    "filledQuantityLots": 0
  }
}
```

### `GET /trade-intents` / `/{id}`

- Response 加 `transactionMode` / `notificationMode` / `filledQuantityLots` / `lastFillAt`。

### `POST /trade-intents/batch`（BE-V0.5-14）

- 支援 `limit_buy_order` / `limit_sell_order` row。
- 同 batch 內可混合 buy_price_alert / limit_buy_order。
- 不放寬 20 row 上限。

## Error Codes

無新增。

Schema 組合錯誤（缺欄位、多欄位、Literal 越界如 `notificationMode = per_fill`、`transactionMode` 未知值）一律走既有 `VALIDATION_ERROR` 422，envelope `details.loc` / `details.msg` 由 Pydantic 自動填入。

刻意不為 schema policing 新增 `NOTIFICATION_MODE_UNSUPPORTED_IN_V0_5` / `TRANSACTION_MODE_UNSUPPORTED` 等 code，避免後續 V2 / V1.5 解放 mode 時 error code 需要逆向移除。

## Configuration

無新 env。

## 驗收條件

- [ ] migration 可 upgrade / downgrade，既有 buy_price_alert / sell_price_alert row 補 `transaction_mode = 'single_notification'`、`notification_mode = 'single'`、`filled_quantity_lots = 0`。
- [ ] migration 對 `trigger_events` backfill `filled_quantity_lots = intent.quantity_lots`。
- [ ] `POST /trade-intents` schema 改為 Pydantic discriminated union（以 `strategy` 為 discriminator），各 strategy 子 schema 設 `extra=forbid`。
- [ ] `POST /trade-intents` 接受 `strategy = limit_buy_order` / `limit_sell_order` 並回正確欄位。
- [ ] `notificationMode = per_fill` 由 Literal 自動拒絕，envelope 為 `VALIDATION_ERROR`、`details.loc = ['notificationMode']`。
- [ ] `buy_price_alert` body 多帶 `transactionMode` 或 `trailValue` 由 `extra=forbid` 拒絕。
- [ ] 不存在 strategy-specific error code（grep `NOTIFICATION_MODE_UNSUPPORTED_IN_V0_5` / `TRANSACTION_MODE_*` 全 repo 命中 0）。
- [ ] `limit_buy_order` 在 ask ≤ target 時觸發；fallback last。
- [ ] `limit_sell_order` 在 bid ≥ target 時觸發；fallback last。
- [ ] Trigger 完成後 `intent.filled_quantity_lots = quantity_lots`、`last_fill_at = triggered_at`。
- [ ] Notification type = `limit_order_triggered`，body 含「成交 / 委託」張數與「僅通知」聲明。
- [ ] 既有 buy_price_alert / sell_price_alert 行為不變（regression）。
- [ ] Duplicate check：buy_price_alert 與 limit_buy_order 同條件不互撞；同 strategy 同條件兩筆會撞 `DUPLICATE_INTENT`。
- [ ] BE-V0.5-13 subscription quota / allowlist 行為與既有 strategy 一致。

## 測試要求

- Unit：strategy → order_side derive（4 種 strategy 各對 buy/sell）。
- Unit：evaluator 對 limit_buy_order ask ≤ / > target、fallback last 各案例。
- Unit：evaluator 對 limit_sell_order bid ≥ / < target、fallback last 各案例。
- Unit：notification template `limit_order_triggered` render 出含「限價買/賣單」、`filled_quantity_lots / quantity_lots`、「僅通知」字樣。
- Unit：discriminated union 解析：4 種 strategy 各自正確 dispatch 到對應 Pydantic 子 schema。
- Unit：`notificationMode = per_fill` → Pydantic 422，`details.loc = ['notificationMode']`。
- Unit：`buy_price_alert` body 多帶 `transactionMode` → Pydantic 422，`details.loc = ['transactionMode']`。
- Unit：`limit_buy_order` body 缺 `targetPrice` → Pydantic 422，`details.loc = ['targetPrice']`。
- Integration：limit_buy_order create → quote ask 達標 → triggered + filled_quantity_lots = quantity_lots + notification 寫入。
- Integration：limit_sell_order 同上對稱。
- Integration：盤中建立已成立 → immediate trigger。
- Integration：buy_price_alert + limit_buy_order 同條件並存不互撞。
- Integration：BE-V0.5-12 既有 test suite 全綠。
- Integration（regression）：buy_price_alert default `transaction_mode = 'single_notification'`、`filled_quantity_lots = quantity_lots` 在 trigger 後正確。

## 工程注意事項

- `transaction_mode` / `notification_mode` 是 V2 預留的 forward-compatible 欄位；V0.5 evaluator 行為一致（trigger 即視為全 fill），不要為了「未來會用」在 V0.5 拆出兩條 evaluator 路徑。
- `notification_mode = per_fill` 在 V0.5 拒絕是刻意的：避免前端誤以為 V0.5 已支援逐筆通知；error message 要寫清楚。
- `filled_quantity_lots` 在 V0.5 trigger 時就寫滿；不要為了 V2 預留留空（V0.5 通知文案需要這個欄位顯示「成交數」）。
- Notification type 用獨立 `limit_order_triggered`，不與 `price_triggered` 共用，避免 template 後續分歧時還要 retro-fit type field。
- Strategy 命名 `limit_buy_order` / `limit_sell_order` 與 domain-spec §3 的 strategy capability matrix 對齊；matrix 需要在 BE-V1-07 的 take_profit / stop_loss 工單同步補列（本工單不動 matrix）。
- 不要把 `order_side` 提前 promote 為 schema column；BE-V1-07 統一處理。V0.5 evaluator 內 derive 即可。
- 既有 BE-V0.5-09 trigger transaction 的「同一 transaction 更新 intent / 寫 trigger_events / 建立 notification」邊界完全沿用；只是在這三個寫入動作裡多寫 `filled_quantity_lots` / `last_fill_at` 兩個欄位。
- V1-10 CSV preview / draft 接手後，limit_buy_order / limit_sell_order 在 CSV row 中與 buy_price_alert / sell_price_alert 屬同一 `price_alert` template 還是另開 `limit_order` template，由 V1-10 決定；本工單 BE-V0.5-14 的 `/trade-intents/batch` 已能接這四種 strategy 不需額外設計。
