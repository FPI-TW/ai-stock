# BE-V1-16：Audit Log、Rate Limits、Retention、Privacy Anonymization

## Metadata

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-01, BE-V1-06, BE-V1-13
- 交付版本：V1

## 背景

V0.5 / 早期 V1 工單一直以 structured log + stub interface 代替正式 audit 與 retention 機制。本工單把它們正式化：

- Audit event table（domain-spec §17）。
- Idempotency key 統一保存（domain-spec §16，24 小時）。
- API rate limit（登入 / password reset / mutating endpoints）。
- Retention 排程：TradeIntent 2 年、NotificationDelivery 2 年、CSV normalized rows 2 年、AuditEvent 3 年、technical logs 90 天、debug raw payload 7-30 天（hash / reference）。
- 帳號刪除 / 匿名化：email、Telegram chat id 可匿名化（domain-spec §19）。

## 目標

- 新增 `audit_events` table。
- 新增 `idempotency_keys` table（取代 BE-V1-10 csv-only impl，集中所有 mutating API）。
- **沿用** BE-V1-01 已建立的 `RateLimiter`（`rate_limit_buckets` table + token-bucket `consume()`）；**勿重建、勿另開表**。本工單只把 buckets 擴到其餘 mutating endpoint、加 env override 與 `Retry-After`。（DB-backed，Redis swap 延到 BE-V1-17 後評估。）
- 新增 retention 排程 jobs。
- 新增 user anonymization command。
- Refactor 所有之前的 audit stub callers 改寫到 `AuditEventWriter`。

## 非目標

- 不做 GDPR 完整資料匯出（V1 不支援 user 匯出資料，domain-spec §1）。
- 不做 admin 匿名化 UI（本工單只提供 command + CLI；admin UI 留後續）。
- 不做 monitoring backlog metrics 對 retention 的觀測（BE-V1-15 範圍）。

## DB Schema

### `audit_events`

- `id uuid primary key`
- `event_type text not null`
- `actor_type text not null check (actor_type in ('user', 'admin', 'system'))`
- `actor_id uuid null`
- `target_type text null`
- `target_id text null`
- `request_id text null`
- `correlation_id text null`
- `reason text null`
- `metadata jsonb not null default '{}'::jsonb`
- `occurred_at timestamptz not null`
- `created_at timestamptz not null`

Indexes：

- `(event_type, occurred_at desc)`
- `(actor_id, occurred_at desc)`
- `(target_type, target_id, occurred_at desc)`
- `occurred_at` for retention sweep

最低事件清單（domain-spec §17）：

- `intent_created` / `intent_activated` / `intent_triggered` / `intent_expired` / `intent_cancelled`
- `notification_sent` / `notification_failed`
- `corporate_action_adjustment_applied`
- `csv_batch_import_confirmed`
- `oco_sibling_cancelled`
- `account_invited` / `account_activated` / `account_disabled`
- `admin_override_applied`
- `kill_switch_enabled` / `kill_switch_disabled`
- `password_reset_completed`
- `telegram_bound` / `telegram_unbound` / `telegram_binding_failed_permanent`
- `admin_user_detail_viewed`
- `admin_2fa_enabled` / `admin_2fa_reset_completed`
- `user_anonymized`

### `idempotency_keys`

- `id uuid primary key`
- `owner_user_id uuid not null references users(id)`
- `key text not null`
- `endpoint text not null`（e.g. `POST /trade-intents`）
- `request_hash text not null`（sha256 of normalized payload）
- `response_status integer not null`
- `response_body jsonb not null`
- `created_at timestamptz not null`
- `expires_at timestamptz not null`

Unique：`(owner_user_id, key, endpoint)`。Index：`expires_at`。

### `rate_limit_buckets`（已由 BE-V1-01 建立，本工單沿用）

簡單 DB-backed token bucket（也可改 Redis）。**此 table 與 `RateLimiter` 已在 BE-V1-01 建立**，本工單不重建，只擴充 bucket configs：

- `bucket_key text primary key`
- `tokens double precision not null`
- `last_refill_at timestamptz not null`

Bucket key 例：`login:email:foo@example.com`、`password_reset:ip:1.2.3.4`（← 此兩類已由 BE-V1-01 接上）、`create_intent:user:<uuid>`（← 本工單新增）。

### `retention_executions`

`scheduled_job_executions` 已涵蓋；retention job 沿用。

## Audit Event Writer

`app/services/audit/writer.py`：

