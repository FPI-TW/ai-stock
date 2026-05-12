# V1 後端工程設計

## 1. Stack 與 Repo 假設

核心技術：

- Python 3.13
- FastAPI
- PostgreSQL
- Alembic
- uv
- ruff

建議 runtime process 邊界：

- Web/API process。
- Quote evaluator worker。
- Notification worker。
- Scheduled jobs worker。

第一版部署可以在同一台機器或同一服務群組內執行，但程式邊界要清楚保留，避免 API 流量、quote evaluation、notification delivery 與 scheduled jobs 互相拖垮，也方便後續拆分擴展。

## 2. 架構原則

- 使用 command handlers 與 domain services。Controller 只處理 transport validation 與呼叫 command，不直接更新 domain state。
- 價格使用 `Decimal` 或 integer tick representation。禁止使用 floating point 儲存或比較價格。
- Timestamp 以 UTC 儲存，另行保存台股市場時區下的 `trading_date`。
- `TradeIntent` 是提醒意圖，不是券商委託單。
- V1 固定 `execution_mode = notify_only`。
- 觸發到通知流程使用 transactional outbox。
- Scheduled jobs 必須 idempotent。
- API、commands、audit events、outbox events、notification delivery attempts 與 technical logs 都要串接 request/correlation ID。
- Owner scope 從 auth context 取得。User-facing APIs 不接受 client 傳入 `owner_user_id`。
- List APIs 使用 cursor pagination。
- Mutating APIs 使用 idempotency keys。

## 3. 建議 Package Layout

```text
app/
  main.py
  api/
    routes/
    deps.py
    errors.py
  core/
    config.py
    security.py
    time.py
    ids.py
  db/
    session.py
    models/
    migrations/
  domain/
    trade_intents/
    notifications/
    symbols/
    market_calendar/
    corporate_actions/
    quotes/
    users/
    admin/
  commands/
  workers/
    quote_evaluator.py
    notification_worker.py
    scheduled_jobs.py
  adapters/
    quote_provider/
    telegram/
    corporate_action_provider/
    symbol_provider/
  tests/
```

## 4. 核心 Domain Model

### TradeIntent

代表使用者擁有的一筆提醒意圖。

重要欄位：

- `owner_user_id`
- `symbol`
- `strategy`
- `execution_mode = notify_only`
- `position_side`
- `quantity_lots`
- `target_price_original`
- `target_price_effective`
- `price_adjustment_reason`
- `price_adjustment_amount`
- `price_adjusted_at`
- `trigger_reference_price_type`
- `trading_date`
- `time_in_force = day`
- `status`
- `input_source`
- `batch_import_id`
- `source_row_number`
- `request_id`

Statuses：

- Non-terminal：`scheduled`、`active`、`paused_data_issue`、`paused_market_status`
- Terminal：`triggered`、`expired`、`cancelled`、`invalid_for_day`、`cancelled_by_account_disabled`、`ambiguous_trigger`

### TradeIntentGroup

用於 OCO bracket alerts。

- `group_type = bracket_alert`
- 兩筆 child intents：`take_profit_alert`、`stop_loss_alert`
- 任一 child 觸發時，sibling 在同一 transaction 取消。
- 若同一次 quote update 兩腳同時成立，group 與 children 轉為 `ambiguous_trigger`，不送一般到價通知。

### TriggerEvent

V1 每筆 intent 最多一筆 trigger event。

必要內容：

- Unique `trade_intent_id`
- Trigger quote snapshot
- 觸發時使用的 effective target
- 若 bid/ask fallback 到 last，需記錄 fallback flag
- `trigger_context`
- `correlation_id`

### Notification 與 NotificationDelivery

使用統一通知模型，不依通知類型拆表。

Notification 必須保存 rendered snapshots：

- `template_key`
- `template_version`
- `message_data`
- `rendered_title`
- `rendered_body`
- `rendered_at`

Delivery 在適用情境需以 event/channel 保持唯一：

