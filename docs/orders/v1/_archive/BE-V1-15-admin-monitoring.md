# BE-V1-15：Admin Monitoring、Alerts、Backlog Metrics、Kill Switches

## Metadata

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-04, BE-V1-05, BE-V1-06, BE-V1-09, BE-V1-11
- 交付版本：V1

## 背景

V1 必須提供 admin-facing 監控與 kill switch（domain-spec §18）。前面工單分別建立了：

- BE-V1-04 scheduled jobs。
- BE-V1-05 quote provider status + symbol health。
- BE-V1-06 outbox / notification deliveries backlog。
- BE-V1-09 corporate action snapshot 套用情況。
- BE-V1-11 telegram delivery 結果。

本工單把它們聚合到一個 admin monitoring 面板 + alert 與 kill switch 控制平面。

最低告警（domain-spec §18）：

- quote source stale / 連線失敗。
- 某 symbol quote validation 連續失敗。
- Telegram delivery failure rate 過高。
- corporate action import job 失敗。
- snapshot 未在開盤前產生。
- notification worker backlog 過高。
- trigger worker backlog 過高。

Kill switch 層級：

- 全系統停止觸發。
- 特定 symbol 停止觸發。
- 停止 Telegram 發送。
- 停止所有外部通知（保留 in_app）。
- 停止 corporate action adjustment 套用。

停止觸發時，quote 仍繼續抓取（用於 UI / 健康監控）。

## 目標

- 新增 `kill_switches` table。
- 新增 `KillSwitchService`：evaluator / outbox worker / importer 統一查詢。
- 新增 admin metrics endpoints（read-only summary）。
- 新增 admin alerts endpoints（結構化 alert log 查看；alert 產生由各模組透過 alert dispatcher 寫入）。
- 新增 `AlertDispatcher`：模組共用，輸出結構化 log + 可選外部通道（PagerDuty / email；本工單留 adapter）。
- 新增 scheduler process（APScheduler / cron-like）統一觸發 V1-03 / V1-04 / V1-09 importer + jobs。
- Backlog metrics endpoint。
- Kill switch 寫 audit。

## 非目標

- 不做 user-facing 服務狀態 page（V1 不需要）。
- 不做正式 PagerDuty / OpsGenie 整合（adapter stub）。
- 不做 long-term metrics 儲存（V1 用 PostgreSQL 即可，後續移到 Prometheus / Datadog）。

## DB Schema

### `kill_switches`

- `id uuid primary key`
- `scope text not null check (scope in ('system', 'symbol', 'telegram', 'external_channels', 'corporate_action'))`
- `target text null`（symbol scope 時填 symbol）
- `enabled boolean not null`
- `reason text not null`
- `enabled_by_admin_id uuid not null references users(id)`
- `enabled_at timestamptz not null`
- `disabled_by_admin_id uuid null references users(id)`
- `disabled_at timestamptz null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Unique partial：`(scope, target) where disabled_at is null`，避免同 scope 重複啟用。

### `admin_alerts`

- `id uuid primary key`
- `alert_type text not null`（e.g. `quote_provider_unhealthy`、`notification_backlog_high`、`snapshot_missing`）
- `severity text not null check (severity in ('info', 'warning', 'critical'))`
- `target text null`（symbol / job_name 等 context）
- `summary text not null`
- `detail jsonb not null`
- `correlation_id text null`
- `created_at timestamptz not null`
- `acknowledged_by_admin_id uuid null references users(id)`
- `acknowledged_at timestamptz null`

Indexes：`(severity, created_at desc)`、`(alert_type, created_at desc)`、`(acknowledged_at)`。

## Architecture

### `KillSwitchService`

```python
class KillSwitchService:
    def is_system_triggering_disabled(self) -> bool: ...
    def is_symbol_triggering_disabled(self, symbol: str) -> bool: ...
    def is_telegram_disabled(self) -> bool: ...
    def is_external_channels_disabled(self) -> bool: ...
    def is_corporate_action_adjustment_disabled(self) -> bool: ...
```

- Cache 進程內，TTL 30s + 寫入 invalidation hook（admin endpoint 寫 kill switch 後立即 invalidate）。
- Evaluator 每輪檢查 system + symbol scope。
- Outbox worker 對每 delivery 檢查 telegram / external channels scope。
- Corporate action snapshot 在 apply 前檢查 scope。

### `AlertDispatcher`

```python
class AlertDispatcher:
    def dispatch(self, alert: AdminAlert) -> None: ...