```python
class AuditEventWriter:
    def record(
        self,
        *,
        event_type: str,
        actor_type: ActorType,
        actor_id: UUID | None,
        target_type: str | None = None,
        target_id: str | None = None,
        reason: str | None = None,
        metadata: dict | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent: ...
```

- Writer 為 command-layer 服務，永遠在 outer transaction 內 write（與 business mutation 同 transaction，避免 audit 漏寫）。
- 寫入失敗應該讓 business transaction 失敗（fail-loud）；不允許 silent drop。

## Idempotency 機制

V1 mutating API 統一規則（domain-spec §16）：

- `POST /trade-intents`
- `POST /trade-intent-groups`
- `POST /trade-intents/csv/confirm`
- `POST /trade-intents/{id}/cancel`
- `POST /trade-intent-groups/{id}/cancel`

每個 request 必須帶 `Idempotency-Key` header 或 body field。

`IdempotencyService`：

```python
class IdempotencyService:
    def begin(self, *, user_id: UUID, key: str, endpoint: str, payload: dict) -> IdempotencyResult: ...
```

Result：

- `cached_response`：已存在 same key + same payload → 回原 response。
- `conflict`：existing key + different payload hash → 回 409 `IDEMPOTENCY_KEY_CONFLICT`。
- `claimed`：新 key，可 proceed；transaction commit 後寫 response。
- TTL 24 小時。

整合：將 BE-V1-10 的 csv-batch-imports idempotency_key 改為 reference `idempotency_keys`（或保留 unique，但 service 同步寫）。

## Rate Limiter

`app/services/rate_limit/limiter.py`：

```python
class RateLimiter:
    def consume(self, *, bucket_key: str, capacity: int, refill_per_second: float, cost: int = 1) -> bool: ...
```

Buckets（建議起始值，由 env override）：

- `login:email:<email>`: capacity 5 / refill 5/15min。
- `login:ip:<ip>`: capacity 30 / 15min。
- `password_reset:email:<email>`: 3 / 1h。
- `password_reset:ip:<ip>`: 10 / 1h。
- `create_intent:user:<id>`: 60 / 1min。
- `csv_confirm:user:<id>`: 5 / 1min。
- `bind_code:user:<id>`: 1 / 1min。
- `webhook:telegram`: 60 / 1min（防偽造）。

`RateLimiter` 與 `rate_limit_buckets` table **已由 BE-V1-01 建立並接上 auth buckets**（`login:*`、`password_reset:*`、invitation/bind 重寄）；本工單**沿用**，只新增上述其餘 endpoint buckets，勿重建。

DB-backed bucket 為 V1 簡化版；多 instance 部署時 contention 不嚴重（PostgreSQL row lock + update）。Redis swap 可在 BE-V1-17 後評估。

> **登入鎖定錯誤碼**：BE-V1-01 與本工單須對齊（`LOGIN_LOCKED` vs `RATE_LIMITED`），以 BE-V1-01 實作時定案者為準，本工單沿用同一碼。

`RATE_LIMITED` 統一 429 + `Retry-After` header。

## Retention Jobs

`app/workers/retention.py`：

- 每日 02:00 跑 retention sweep。
- 政策：
  - `audit_events`：刪 3 年前 row。
  - `trade_intents` / `trade_intent_groups`：刪 2 年前 terminal row（cascade child via FK ON DELETE SET NULL / RESTRICT 視 schema）。
  - `notification_deliveries` / `notification_delivery_attempts`：刪 2 年前 row。
  - `notifications`：刪 2 年前 row。
  - `csv_batch_imports` / `csv_batch_rows`：刪 2 年前 row。
  - `csv_batch_drafts`：刪 expired 24 小時前 row。
  - `idempotency_keys`：刪 expired 後 row。
  - `outbox_events`：刪已 completed 90 天前 row。
  - `trigger_events`：與 trade_intent 同步刪。
  - `symbol_import_reports` / `calendar_import_reports` / `corporate_action_import_reports`：90 天。
  - `admin_alerts`：1 年（acknowledged）/ 永久（未 ack）。
  - technical log（structured log file）：90 天，由部署層 log rotation 處理。

Job 使用 `scheduled_job_executions` 防重跑。每次刪批限 1000 row + sleep 100ms 避免長 transaction。

## User Anonymization

`AnonymizeUserCommand`：

