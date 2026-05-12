# V0.5 / V1 後端工程設計

## 1. Stack 與交付模式

這份文件描述同一套後端如何分兩階段交付：

- `v0.5`：本地可跑的最小正式垂直切片。無註冊、無登入、無 admin，不部署到 EC2/RDS；只跑通「建立單筆到價提醒 -> quote 達標 -> intent 觸發 -> 產生站內通知 -> 列表可查看」。
- `v1`：正式上線版，部署於 EC2 + RDS，補齊 auth、CSV、Telegram、admin、真實資料來源、營運監控、安全與資料保留。

核心技術：

- Python 3.13
- FastAPI
- PostgreSQL
- Alembic
- uv
- ruff

V0.5 只需要本地 FastAPI + PostgreSQL。V1 需支援 EC2 + RDS production deployment。

## 2. V0.5 設計邊界

V0.5 不是 demo-only 功能集合，而是 V1 會沿用的最小正式垂直切片。

V0.5 要做：

- 最小 DB schema：只建主流程用到的 tables。
- 固定 single local user context，不做 auth，但 API 仍不得接受 client 傳入 `owner_user_id`。
- 最小 symbol seed 與 symbol validation。
- Tick-size validation 與 Decimal price handling。
- 基本 `day` trading session 判斷。
- 單筆 `buy_price_alert` / `sell_price_alert` create、cancel、list。
- Development quote adapter，用於本地推進 quote，驅動正式 quote evaluation domain logic。
- Quote evaluation 與 trigger transaction。
- Minimal in-app notification record 與 notification list/read API。
- 主流程 integration tests。

V0.5 不做：

- Auth、session、refresh token、CSRF。
- Admin APIs。
- CSV。
- Telegram。
- 停利 / 停損、OCO。
- Corporate action、cash dividend adjustment、effective price preview。
- 真實資料 importer。
- Audit log、outbox、notification delivery attempts、import reports。
- Scheduled jobs、pause/resume、quote unhealthy recovery。
- Kill switches、monitoring、retention、privacy anonymization。

## 3. V1 擴充方向

V1 在 v0.5 主流程上補齊：

- 正式 auth context 取代 local user context。
- Owner-scope authorization 與 cross-user forbidden tests。
- Production symbol / market calendar / corporate action importers。
- Licensed quote provider adapter、quote health、pause/resume。
- Transactional outbox、notification delivery attempts、worker retry。
- 停利 / 停損、OCO。
- CSV preview / confirm。
- Telegram binding / delivery。
- Admin user management、data overrides、monitoring、alerts、kill switches。
- Audit log、rate limits、retention、privacy anonymization。
- EC2/RDS deployment readiness、backup/restore、production hardening。

## 4. 架構原則

- 使用 command handlers 與 domain services。Controller 只處理 transport validation 與呼叫 command，不直接更新 domain state。
- 價格使用 `Decimal` 或 integer tick representation。禁止使用 floating point 儲存或比較價格。
- Timestamp 以 UTC 儲存，另行保存台股市場時區下的 `trading_date`。
- `TradeIntent` 是提醒意圖，不是券商委託單。
- V0.5 與 V1 都固定 `execution_mode = notify_only`。
- Owner scope 從 context 取得：v0.5 是 local user context，v1 是 auth context。
- V0.5 先假設無瞬間大流量，不做 worker scaling、outbox、queue claim/lock。
- V1 觸發到通知流程再升級為 transactional outbox。
- List APIs 使用 cursor pagination。
- Mutating APIs 最終 V1 使用 idempotency keys；v0.5 可先依靠 DB constraints 與 status-guarded transaction。

## 5. 建議 Package Layout

```text
app/
  main.py
  api/
    routes/
    deps.py
    errors.py
  core/
    config.py
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
    quotes/
  commands/
  adapters/
    quote_provider/
  tests/
```

V1 可再擴充：

```text
app/
  core/security.py
  domain/
    corporate_actions/
    users/
    admin/
    outbox/
    audit/
  workers/
    quote_evaluator.py
    notification_worker.py
    scheduled_jobs.py
  adapters/
    telegram/
    corporate_action_provider/
    symbol_provider/
```

## 6. V0.5 核心 Domain Model

### TradeIntent

V0.5 最小欄位：

- `owner_user_id`
- `symbol`
- `strategy = buy_price_alert | sell_price_alert`
- `execution_mode = notify_only`
- `quantity_lots`
- `target_price_original`
- `target_price_effective`
- `trigger_reference_price_type`
- `trading_date`
- `time_in_force = day`
- `status`
- `created_at`
- `updated_at`

V0.5 statuses：

- Non-terminal：`scheduled`、`active`
- Terminal：`triggered`、`cancelled`

V1 再擴充：

