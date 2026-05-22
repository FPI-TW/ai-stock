# BE-V1-06：Outbox、Notification Delivery Attempts、Worker Retry

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：35h
- 依賴：BE-V1-01, BE-V0.5-09, BE-V0.5-10
- 交付版本：V1

## 背景

BE-V0.5-09 的 trigger transaction 在同一 DB transaction 內完成：

1. Lock intent
2. Status guard
3. 寫 `trigger_events`
4. 更新 `trade_intents.status = triggered`
5. **直接** 建立 `notifications`

V1 要把第 5 步改為 transactional outbox（domain-spec §16），原因：

- Notification 將擴增 `in_app` + `telegram` 兩個 channel，channel-level retry 不可影響 trigger transaction。
- BE-V1-05 paused/resumed 也會產生 notification，需要走同樣的 outbox。
- Telegram dispatch 可能失敗、需要重試與 rate-limit 控制。
- BE-V1-15 監控 backlog metrics 需要 worker queue 視角。

## 目標

- 新增 `outbox_events` table（at-least-once）。
- 新增 `notification_deliveries` table（per-channel delivery state）。
- 新增 `notification_delivery_attempts` table（per attempt log）。
- BE-V0.5-09 trigger transaction 第 5 步改寫：不再直接寫 `notifications.rendered_*`，改為寫 `outbox_events` row。
- 新增 `NotificationWorker`：讀 outbox → claim → render → 建立 `notifications` row（in_app）→ 透過 `TelegramAdapter` Protocol 處理 telegram channel stub → 更新 delivery state。
- 支援多 worker 並行：透過 `locked_by` / `locked_until` claim。
- Telegram retryable / permanent error 的 delivery state 與 backoff 骨架（正式 adapter、binding revoke、binding failed notification 由 BE-V1-11 落地）。
- Notification template registry（V1 templates by code, not admin-editable）。

## 非目標

- 不做 Telegram adapter 實作（BE-V1-11）。
- 不做 notification settings UI（BE-V1-12）。
- 不做 admin monitoring dashboard（BE-V1-15），但 outbox / delivery schema 提供查詢端點。
- 不做 user-level notification preference（BE-V1-12）。

## DB Schema

### `outbox_events`

- `id uuid primary key`
- `event_type text not null`（`trigger_intent_triggered` / `intent_paused_data_issue` / `intent_paused_market_status` / `intent_resumed` / `intent_invalid_for_day` / `intent_cancelled_by_account_disabled` / `market_closed_rescheduled`）
- `aggregate_type text not null`（`trade_intent` / `system`）
- `aggregate_id uuid not null`
- `owner_user_id uuid not null references users(id)`
- `payload jsonb not null`（已 render 前的 message_data + 必要 ids）
- `correlation_id text not null`
- `available_at timestamptz not null default now()`
- `status text not null check (status in ('pending', 'in_progress', 'completed', 'failed'))`
- `locked_by text null`（worker id）
- `locked_until timestamptz null`
- `attempt_count integer not null default 0`
- `last_error text null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Indexes：

- `(status, available_at)` 用於 worker poll。
- `(aggregate_type, aggregate_id)`。
- `correlation_id`。

### `notification_deliveries`

- `id uuid primary key`
- `outbox_event_id uuid not null references outbox_events(id)`
- `trigger_event_id uuid null references trigger_events(id)`（若是 price_triggered）
- `owner_user_id uuid not null references users(id)`
- `notification_id uuid null references notifications(id)`（in_app 才有）
- `channel text not null check (channel in ('in_app', 'telegram'))`
- `status text not null check (status in ('pending', 'sent', 'failed_retryable', 'failed_permanent', 'skipped'))`
- `skip_reason text null`（`telegram_skipped_disabled` / `telegram_skipped_unbound` / `skipped_account_disabled` / `skipped_cancelled`）
- `template_key text not null`
- `template_version text not null`
- `message_data jsonb not null`
- `rendered_title text null`
- `rendered_body text null`
- `rendered_at timestamptz null`
- `external_message_id text null`（telegram_message_id）
- `sent_at timestamptz null`
- `error_code text null`
- `attempt_count integer not null default 0`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Unique：`(trigger_event_id, channel)`（domain-spec §16 約束）。

### `notification_delivery_attempts`

- `id uuid primary key`
- `delivery_id uuid not null references notification_deliveries(id)`
- `attempt_number integer not null`
- `started_at timestamptz not null`
- `finished_at timestamptz null`
- `outcome text not null check (outcome in ('sent', 'retryable_error', 'permanent_error'))`
- `error_code text null`
- `error_detail text null`
- `correlation_id text not null`

### `notifications`（既有 V0.5）

- 不刪欄位；新增：
  - `template_key text not null default 'price_triggered'`
  - `template_version text not null default 'v1'`
  - `message_data jsonb not null default '{}'::jsonb`
- 舊資料 migration 補預設值。
- `notifications` 仍只代表 in_app delivery 的可顯示 record；新建仍由 worker 寫入，但 worker 內由 `notification_deliveries.notification_id` link。

## 流程

### Trigger transaction（BE-V0.5-09 改寫）

原 transaction 第 5 步「建立 notifications」改為：

5. 建立 `outbox_events` row，event_type = `trigger_intent_triggered`，payload 包含：
   - `tradeIntentId`、`triggerEventId`、`ownerUserId`、`triggerContext`、`symbol`、`strategy`、`targetPriceEffective`、`triggerPrice`、`fallbackUsed`、`quoteSnapshot`。

整個 transaction 完成。`notifications` 表本身不再由 trigger transaction 寫入。

### NotificationWorker

`app/workers/notification_worker.py`：

```python
class NotificationWorker:
    def run(self, *, worker_id: str, batch_size: int = 10, poll_interval: float = 1.0) -> None: ...
    def claim_batch(self, *, worker_id: str, batch_size: int) -> list[OutboxEvent]: ...
    def process(self, event: OutboxEvent) -> ProcessResult: ...