- `channel = in_app | telegram`
- `status = pending | sent | failed_retryable | failed_permanent | skipped`
- `sent_at`
- `error_code`

### AuditEvent

最低欄位：

- `actor_type = user | system | admin`
- `actor_id`
- `event_type`
- `occurred_at`
- `metadata`
- `request_id` 或 `correlation_id`

核心交易資料不要用通用 `deleted_at` 表達語意，應使用明確 status 與 audit events。

## 5. 策略語意

### Price Alerts

- `buy_price_alert`：優先使用 `ask_price <= target_price_effective`
- `sell_price_alert`：優先使用 `bid_price >= target_price_effective`
- 若 bid/ask 缺失，只能在 last price 可用時 fallback，且要標記 `fallback_used = true`。

### Take Profit 與 Stop Loss

`position_side` 必填，可為 `long` 或 `short`。

- 多單停利：賣出提醒，`bid >= target`
- 多單停損：賣出提醒，`bid <= target`
- 空單停利：買回提醒，`ask <= target`
- 空單停損：買回提醒，`ask >= target`

V1 允許使用者聲明空單持倉做出場提醒，但不支援空單進場。

## 6. Price、Tick 與 Corporate Action Services

實作台股 tick-size table，供以下用途使用：

- 使用者 target validation。
- Cash dividend effective price adjustment。
- 未來 V2 drift ticks calculation。

使用者原始目標價必須是合法 tick。若不合法，回傳 `INVALID_TICK_SIZE` 與最接近合法價格。

現金股利調整公式：

```text
target_price_effective = target_price_original - corporate_action_adjustment_amount
```

若調整後價格不是合法 tick，需 round away from trigger，避免 rounding 讓條件更容易觸發。

Corporate actions：

- Strategy engine 只讀內部 `corporate_actions` 與 `trading_day_adjustment_snapshot`。
- V1 只套用 cash dividend。
- 會影響價格基準但 V1 不支援的 action，需讓相關 intents 轉 `paused_data_issue`。
- Snapshot disputed 時，相關 active intents 暫停；恢復後不回放暫停期間 quote。

## 7. Market Calendar

建立 `MarketCalendarService`，能力包含：

- `is_trading_day`
- `get_next_trading_day`
- `get_regular_session`
- `is_within_regular_session`
- `get_day_intent_trading_date`
- `get_day_intent_expiry`

V1 `day` intent 只覆蓋一般盤。盤前、收盤後、非交易日建立時，依規則進入對應 trading date 的 `scheduled`。

Expiry 要有雙保險：

- Scheduled expiry job 將符合條件的 day intents 轉 `expired`。
- Quote evaluator 每次評估前檢查 session，盤外永遠不得觸發。

## 8. Quote Evaluation

Quote provider interface：

```python
class QuoteProvider:
    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        ...
```

Normalized quote 必須包含：

- `symbol`
- `bid_price`
- `ask_price`
- `last_price`
- `quote_time`
- `source`
- `source_latency_label`
- `raw_payload_ref` 或 raw hash
- `received_at`

Validation：

- `received_at - quote_time <= 10s`
- `now - quote_time <= 10s`
- `now` 與 `quote_time` 都在 regular session。
- `bid_price <= ask_price`
- 價格必須大於 0。
- 缺 bid/ask 時可 fallback 到 last price。
- bid、ask、last 都不足時，不評估該 symbol。

Evaluator loop：

1. 找出 active intent symbol set。
2. 每 1-5 秒依 symbol 抓 quote。
3. 驗證 quote。
4. 評估該 symbol 的所有 active intents。
5. 在同一 transaction 更新 intent/group 狀態、建立 `TriggerEvent`、寫入 `OutboxEvent`。

使用 DB constraints 與 transactional state conditions 防止 duplicate triggers 與 trigger/cancel race。

## 9. API Conventions

### Error Envelope

