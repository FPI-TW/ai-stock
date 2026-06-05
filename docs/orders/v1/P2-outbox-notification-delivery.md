# P2：通知交付保證（Transactional Outbox + Delivery Worker）

## Metadata

- 分層：上線後
- 優先序：P1
- ROM：**M**
- 依賴：L2（correlation id）；refactor 既有 trigger / notification 路徑
- 交付版本：V1
- 併自舊工單：BE-V1-06-outbox-notification-worker

## 背景

V0.5 觸發後**直接寫 `notifications` 表 + best-effort telegram**（`src/app/commands/trigger_intent.py`、`services/telegram_notification.py`），無交付保證、無重試、無 delivery 狀態。上線初期可接受，但要把「偶爾漏通知」修掉就靠本票。對齊 `docs/domain-spec.md` §16（transactional outbox）、§12（通知失敗規則 / delivery 狀態機）。

## 目標

- **Transactional outbox**：觸發 transaction 內同時更新 `TradeIntent`/OCO sibling/`TriggerEvent`/寫 `OutboxEvent`（at-least-once）。
- **`NotificationDelivery`**：`channel`、`status = pending|sent|failed_retryable|failed_permanent|skipped`、`sent_at`、`error_code`；unique `(trigger_event_id, channel)`。
- **Notification worker**：讀 outbox（`FOR UPDATE SKIP LOCKED` + `locked_by`/`locked_until`/`attempt_count`/`available_at`），多 worker 可並行；handler idempotent；lock timeout 可重試。
- **重試策略**：telegram retryable（rate limit/timeout/5xx）3 次 exponential backoff；permanent（封鎖/chat not found/forbidden）→ binding 標 `failed_permanent`/`revoked`，後續不送 telegram，保留 in_app + UI 顯示綁定異常。
- **Skip reasons**：`telegram_skipped_disabled`/`telegram_skipped_unbound`/`skipped_account_disabled`/`skipped_cancelled`。
- **Rendered 快照**：`template_key`/`template_version`/`message_data`/`rendered_title`/`rendered_body`/`rendered_at`；telegram 另存 `telegram_message_id`/`sent_text_hash`。
- 既有 trigger 路徑 refactor 走 outbox（業務語意不變）。

## 非目標

- 不做 telegram 綁定流程（P3）；本票只消費 binding 狀態決定 skip/send。
- 不做通知偏好 UI（P3）。
- 不改通知 type / template 文案（沿用既有 + P1/P4 新增者）。

## DB / 介面

- `outbox_events`：`id`、`event_type`、`payload jsonb`、`status`、`available_at`、`locked_by`、`locked_until`、`attempt_count`、`correlation_id`、`created_at`。
- `notification_deliveries`：見上；+ rendered 快照欄位。
- 約束：`TriggerEvent(trade_intent_id)` unique（既有）、`NotificationDelivery(trigger_event_id, channel)` unique。

## 依賴與交接

- L2 correlation id 延伸到 outbox / delivery attempt。
- P3 telegram binding 狀態決定 telegram delivery 的 send/skip/permanent-revoke。
- P5 監控讀 outbox/delivery backlog 與失敗率。

## 驗收條件

- [ ] 觸發 transaction 同時寫 intent/sibling/trigger/outbox；commit 後 worker 才見。
- [ ] worker 多實例並行不重複送（SKIP LOCKED + unique）。
- [ ] telegram retryable 3 次 backoff；permanent → binding revoke + 保留 in_app。
- [ ] delivery 狀態機正確（pending→sent/failed_*/skipped）；各 skip reason 命中。
- [ ] rendered 快照保存，template 改版不影響歷史通知。
- [ ] 既有觸發行為 refactor 後不變（既有 tests 全綠）。

## 測試要求

- Unit：delivery 狀態轉移；retry backoff；skip reason 判定。
- Integration：outbox claim/lock 並行；telegram retryable/permanent；transaction 原子性；冪等重放不重複副作用。

## 工程注意事項

- Postgres-everywhere：outbox 用 PG `SKIP LOCKED`，不引入訊息佇列 / Redis。
- 接受極少數「API 成功但 DB 更新失敗」殘餘重複風險，用 delivery attempt + telegram message id 降低（§16）。