```

- 寫 `admin_alerts` row。
- 寫 structured log。
- 呼叫外部 adapter（stub）。

各模組接入：

- BE-V1-05 quote provider unhealthy / symbol unhealthy duration 過長 → dispatch。
- BE-V1-06 worker backlog > threshold → dispatch（透過 backlog poller）。
- BE-V1-04 expire job 發現 unfired active intents → dispatch。
- BE-V1-09 importer 失敗 / snapshot 未在開盤前產生 → dispatch。
- BE-V1-11 telegram delivery failure rate > threshold（每 15 分鐘窗口）→ dispatch。

### `BacklogPoller`

`app/workers/backlog_poller.py`：

- 每 1 分鐘查 outbox / notification_deliveries 統計。
- 超過 thresholds：
  - `outbox_pending_count > 1000` → critical。
  - `outbox_in_progress_age > 5min` → warning。
  - `telegram_failed_retryable_rate_15min > 20%` → warning。
- 透過 AlertDispatcher 寫 alert（去重：同類型 1 小時內只寫一次，acknowledged 後計時 reset）。

### Scheduler

新建 `app/workers/scheduler.py`，使用 APScheduler（in-process scheduler）：

- 每交易日 08:30 → calendar import + symbol import。
- 每交易日 08:40 → corporate action snapshot apply。
- 每交易日 09:00 → activate_scheduled_intents。
- 每交易日 13:30 / 12:30（半日）+ 1 分鐘 → expire_day_intents + mark_unfired_active_alerts。
- 每 5 分鐘 → backlog poll。
- 每 30 分鐘 → cleanup expired csv batch drafts + idempotency keys。

Scheduler 透過 `scheduled_job_executions` table（BE-V1-04 建立）保證 idempotency。

## API

### Kill switch endpoints

#### `GET /admin/kill-switches`

- 列出當前所有 active kill switches。

#### `POST /admin/kill-switches`

Body：

```json
{
  "scope": "symbol",
  "target": "2330",
  "reason": "vendor data anomaly"
}
```

- Enable kill switch。
- 寫 audit `kill_switch_enabled`。
- Reason 強制。

#### `DELETE /admin/kill-switches/{id}`

- Disable kill switch。
- 寫 audit `kill_switch_disabled`。

### Metrics endpoints

#### `GET /admin/metrics/quote-provider`

- 同 BE-V1-05 status。
- 加上歷史 reconnect_count。

#### `GET /admin/metrics/outbox-backlog`

```json
{
  "data": {
    "pendingCount": 12,
    "inProgressCount": 3,
    "failedCount": 1,
    "oldestPendingAt": "...",
    "telegramFailureRate15min": 0.05
  }
}
```

#### `GET /admin/metrics/notification-deliveries`

- channel × status × 24h aggregate。

#### `GET /admin/metrics/snapshot-status`

- 今日 snapshot 是否完成、缺漏 symbols、disputed counts。

#### `GET /admin/metrics/jobs`

- 最近 30 天 scheduled_job_executions 摘要。

### Alerts endpoints

#### `GET /admin/alerts`

- Cursor pagination + filter（severity / acknowledged / alert_type / date range）。

#### `POST /admin/alerts/{id}/acknowledge`

- Set acknowledged_by + acknowledged_at。
- Audit。

## Kill Switch 行為對齊

- `scope = system`：evaluator 不寫 `trigger_events`、不更新 intent 為 triggered；quote 仍抓取與 health monitor 仍運作。
- `scope = symbol`：對特定 symbol 不觸發。
- `scope = telegram`：BE-V1-06 worker 對 telegram delivery 全部 skip（skip_reason = `telegram_skipped_kill_switch`）。
- `scope = external_channels`：所有非 in_app channel 全 skip。
- `scope = corporate_action`：scheduler 不執行 apply snapshot；既有 snapshot 仍生效。

啟用 kill switch 時不回滾既有狀態；只影響未來。

## 驗收條件

- [ ] `kill_switches` / `admin_alerts` migration 可 upgrade / downgrade。
- [ ] `KillSwitchService` 對 evaluator / worker / importer 行為改變正確。
- [ ] Kill switch enable / disable 寫 audit。
- [ ] Scheduler 在交易日依排程執行 import / activate / expire / backlog poll。
- [ ] BacklogPoller 超過 threshold 時寫入 `admin_alerts`。
- [ ] Quote provider unhealthy / symbol unhealthy / snapshot missing 等 alert 透過 AlertDispatcher 寫入。
- [ ] Alert ack endpoint 正確更新 row。
- [ ] Admin metrics endpoints 全部需要 2FA verified + audit metrics access。

## 測試要求

- Unit：`KillSwitchService` cache + invalidation。
- Unit：Backlog threshold dedup 邏輯（同類 alert 1 小時內不重複）。
- Integration：enable system kill switch → evaluator 不觸發 trigger，quote 仍進 health monitor。
- Integration：enable symbol kill switch → 該 symbol intent 不觸發，其他 symbol 正常。
- Integration：enable telegram kill switch → outbox telegram delivery skip。
- Integration：scheduler 模擬時鐘執行 expire job 一次後不重跑（idempotency）。
- Integration：BacklogPoller 模擬 1500 pending → 寫 critical alert。
- Integration：admin ack alert → DB row 更新，audit 紀錄。

## 工程注意事項

- Scheduler 是 in-process，若 web / worker 分開部署，必須只在 worker process 啟用（避免 web 多 instance 重複跑）。
- Backlog metrics 查詢可能慢（每分鐘）→ 使用統計 index + LIMIT；避免全表 count。
- Alert dedup 不要把所有 alert 都壓在同 row update；保留歷史 row 但加 `is_duplicate_of` 欄位或在 detail.jsonb 加 occurrence_count。本工單採後者，避免新加 column。
- Kill switch 是嚴重 op，必須 admin 2FA + reason + audit；rate limit `POST /admin/kill-switches` per admin 1 / 5 秒（防誤觸）。
- Symbol scope kill switch 對 symbol master 不存在 symbol 應拒絕（避免打錯字）。
- `external_channels` scope 與 `telegram` scope 同時啟用時，effective decision 是「都 skip」；不需要互斥。
- 對 worker scaling：kill switch 必須是 cluster-wide observability（DB 為共享狀態），cache 用短 TTL 即可。
- Backlog threshold env-configurable，避免 hardcode：`OUTBOX_PENDING_CRITICAL_THRESHOLD` 等。