```json
{
  "error": {
    "code": "INVALID_TICK_SIZE",
    "message": "目標價不符合最小升降單位",
    "details": {
      "nearestPrices": ["98.8", "98.9"]
    },
    "requestId": "..."
  }
}
```

### Success Warnings

```json
{
  "data": {},
  "warnings": [
    {
      "code": "QUOTE_UNAVAILABLE",
      "message": "已建立，等待有效行情"
    }
  ]
}
```

### Idempotency

以下 API 必須使用：

- `CreateTradeIntent`
- `CreateTradeIntentBatch`
- CSV confirm
- `CancelTradeIntent`
- `CancelTradeIntentGroup`

規則：

- 缺 key 回傳 `IDEMPOTENCY_KEY_REQUIRED`。
- Same user、same key、same payload 回傳同一結果。
- Same user、same key、different payload 回傳 `IDEMPOTENCY_KEY_CONFLICT`。
- Key 保存 24 小時。

## 10. 主要 API Surface

User APIs：

- Auth：invite activation、login、refresh、logout、password reset。
- Symbols：autocomplete 與 lookup。
- Corporate action preview：針對 selected symbol/date/strategy 計算 effective price。
- Trade intents：create、cancel、list active/scheduled/history、detail。
- CSV：preview、confirm、batch status/history。
- Notifications：list、read、unread count。
- Notification settings：get/update Telegram enabled state。
- Telegram binding：create bind code、get status、unbind。

Admin APIs：

- User create/disable/resend invitation。
- Symbol master import status 與 overrides。
- Market calendar import status 與 overrides。
- Corporate action import status、overrides、disputed snapshot handling。
- System health、worker backlog、data health、notification failure summary。
- Kill switch read/update。
- Audit query with restricted access controls。

## 11. CSV Backend Contract

後端擁有權威 validation 與 preview。

CSV preview：

- 接收前端解析後的 rows。
- Normalize rows。
- 驗證 symbols、tick size、trading date、daily limits、strategy rules、OCO relationship、limits、duplicates、corporate action adjustment。
- 保存 `csv_batch_draft` 15 分鐘。
- 回傳 row-level errors 與 warnings。

CSV confirm：

- 需要 idempotency key。
- 逐列重新驗證。
- Draft 過期或 preview 不一致時拒絕。
- 在同一 transaction 建立所有 intents。
- 任一 row 失敗則不建立任何 intent。

保存 metadata：

- `input_source = csv`
- `batch_import_id`
- `source_row_number`
- `source_file_name`
- `raw_row_hash`

不長期保存原始 CSV file。

## 12. Security 與 Roles

Roles：

- `user`
- `admin`

Auth：

- Short-lived access JWT。
- DB-backed refresh token rotation。
- Refresh token 只保存 hash。
- Refresh token 使用 HttpOnly、Secure、SameSite cookie。
- State-changing requests 需要 CSRF protection。
- 檢查 Origin/Referer。
- Tokens 不可存 localStorage。

Admin：

- 使用 admin 功能前必須完成 TOTP 2FA。
- 高風險操作必須寫 audit events。
- Admin 存取 user data 必須明確、填 reason、可稽核。

Account disable：

- Active/scheduled user intents 轉 `cancelled_by_account_disabled`。
- 停用 Telegram binding。
- Pending notification deliveries 標記 skipped。
- 保留 history 與 audit。

## 13. 測試策略

Domain unit tests：

- Tick size。
- Dividend adjustment。
- Round away from trigger。
- Strategy trigger direction。
- OCO rules。
- Status transitions。

Command/integration tests：

- Create intent。
- CSV all-or-nothing。
- Immediate trigger。
- Quote trigger transaction。
- Notification outbox。
- Account disable cancellation。
- Snapshot disputed。
- Market calendar override。
- Cross-user access forbidden。

Adapter contract tests：

- Quote provider。
- Telegram adapter。
- Corporate action provider。
- Symbol provider。

使用 fake adapters 讓測試與 frontend contract fixtures 可重現。
