# BE-V1-04：Production Market Calendar Importer、Scheduled Activation/Expiry

## Metadata

- 類型：HITL
- 優先序：P0
- 預估：35h
- 依賴：BE-V0.5-06
- 交付版本：V1

## 背景

BE-V0.5-06 把台股一般盤時段 hardcode 為 09:00–13:30 Asia/Taipei，且未處理：

- 國定假日 / 補班日。
- 颱風臨時休市。
- 半日盤。
- Scheduled 狀態 day intent 開盤後自動轉 active。
- 收盤後 day intent 自動轉 expired。

V1 必須建立 `market_calendar` table 並接 TWSE holiday schedule，提供 `MarketCalendarService` 給 evaluator / scheduler 共用，並做 scheduled activation / expiry job。

`docs/domain-spec.md` §14 + §6：

- 開盤前臨時休市 → scheduled day intents 自動改下一交易日。
- 盤中臨時停止交易 → active intents 轉 `paused_market_status`。
- 收盤後未觸發 → `expired`。
- Quote evaluator 每輪評估前仍需檢查 session（雙保險）。

## 目標

- 新增 `market_calendar` table（trading day + 半日標記 + override）。
- 新增 `MarketCalendarService`：取代 V0.5 的 hardcoded session。
- 實作 `MarketCalendarImporter`：抽象 source adapter + TWSE source。
- Scheduled job 介面（callable + CLI）：
  - `activate_scheduled_intents`（每交易日開盤觸發）。
  - `expire_day_intents`（每交易日收盤後觸發）。
  - `mark_unfired_active_alerts`（補保險，若 expire 沒跑到，發 admin alert）。
- 替換 V0.5 直接呼叫 hardcoded session 的程式：evaluator、`CreateTradeIntent`、`/symbols`、handoff docs 都改透過 service。

## 非目標

- 不做 admin override endpoint（BE-V1-14）。
- 不做正式 scheduler process（BE-V1-15）；本工單提供 callable + CLI，cron 接線留 R3。
- 不做 corporate action（BE-V1-09）。
- 不處理 TPEx 與 TWSE 不同休市的情境（V1 假設兩者同休；若 future 出現分歧，再擴 schema）。
- 不做盤中暫停交易自動偵測（這需要 quote provider hooks；BE-V1-05 補 status feed）。

## DB Schema

### `market_calendar`

- `id uuid primary key`
- `trading_date date not null unique`
- `is_trading_day boolean not null`
- `session_open time not null default '09:00'`
- `session_close time not null default '13:30'`
- `is_half_day boolean not null default false`
- `reason text null`（e.g. `national_holiday`、`typhoon`、`year_end_half_day`）
- `source text not null check (source in ('twse_holiday', 'manual_seed', 'admin_override'))`
- `source_updated_at timestamptz null`
- `override_by_admin_id uuid null references users(id)`
- `override_at timestamptz null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Indexes：`trading_date unique`、`(is_trading_day, trading_date)`。

Migration seed：未來 1 年的工作日，依 TWSE 公告補 holiday flag；半日盤逐筆 override。

### `calendar_import_reports`

結構同 `symbol_import_reports`，trigger_actor 同樣涵蓋 `scheduled` / `manual_admin` / `cli`。

### `scheduled_job_executions`（為本工單 + BE-V1-15 共用）

- `id uuid primary key`
- `job_name text not null`（e.g. `activate_scheduled_intents`）
- `trading_date date not null`
- `started_at timestamptz not null`
- `finished_at timestamptz null`
- `status text not null check (status in ('running', 'success', 'failed', 'skipped'))`
- `metadata jsonb null`
- `created_at timestamptz not null`
- Unique `(job_name, trading_date)`：防止重跑產生重複 side effects（domain-spec §16）。

## 架構

### `MarketCalendarService`

`app/services/market_calendar/service.py`：

```python
class MarketCalendarService:
    def is_trading_day(self, d: date) -> bool: ...
    def get_next_trading_day(self, after: date) -> date: ...
    def get_regular_session(self, d: date) -> Session | None: ...
    def is_within_regular_session(self, ts: datetime) -> bool: ...
    def get_day_intent_trading_date(self, created_at: datetime) -> date: ...
    def get_day_intent_expiry(self, trading_date: date) -> datetime: ...
