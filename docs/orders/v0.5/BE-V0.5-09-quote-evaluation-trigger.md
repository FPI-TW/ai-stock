# BE-V0.5-09：Quote Evaluation、Trigger Transaction、Minimal Notification

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：32h
- 依賴：BE-V0.5-07, BE-V0.5-13
- 交付版本：V0.5

## 背景

這張工單完成 V0.5 主流程的核心：quote 達標後，系統用同一 transaction 將 intent 轉為 `triggered`、保存 trigger metadata、建立最小站內 notification。

V0.5 不做 outbox，不做 delivery retry，不做 worker scaling。這是刻意取捨，因為本地展示不會遇到瞬間大流量。

## 目標

- 實作 buy/sell price alert evaluation。
- 由 Shioaji quote provider（BE-V0.5-13）的 quote 更新驅動 evaluation。
- 提供 manual evaluate endpoint / command 作為測試與 debug 入口（in-memory provider 場景必用）。
- 實作 trigger transaction。
- 建立 minimal notification。
- 支援 create 時 immediate trigger（讀 provider 當前 snapshot）。

## 非目標

- 不做 outbox。
- 不做 notification delivery attempts。
- 不做 Telegram。
- 不做 pause/resume。
- 不做 OCO。
- 不做 take-profit/stop-loss。

## Evaluation Rules

### `buy_price_alert`

- 優先使用 `ask_price <= target_price_effective`。
- 若 ask 缺失且 last 存在，使用 `last_price <= target_price_effective`，並標記 `fallback_used = true`、`trigger_reference_price_type = last_fallback`。

### `sell_price_alert`

- 優先使用 `bid_price >= target_price_effective`。
- 若 bid 缺失且 last 存在，使用 `last_price >= target_price_effective`，並標記 `fallback_used = true`、`trigger_reference_price_type = last_fallback`。

### Session Guard

- Evaluator 執行時 `now` 必須在 regular session。
- Quote `quote_time` 必須在 regular session。
- Session 外不得觸發。

## Trigger Transaction

同一 DB transaction 內完成：

1. Lock / reload intent。
2. 確認 status 仍是 `active`。
3. 寫入 `trigger_records`。
4. 更新 `trade_intents.status = triggered`、`triggered_at`。
5. 建立 `notifications`。

防重：

- `trigger_records.trade_intent_id` unique。
- Intent update 使用 status guard，例如 `where status = 'active'`。

## Evaluation Driving

Demo runtime（`QUOTE_PROVIDER=shioaji_demo`）：

- Shioaji callback 收到 quote 更新後，呼叫 evaluator 評估該 symbol 的 active intents。
- Callback 在 Shioaji 自己的 thread；evaluator 內部以 short-lived SQLAlchemy session 處理，避免跨 thread 共用 session。
- 不需要 background worker；callback 即是觸發點。
- Evaluator 不可 import `shioaji_demo` 任何 symbol；callback dispatch 走 base interface（見 BE-V0.5-13 模組切分）。

Manual evaluate（測試 / debug 用，integration test 必用）：

### `POST /dev/evaluate-quotes`

只在 local mode 啟用。

Request：

```json
{
  "symbols": ["2330"]
}
```

Response：

```json
{
  "data": {
    "evaluatedSymbols": ["2330"],
    "triggeredIntentIds": ["..."]
  }
}
```

若未傳 symbols，可評估所有 active symbols。實作可依簡單路徑，不需優化大批量。Endpoint 使用 service 層相同的 evaluator，不複製邏輯。

## Notification Content

V0.5 minimal title/body：

- Title：`2330 到價提醒已觸發`
- Body 必須包含：
  - strategy。
  - target price。
  - trigger price。
  - quote time。
  - 「僅通知、未下單、不保證成交」。

## 驗收條件

- [ ] Evaluator 可依 quote provider（Shioaji 或 in-memory）的 snapshot 評估 active intents。
- [ ] Shioaji callback 觸發 evaluator，盤中真實價格可驅動 trigger。
- [ ] `buy_price_alert` 使用 ask <= target，必要時 fallback last。
- [ ] `sell_price_alert` 使用 bid >= target，必要時 fallback last。
- [ ] Trigger transaction atomic 更新 intent、保存 trigger metadata、建立 notification。
- [ ] Unique constraint 或 status guard 防止 duplicate trigger。
- [ ] 盤中 create 時若 current quote 已符合條件，可立即觸發。

## 測試要求

Integration tests 一律使用 `InMemoryQuoteProvider`（BE-V0.5-13），避免測試打 Shioaji 網路：

- Integration：buy ask below target triggers。
- Integration：sell bid above target triggers。
- Integration：last fallback triggers and records fallback。
- Integration：session outside does not trigger。
- Integration：duplicate evaluate does not create duplicate trigger/notification。
- Integration：immediate trigger on create。
- Integration：cancelled intent does not trigger。
- Unit：Shioaji callback 進來後會呼叫 evaluator（以 fake callback 注入，不打網路）。

## 工程注意事項

- 不要把 notification rendering 分散在 evaluator 內，可使用小型 template function。
- 不要為 V0.5 引入額外 background worker；Shioaji callback 即觸發點，dev endpoint 供測試補位。
- Callback thread 與 request thread 共用 evaluator 時，DB session 必須各自開，不可共用。
- V1 要升級 outbox，因此 trigger transaction 的邊界要清楚。
