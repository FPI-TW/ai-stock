# V0.5 / V1 後端工程設計

## 1. Stack 與交付模式

這份文件描述同一套後端如何分兩階段交付：

- `v0.5`：本地可跑的最小正式垂直切片。無註冊、無登入、無 admin，不部署到 EC2/RDS；只跑通「建立單筆到價提醒 -> quote 達標 -> intent 觸發 -> 產生站內通知 -> 列表可查看」。
- `v1`：正式上線版，部署於 EC2 + RDS，補齊 auth、CSV、Telegram、admin、真實資料來源、營運監控、安全與資料保留。

核心技術：

- Python 3.13
- FastAPI
- PostgreSQL
- SQLAlchemy sync engine with `psycopg` 3
- Alembic
- uv
- ruff

V0.5 只需要本地 FastAPI + PostgreSQL。V1 需支援 EC2 + RDS production deployment。

V0.5 DB driver decision：

- 使用 `postgresql+psycopg://` 與 SQLAlchemy sync engine。
- 不採用 `asyncpg` 作為 V0.5 基礎，避免在骨架階段過早把 route、session dependency、tests 全部推向 async。
- 若 V1 實際出現高併發 DB I/O 需求，再評估 SQLAlchemy async engine；`psycopg` 3 仍可支援 async path。
- BE-V0.5-01 提供 local-only PostgreSQL `docker-compose.yml`，使用 `postgres:17`、`ai_stock` database/user/password、`5432:5432`、named volume；不建立 app container，不代表 production Docker decision。

V0.5 migration timestamp decision：

- 所有 V0.5 tables 都有 `created_at`。
- 狀態會變更的 tables 有 `updated_at`；V0.5 包含 `symbols`、`trade_intents`、`notifications`。
- `trigger_events` 是 immutable trigger snapshot，只保留 `created_at` 與 domain event time `triggered_at`。
- 不建立 DB trigger 自動更新 `updated_at`；更新 command/repository 必須顯式寫入 `updated_at`。

V0.5 database baseline decision：

- BE-V0.5-02 只交付 ORM models、Alembic migration、constraints、integration tests；repository layer 依 BE-V0.5-04/07/09 use case 再補。
- Alembic migration 不 seed symbols；BE-V0.5-04 負責最小 symbol seed。
- Canonical symbol fields 使用 lowercase enum where applicable：`instrument_type = stock | etf`，`tradable_status = tradable | halted | unsupported`。
- `market` canonical values 只允許 `TWSE | TPEx`，不加入 `TPEX` alias。
- `symbols.symbol` 格式 normalize 留給 symbol service，不在 DB 層加 regex。
- `trade_intents.symbol` 與 `trigger_events.symbol` 都 FK 到 `symbols(symbol)`。
- `trigger_events.owner_user_id` 與 `notifications.owner_user_id` 不做 owner composite FK；後續 command transaction tests 驗證 copy/ownership 一致性。
- Duplicate intent DB invariant 只限制 active/scheduled user-facing duplicate；terminal `cancelled` / `triggered` 後允許重建。
- Price columns 使用 `numeric(9, 4)`，DB 只限制正數；tick-size validation 留給 BE-V0.5-05。
- `quote_snapshot` 只保證 non-null JSONB，不加 shape constraint。
- `trigger_events` 要檢查 `last_fallback` 與 `fallback_used` 一致。
- `notifications.trade_intent_id` nullable 以保留 V1 擴充，但 `price_triggered` 必須有 `trade_intent_id`。
- 不加 trade intent status 與 terminal timestamp 的 DB consistency check；BE-V0.5-07/09 command tests 驗證。

V0.5 settings decision：

- 使用 `pydantic-settings` 建立集中式 typed settings。
- `.env.example` 提供本地範例。
- V0.5 預設 `APP_ENV=local`、`APP_NAME=ai-stock-api`、`APP_VERSION=0.5.0`、`LOCAL_MODE=true`、`LOCAL_USER_ID=local-user`、`REQUEST_ID_HEADER=X-Request-Id`。
- V0.5 local mode 允許 app 在缺少 `DATABASE_URL` 時啟動；`/health` 必須回 `503 DATABASE_UNAVAILABLE`。Production fail-fast 不在 V0.5 範圍。

V0.5 quality script decision：

- `Makefile` 是本地開發與品質檢查的穩定入口，README 記錄等價 `uv` 指令。
- `make dev` 使用 `uv run uvicorn app.main:app --app-dir src --reload --port 8100` 以支援 `src` layout，並固定本地預設 API port。
- 基礎 targets：`install`、`dev`、`lint`、`format`、`format-check`、`test`、`test-integration`。
- BE-V0.5-01 暫不加入 `mypy` 或 `pyright`；待 SQLAlchemy ORM/domain model 穩定後再評估 typed Python baseline。
- BE-V0.5-02 起加入 `mypy`，`make typecheck` 執行 `uv run mypy src tests`，`make check` 執行非 PostgreSQL 品質門檻：`lint`、`format-check`、`typecheck`、`test`。
- mypy baseline 要求 typed function definitions 與 `check_untyped_defs`，但暫不開 `strict = true` 或 `disallow_any_*`。

V0.5 test strategy decision：

- `make test` 預設跑不需 Docker 的快速 unit/API tests，可 mock DB ping。
- `make test-integration` 使用 `pytest -m integration`，要求 local PostgreSQL 已啟動，打真 DB 驗證 health connectivity。
- V1 完成時不可只依賴 mock；主流程與 DB transaction/constraints 必須有真 PostgreSQL integration tests。

V0.5 health endpoint decision：

- V0.5 只保留 `GET /health`，語意為 readiness，必須檢查 DB connectivity。
- 暫不拆 `/live` / `/ready`；V1 deployment readiness 再評估。

V0.5 request id decision：

- Request id header 名稱由 `REQUEST_ID_HEADER` 設定決定，預設 `X-Request-Id`。
- Client 傳入 request id 時，長度 `1..128` 則原樣保留；缺失或空字串則產生 UUID4。
- 超過 128 字元回 `400 VALIDATION_ERROR`，response 與 error envelope 仍帶有效 request id。

V0.5 error message decision：

- Error envelope 的 `code` 是前端邏輯契約。
- `message` 使用中文 user-facing 顯示文字。
- 基礎訊息：`INTERNAL_ERROR=發生未預期錯誤`、`VALIDATION_ERROR=請求資料不合法`、`DATABASE_UNAVAILABLE=資料庫暫時無法使用`。
- 未處理 exception 的 response 使用 `INTERNAL_ERROR` 與 `details: {}`；stack trace、path、method、request id 只進 server log。

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
src/
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
src/
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