- `position_side`
- `price_adjustment_reason`
- `price_adjustment_amount`
- `price_adjusted_at`
- `input_source`
- `batch_import_id`
- `source_row_number`
- `request_id`
- `expired`、`invalid_for_day`、`paused_data_issue`、`paused_market_status`、`cancelled_by_account_disabled`、`ambiguous_trigger`

### Trigger Record

V0.5 可用最小 trigger record 或直接在 `TradeIntent` 保存 trigger metadata。

建議保留獨立 trigger record，方便 V1 擴充：

- Unique `trade_intent_id`
- Trigger quote snapshot
- Effective target used
- `fallback_used`
- `triggered_at`

### Notification

V0.5 只需要最小站內通知：

- `owner_user_id`
- `trade_intent_id`
- `type = price_triggered`
- `rendered_title`
- `rendered_body`
- `read_at`
- `created_at`

V1 再擴充 `NotificationDelivery`、template version、delivery attempts、Telegram metadata。

## 7. 策略語意

V0.5 只支援：

- `buy_price_alert`：優先使用 `ask_price <= target_price_effective`
- `sell_price_alert`：優先使用 `bid_price >= target_price_effective`

若 bid/ask 缺失，可在 last price 可用時 fallback，且記錄 `fallback_used = true`。

V1 再補：

- `take_profit_alert`
- `stop_loss_alert`
- OCO bracket alert group
- Long/short position-side trigger semantics

## 8. Price、Tick 與 Trading Session

V0.5 必須實作：

- 台股 tick-size validation。
- Nearest legal price suggestions。
- Decimal price handling。
- 基本 regular session 判斷。
- `day` intent 的 trading date。
- Evaluator 盤外不得觸發。

V0.5 不做 cash dividend adjustment。V1 再實作：

- Corporate action importer。
- Cash dividend snapshot。
- Effective target preview。
- Round away from trigger。
- Unsupported corporate action pause behavior。

## 9. Quote Evaluation

Quote provider interface：

```python
class QuoteProvider:
    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        ...
```

V0.5 使用 development quote adapter。本地 API 或測試可推進指定 symbol quote。

Normalized quote 至少包含：

- `symbol`
- `bid_price`
- `ask_price`
- `last_price`
- `quote_time`
- `received_at`

V0.5 validation：

- `now` 與 `quote_time` 在 regular session。
- `bid_price <= ask_price`。
- 價格大於 0。
- 缺 bid/ask 可 fallback last。
- bid、ask、last 都不足時不評估。

V0.5 trigger flow：

1. 找出 active intents。
2. 用 development quote adapter 取得 quote。
3. 驗證 quote。
4. 評估 buy/sell price alert。
5. 同一 transaction 更新 intent 為 `triggered`、保存 trigger metadata、建立 notification。

V1 再擴充：

- Licensed quote provider。
- Quote freshness threshold。
- Symbol-level quote unhealthy。
- Provider global failure。
- Pause/resume。
- Outbox 與 notification worker。

## 10. API Surface

V0.5 APIs：

- `GET /health`
- `GET /symbols`
- `POST /trade-intents`
- `GET /trade-intents`
- `GET /trade-intents/{id}`
- `POST /trade-intents/{id}/cancel`
- `POST /dev/quotes`
- `POST /dev/evaluate-quotes`
- `GET /notifications`
- `POST /notifications/{id}/read`

V1 additional APIs：

- Auth：invite activation、login、refresh、logout、password reset。
- CSV：preview、confirm、batch status/history。
- Telegram binding：create bind code、status、unbind。
- Notification settings。
- Admin user management。
- Symbol/calendar/corporate-action overrides。
- Monitoring、alerts、kill switches。

## 11. Error Conventions

V0.5 就應使用正式 error envelope：

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

V0.5 核心 error codes：

- `UNKNOWN_SYMBOL`
- `UNSUPPORTED_INSTRUMENT`
- `INVALID_TICK_SIZE`
- `DUPLICATE_INTENT`
- `QUOTE_UNAVAILABLE`
- `FORBIDDEN`

V1 再補完整 error code matrix、HTTP status mapping、idempotency conflict、CSV row errors。

## 12. 測試策略

V0.5 integration tests：

- Create buy/sell alert。
- Invalid tick。
- Unknown symbol。
- Duplicate intent。
- Development quote update。
- Quote evaluation trigger。
- Immediate trigger。
- Cancel active intent。
- Notification list/read。

V1 tests：

- Auth/session/CSRF。
- Cross-user forbidden。
- CSV all-or-nothing。
- Telegram adapter contract。
- Corporate action adjustment。
- OCO sibling cancellation。
- Outbox idempotency。
- Account disable cancellation。
- Market calendar override。
- Quote unhealthy pause/resume。

