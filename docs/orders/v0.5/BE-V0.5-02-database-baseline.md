# BE-V0.5-02：最小 Database Baseline、Alembic、Core Intent/Notification Tables

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：22.5h
- 依賴：BE-V0.5-01
- 交付版本：V0.5

## 背景

V0.5 只建主流程必需資料表，避免先做未使用的 audit、outbox、delivery attempts、import reports、retention records。這些會在 V1 additional 補上。

主流程需要：

- symbol validation。
- trade intent create/list/cancel。
- trigger metadata。
- minimal in-app notification list/read。

## 目標

建立 PostgreSQL integration、Alembic migrations、ORM models 或 repository layer，以及 V0.5 最小 schema。

## 非目標

- 不建 audit events。
- 不建 outbox events。
- 不建 notification deliveries / delivery attempts。
- 不建 CSV batch tables。
- 不建 users/auth/session tables。
- 不建 corporate actions / import reports。

## 建議資料表

### `symbols`

用途：V0.5 最小 symbol seed 與 validation。

欄位：

- `id uuid primary key`
- `symbol text not null unique`
- `display_name text not null`
- `market text not null`
- `instrument_type text not null`
- `tradable_status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

約束：

- `market in ('TWSE', 'TPEx')`
- `instrument_type in ('stock', 'ETF')`
- `tradable_status in ('tradable', 'halted', 'unsupported')`

### `trade_intents`

欄位：

- `id uuid primary key`
- `owner_user_id uuid not null`
- `symbol text not null`
- `strategy text not null`
- `execution_mode text not null`
- `quantity_lots integer not null`
- `target_price_original numeric(18, 6) not null`
- `target_price_effective numeric(18, 6) not null`
- `trigger_reference_price_type text not null`
- `trading_date date not null`
- `time_in_force text not null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`
- `cancelled_at timestamptz null`
- `triggered_at timestamptz null`

V0.5 enum values：

- `strategy in ('buy_price_alert', 'sell_price_alert')`
- `execution_mode = 'notify_only'`
- `time_in_force = 'day'`
- `status in ('scheduled', 'active', 'triggered', 'cancelled')`
- `trigger_reference_price_type in ('ask', 'bid', 'last_fallback')`

Indexes：

- `(owner_user_id, status, trading_date)`
- `(symbol, status, trading_date)`
- `(owner_user_id, symbol, strategy, target_price_effective, quantity_lots, trading_date, status)` 可用於 duplicate check，實作上可用 partial unique index 限制 active/scheduled。

### `trigger_records`

欄位：

- `id uuid primary key`
- `trade_intent_id uuid not null unique`
- `owner_user_id uuid not null`
- `symbol text not null`
- `quote_snapshot jsonb not null`
- `target_price_effective numeric(18, 6) not null`
- `trigger_price numeric(18, 6) not null`
- `trigger_reference_price_type text not null`
- `fallback_used boolean not null default false`
- `triggered_at timestamptz not null`

### `notifications`

欄位：

- `id uuid primary key`
- `owner_user_id uuid not null`
- `trade_intent_id uuid null`
- `type text not null`
- `rendered_title text not null`
- `rendered_body text not null`
- `read_at timestamptz null`
- `created_at timestamptz not null`

V0.5 `type`：

- `price_triggered`

Indexes：

- `(owner_user_id, created_at desc)`
- `(owner_user_id, read_at)`

## Migration 要求

- 使用 Alembic 管理 schema。
- Migration 必須可 upgrade/downgrade。
- Decimal 欄位不可用 float。
- 所有 timestamp 使用 `timestamptz`。
- 所有 table 有 `created_at`，狀態會變更的 table 有 `updated_at`。

## 驗收條件

- [ ] Alembic 可建立並 downgrade V0.5 schema。
- [ ] Schema 支援 symbol validation、trade intent create/list/cancel、trigger metadata、notification list/read。
- [ ] 不建立 V0.5 未使用的 audit/outbox/delivery/import tables。
- [ ] DB timestamps 使用 UTC，並保存 `trading_date`。
- [ ] Migration tests 可在 PostgreSQL 上執行。

## 測試要求

- Migration upgrade test。
- Migration downgrade test。
- Constraint test：`quantity_lots <= 0` 不可被寫入。
- Constraint test：不合法 enum value 不可被寫入。
- Unique test：同一 `trade_intent_id` 不可有兩筆 `trigger_records`。

## 工程注意事項

- 若使用 SQLAlchemy，model enum 可以先用字串欄位 + check constraint，避免早期 enum migration 難改。
- V1 需要新增更多 status，不要把 Python enum 寫死到難以擴充。
- Duplicate intent partial unique index 若過早複雜，可先在 command transaction 中用 row lock/check 實作，但需有測試。