```

- 全部 query `market_calendar`，不再讀 hardcoded constant。
- Cache：每次 query 緩存 `market_calendar` 未來 N=90 天 + 過去 30 天 in-memory（避免 evaluator hot path 打 DB）；TTL 5 分鐘 + admin override 後 invalidation hook。

### `MarketCalendarImporter`

- Adapter：`TwseHolidayAdapter`（抓 TWSE 行事曆，HTML / JSON）。
- 流程同 BE-V1-03 importer：fetch → normalize → diff → upsert（保留 admin override）→ 寫 import report。
- 對於 admin override 過的 trading_date，不被 importer 覆寫。

### Scheduled jobs

`app/services/market_calendar/jobs.py`：

#### `activate_scheduled_intents`

- 在交易日開盤時觸發。
- Query `trade_intents.status = 'scheduled' AND trading_date = today AND time_in_force = 'day'`。
- Transition → `active`。
- 寫 audit stub `intent_activated`（BE-V1-16 接 audit table）。
- Idempotent：用 `scheduled_job_executions` 的 unique `(job_name, trading_date)` 防重；若 record 已存在 `success`，直接 skip。

#### `expire_day_intents`

- 在交易日收盤後觸發。
- Query `trade_intents.status in ('scheduled', 'active', 'paused_data_issue', 'paused_market_status') AND trading_date = today`。
- Transition → `expired`，set `expired_at`。
- 寫 audit stub `intent_expired`。
- 同樣用 `scheduled_job_executions` idempotency。

#### `mark_unfired_active_alerts`

- 在 expire job 完成後執行 self-check。
- 若仍有 `status = 'active' AND trading_date < today` 的 row 存在，產生 admin alert（structured log；BE-V1-15 接 telemetry）並強制補跑 expire。

### `CreateTradeIntent` 行為調整

- BE-V0.5-07 中決定 `trading_date` 與 `scheduled` / `active` 的邏輯，改透過 `MarketCalendarService.get_day_intent_trading_date(now)`。
- 非交易日 / 盤前建立 → `scheduled`，`trading_date = next_trading_day`。
- 盤中建立 → `active`，`trading_date = today`。
- 收盤後建立 → `scheduled`，`trading_date = next_trading_day`。

### Quote Evaluator 行為調整

- BE-V0.5-09 evaluator 已有 session guard，但讀 hardcoded constant；本工單改讀 `MarketCalendarService.is_within_regular_session(now)`。
- 半日盤生效時自動使用 `session_close = '12:30'`。

### `trade_intents` 欄位調整

- 新增 `expired_at timestamptz null`。
- 既有 `status` enum 加 `'expired'` / `'paused_market_status'` / `'paused_data_issue'`（migration 改 check constraint）。

## API

### `GET /market-calendar/today`（user readable）

Response：

```json
{
  "data": {
    "tradingDate": "2026-05-22",
    "isTradingDay": true,
    "sessionOpen": "09:00",
    "sessionClose": "13:30",
    "isHalfDay": false,
    "now": "2026-05-22T05:00:00Z",
    "inSession": true
  }
}
```

供前端展示「今日是否開盤」。

### `POST /admin/market-calendar/import`

- 同 BE-V1-03 import 觸發 endpoint。
- Role: admin。

### `POST /admin/jobs/run`（admin only）

- Body：`{ "jobName": "activate_scheduled_intents" | "expire_day_intents" | "mark_unfired_active_alerts" }`
- 立即執行對應 job，主要供 BE-V1-15 上線前手動驗證。
- Idempotency 仍由 `scheduled_job_executions` 保證。

## 驗收條件

- [ ] `market_calendar` migration 可 upgrade / downgrade，未來 1 年 holiday seed 套用。
- [ ] `MarketCalendarService` 取代所有 hardcoded session reference（grep `09:00` / `13:30` 在 service 外無命中）。
- [ ] Activate job 把 scheduled day intents 轉 active；同日重跑無副作用。
- [ ] Expire job 在收盤後把未觸發 day intents 轉 expired。
- [ ] 半日盤 trading_date 的 expire 在 12:30 後生效。
- [ ] 收盤前的 active intent 不會被 expire job 提前 transition。
- [ ] 非交易日不能觸發 evaluator（session guard 拒絕）。
- [ ] Calendar importer 不覆寫 admin override 過的 trading_date。
- [ ] V0.5 既有 integration tests 全綠（migration + service swap 後）。

## 測試要求

- Unit：`get_day_intent_trading_date` 對盤前 / 盤中 / 盤後 / 非交易日 / 半日 各回正確值。
- Unit：`is_within_regular_session` 在 09:00:00、13:30:00、半日 12:30:00 邊界正確。
- Integration：`activate_scheduled_intents` 把 scheduled intent 轉 active；重跑 idempotent。
- Integration：`expire_day_intents` 把過期 intent 轉 expired。
- Integration：admin override 一日為 holiday → service 自動視為非交易日。
- Integration：Calendar importer 不覆寫 override 過的 row。
- Integration：BE-V0.5-09 quote evaluator 在非交易日不觸發。

## 工程注意事項

- 半日盤的 `session_close = 12:30` 是 override 欄位，不要 hardcode 在 service。
- Service 必須容忍 calendar 未來缺資料（importer 還沒跑到）；fallback 行為：回 `None` 或 raise `CalendarNotSeeded` exception，evaluator / scheduler 必須處理。
- `scheduled_job_executions` 的 unique constraint 是 idempotency 主要保障；不要在 application code 再寫 `if already_ran: skip`。
- Hot path（evaluator 每 1–5 秒呼叫 session check）必須命中 cache；TTL 不宜太短，但 admin override 必須 invalidate。
- 對既有 V0.5 `trade_intents.status` 只允許 `scheduled` / `active` / `triggered` / `cancelled` 的 check constraint 必須在本工單 migration 中放寬，且要更新 BE-V0.5-07 documents 中的 status enum。
- 不要在 evaluator 內直接做 expiry transition；evaluator 只 read，job 才 write。