- Input：`user_id`、`reason`（admin 必填）、optional `target_email` 對齊 check。
- Behavior：
  - user.email = `anonymized-<sha256(...)>@anonymized.local`。
  - user.password_hash = null。
  - user.status = `anonymized`（schema 加 status 值）。
  - mfa_secret = null。
  - terms_* = null。
  - 既有 trade_intent / notification / audit row 保留，但 owner reference 仍 link 同 user_id（已匿名化 email 即可）。
  - telegram_bindings 全 revoked + chat_id 改寫成 hash。
  - Audit `user_anonymized`。
- 不刪歷史資料（domain-spec §19）。

Admin CLI：

```
python -m app.cli.anonymize_user --user-id ... --reason "GDPR-like request"
```

Endpoint：留 admin endpoint stub（`POST /admin/users/{id}/anonymize`）但需要 admin 2FA + audit reason，僅 P2 範圍可選。

## API 變動

### `Idempotency-Key` header

所有 mutating endpoint 接受 header `Idempotency-Key`，可繼續支援 body field 作為 fallback（BE-V1-10 CSV）。

缺失：

- V1 mutating endpoint 缺 `Idempotency-Key` → 400 `IDEMPOTENCY_KEY_REQUIRED`。

### `GET /admin/audit-events`

- Cursor pagination + filter（event_type / actor_id / target_type / target_id / date range）。
- 需 admin 2FA + audit read access。

### Retention job 觸發 endpoint

- `POST /admin/jobs/run` 已涵蓋（BE-V1-04）；retention sweep job name = `retention_sweep`。

## Error Codes

新增：

- `IDEMPOTENCY_KEY_REQUIRED`：400。
- `IDEMPOTENCY_KEY_CONFLICT`：409。
- `RATE_LIMITED`：429（已在 V1 core，本工單實作）。

## 驗收條件

- [ ] `audit_events` migration 可 upgrade / downgrade。
- [ ] 所有 audit stub callers refactor 到 `AuditEventWriter`，舊 stub log 移除。
- [ ] `IdempotencyService` 對 same key + same payload 回 cached response；different payload 回 409。
- [ ] Login 5 次失敗 / 15 分鐘 → `RATE_LIMITED` 並 set `Retry-After`。
- [ ] Mutating endpoint 缺 `Idempotency-Key` → 400。
- [ ] Retention sweep 對各 table 正確刪 row；job idempotent。
- [ ] User anonymization：email / chat_id / mfa_secret 全部匿名化；intent / audit 保留 user_id reference。
- [ ] `GET /admin/audit-events` 對 admin without 2FA → 403。

## 測試要求

- Unit：`IdempotencyService.begin` 三種 outcome。
- Unit：`RateLimiter.consume` token bucket refill 邊界。
- Unit：`AuditEventWriter` 寫入失敗 → business transaction 一起 rollback。
- Integration：retention sweep 對 mocked 過舊 row 正確刪。
- Integration：anonymize user → email 匿名化、intent 保留。
- Integration：anonymized user login → fail（status = anonymized）。
- Integration：mutating endpoint same Idempotency-Key + same payload 兩次呼叫 → 第二次回 cached response，DB 只建一筆。
- Integration：rate limit lockout 後 `Retry-After` 後解除。
- Integration：audit events 對 `intent_created` / `intent_triggered` / `intent_cancelled` 等核心事件存在。

## 工程注意事項

- Audit 寫入應與 business mutation 共用 transaction：分散在 controller / command 各自寫的話容易漏掉。建議在 command base class 提供 `record_audit(...)` helper，命令完成前統一 flush。
- Idempotency key 對於 `Cancel` 系列要小心：cancel 重送同 key 必須回原 response，不可建立第二筆「沒事可做」的 result（domain-spec §16）。
- Rate limit bucket key 中含使用者 email / IP：log 時 hash 化，不要明文寫。
- Retention 對 FK 嚴格：trade_intents 刪除前必須先刪 trigger_events、notification_deliveries 等子表，順序固定。
- Anonymization：email unique constraint 必須容忍多筆 anonymized email；採用 `anonymized-<random>@anonymized.local` 形式。
- Audit table 寫入頻繁：適時開啟 partition by month（V1 暫不做，但 schema 設計時保留可能）。
- 不可用 cascade delete 把 audit row 也刪掉；audit 是合規證據，保留期長於業務資料。
- 對 BE-V1-13 admin disable cascade：disable 與 anonymize 是兩步驟，admin disable 後仍可後續 anonymize；schema 與流程要相容。