```

Claim：

```sql
UPDATE outbox_events
SET status = 'in_progress',
    locked_by = :worker_id,
    locked_until = now() + interval '60 seconds',
    updated_at = now()
WHERE id IN (
    SELECT id FROM outbox_events
    WHERE status = 'pending' AND available_at <= now()
    ORDER BY available_at
    LIMIT :batch_size
    FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

Process：

1. Lookup user `notification_settings`（BE-V1-12 提供；本工單先以 stub `NotificationSettingsService` 回固定值：`in_app=true`，`telegram=if_bound`）。
2. 對每個適用 channel 建立 / 更新 `notification_deliveries`。
3. 套 template（`NotificationTemplateRegistry`）render title/body。
4. `in_app` channel：在同 transaction 內建立 `notifications` row（user-visible），`notification_deliveries.notification_id` link。
5. `telegram` channel：呼叫 `TelegramAdapter.send(message)` Protocol stub（BE-V1-11 替換正式 adapter）。
   - 成功 → `sent_at`、`external_message_id`、`status = sent`。
   - Retryable error（rate limit、5xx、timeout）→ `status = failed_retryable`，`outbox_events.attempt_count++`、`available_at = now() + backoff`，最多 3 次。
   - Permanent error（bot blocked、chat not found）→ `status = failed_permanent`。`telegram_bindings.status = 'revoked'` 與 `telegram_binding_failed` 通知由 BE-V1-11 接手。
6. 寫 `notification_delivery_attempts`。
7. 全部 channel 完成 → `outbox_events.status = completed`。
8. 任一 channel 仍 retryable → `outbox_events.status = pending`，等下一次 poll。
9. 達 max attempts 仍失敗 → `status = failed`（in_app 例外：必須成功；in_app DB 寫入失敗為 retryable）。

Backoff：`min(60s * 2^attempt, 600s)`。

### Notification template registry

`app/services/notifications/templates/`：

- `price_triggered.py`
- `intent_paused_data_issue.py`
- `intent_paused_market_status.py`
- `intent_resumed.py`
- `intent_invalid_for_day.py`
- `market_closed_rescheduled.py`
- `telegram_binding_failed.py`

每個 template：

```python
class PriceTriggeredTemplate(NotificationTemplate):
    key = "price_triggered"
    version = "v1"

    def render(self, data: dict, channel: Channel) -> RenderedMessage: ...
```

- `rendered_title` / `rendered_body` 對 in_app 與 telegram 可不同格式（telegram 用 markdown）。
- 文案必含「僅通知、未下單、不保證成交」（domain-spec §12）。
- 除息調整時顯示原始 + 調整金額 + 有效目標價（BE-V1-09 補欄位）。

### Idempotency

- `notification_deliveries (trigger_event_id, channel)` unique → 重 claim 不會建第二筆 delivery。
- Worker 對已有 `status = sent` 的 channel 直接跳過。
- Telegram dispatch 仍有極小機率 API 成功但 DB 更新失敗造成重複（domain-spec §16 接受此風險），但透過 `sent_text_hash` + Telegram 內 dedup 機制盡量降低。

## 通知失敗對 trade intent 的影響

- `TradeIntent` 條件成立後仍轉 `triggered`，不因通知失敗回滾（domain-spec §12）。
- 通知失敗只影響 `NotificationDelivery` 狀態。
- 達 max attempts 後不再重試，留給 BE-V1-15 monitoring。

## API

### `GET /notifications`（既有）

- Response 仍只回 in_app notifications。
- 加 `templateVersion`、`messageData` 給前端做動態 UI。

### `GET /admin/outbox/backlog`

- Response：pending / in_progress / failed_retryable 各統計。
- Owner: admin。
- 為 BE-V1-15 dashboard 用。

### `GET /admin/notification-deliveries`（cursor pagination）

- Filter：`channel`、`status`、`ownerUserId`、`triggerEventId`、日期區間。
- Owner: admin（read with audit reason，BE-V1-13 / 16 完整化）。

## 驗收條件

- [ ] `outbox_events`、`notification_deliveries`、`notification_delivery_attempts` migration 可 upgrade / downgrade。
- [ ] BE-V0.5-09 trigger transaction 改為寫 outbox event，舊 integration tests（V0.5-12）改 fixture 後仍綠。
- [ ] Notification worker 可單 worker 啟動，輪詢處理；多 worker 不會 double-process（SKIP LOCKED）。
- [ ] in_app channel：worker 寫入 `notifications` + `notification_deliveries` 一致。
- [ ] telegram channel stub：retryable error 進入 backoff；permanent error 將 delivery 標為 `failed_permanent`，不依賴 `telegram_bindings` table。
- [ ] `(trigger_event_id, channel)` unique 強制；重複 claim 同一 event 不會產生第二筆 delivery。
- [ ] 達 max retry 後 `outbox_events.status = failed`，但 in_app delivery 確保不會 stuck（in_app retry strategy 必須最終完成）。
- [ ] 觸發 transaction 與 outbox dispatch 之間：DB rollback 不會導致通知漏發；outbox dispatch 失敗不會影響 trigger 已完成。

## 測試要求

- Unit：claim SQL 對 SKIP LOCKED 行為（PostgreSQL 整合）。
- Unit：template render（每種 type + channel）。
- Unit：Telegram retryable / permanent error 分類。
- Integration：trigger → outbox → in_app notification + telegram delivery（Telegram adapter stub success）。
- Integration：Telegram permanent error → delivery `failed_permanent`，outbox 可完成或依 policy 結束，不觸碰 binding 狀態。
- Integration：worker crash 後 `locked_until` 過期，另一 worker 重 claim。
- Integration：outbox 已 completed event 不會被再次 process。
- Integration：notification settings 停用 telegram → delivery `status = skipped`，`skip_reason = telegram_skipped_disabled`。
- Integration：account disabled user 的 delivery `skip_reason = skipped_account_disabled`。

## 工程注意事項

- 不要在 worker 內共用 web request 的 SQLAlchemy session；worker 自己開 session per claim batch。
- 對 PostgreSQL：`FOR UPDATE SKIP LOCKED` 在 outbox table 不能加 `NOWAIT`，會 starve 高 attempt event。
- Backoff 透過 `available_at` 控制；不要在 worker process 內 `sleep` 阻塞。
- Telegram adapter 是 Protocol，本工單注入 stub（in-memory list）即可；BE-V1-11 替換真實實作並處理 binding revoke / binding failed notification。
- `notifications.rendered_*` 仍由 worker 寫入，不要回頭把 render 邏輯放回 trigger transaction。
- Template version 必須保存到 delivery row：避免 template 改版後歷史通知 render 不出來（V1 暫不重 render 歷史）。
- Outbox row 不要 hard delete；retention 由 BE-V1-16 處理。
- Worker shutdown 時把自己的 `locked_by` row 釋放回 `pending`，避免 `locked_until` 期間其他 worker 不 claim。
- 多 worker scaling 與 process supervisor（systemd / supervisord）由 BE-V1-17 deployment 補；本工單只確保 logic 可水平擴展。
