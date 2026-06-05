# L2：平台守門 / 防護（稽核 + 冪等 + 限流 + 建立上限 + 緊急停止鈕）

## Metadata

- 分層：**上線前（必要）**
- 優先序：P0
- ROM：**M**
- 依賴：L1（auth context + RateLimiter primitive + audit stub 介面 + admin 2FA）
- 交付版本：V1
- 併自舊工單：BE-V1-16-audit-rate-limit-retention（audit + rate-limit 部分）、+ idempotency、+ 建立上限、+ 最小 kill switch

## 背景

L1 把身分做好後，平台需要一層「防護與紀錄」才能安全上線給真人用，對齊 `docs/domain-spec.md` §16（command/outbox/idempotency/error envelope）、§17（audit log）、§15（建立上限）、§18（kill switch）、§13（登入/操作限流）。

本票把四件跨業務的守門能力收一起：**稽核紀錄、冪等、全域限流、建立上限**，外加上線初期最重要的營運安全閥——**最小緊急停止鈕（kill switch）**。這些都不屬於單一業務，集中一票避免散落。

L1↔L2 循環解法：L1 用 audit **logging stub** 與只接 auth buckets 的 RateLimiter；本票補 `audit_events` 真表 + 把全 endpoint 接上限流，並 refactor L1 的 stub callers（callers signature 不變）。

## 目標

- **Audit log**：`audit_events` 表 + `AuditEventWriter`（取代 L1 stub），覆蓋 §17 最低事件集；refactor 所有既有 stub callers。
- **Idempotency**：`idempotency_keys` 表 + manager，套用 `CreateTradeIntent` / `CancelTradeIntent` / `CancelTradeIntentGroup`（後者待 P1，本票先建機制）；缺 key 拒絕、same key+same payload 回同結果、same key+diff payload 回 409、保存 24h。
- **Request / correlation id 貫穿**：API `X-Request-Id`、command `request_id`、audit `request_id`、（outbox `correlation_id` 待 P2）。
- **建立上限 enforcement**：單一 user active/scheduled ≤ 200、單一 user 單一 symbol ≤ 20；env 預設（admin 可調 endpoint 屬 P5，本票注入式預設不被 P5 阻塞）。
- **全域限流**：沿用 L1 的 `rate_limit_buckets` + `RateLimiter`，擴到所有 mutating endpoint（`create_intent`、`cancel_intent`、未來 webhook）；回 `RATE_LIMITED` + `Retry-After`。
- **最小 Kill Switch**：`system_flags` 表 + 全域停止觸發旗標 + admin toggle endpoint + evaluator 每輪檢查（見下節）。

## 非目標

- 不建 RateLimiter primitive 本體（L1 已建）。
- 不做 admin 可調 config endpoint（P5）；本票限額用 env 預設。
- 不做 retention / 匿名化（P6）；本票只建 audit 表，清理政策屬 P6。
- 不做 kill switch 分層（per-symbol / telegram-only / corporate-action）—— P5 在本票全域旗標上擴充。
- 不做 transactional outbox（P2）；本票 correlation id 先涵蓋 command/audit。

## DB Schema

### `audit_events`
- `id uuid pk`、`event_type text not null`、`actor_type text check (in 'user','system','admin')`、`actor_id uuid null`、`occurred_at timestamptz not null`、`metadata jsonb`、`request_id text null`
- Index：`(event_type, occurred_at)`、`(actor_id, occurred_at)`
- 最低事件（§17）：`intent_created`/`intent_activated`/`intent_triggered`/`intent_expired`/`intent_cancelled`/`notification_sent`/`notification_failed`/`corporate_action_adjustment_applied`/`oco_sibling_cancelled`/`account_invited`/`account_activated`/`account_disabled`/`admin_override_applied`/`kill_switch_enabled`/`kill_switch_disabled`（部分事件來源在後續票，本票建表 + writer，事件由各票寫入）。

### `idempotency_keys`
- `id uuid pk`、`user_id fk`、`key text`、`endpoint text`、`request_hash text`、`response_snapshot jsonb`、`status text`、`created_at`、`expires_at`（24h）
- Unique：`(user_id, key)`。

### `system_flags`（Kill Switch）
- `flag_key text pk`（V1 只用 `global_trigger_halt`）、`enabled boolean not null default false`、`updated_by uuid null`、`updated_at timestamptz`、`reason text null`

## 最小 Kill Switch 設計

- **行為**：admin 一鍵「全域停止觸發」。開啟後 quote evaluator **照常抓 quote**（UI 行情/最後更新時間不受影響），但**不判定到價、不產 `TriggerEvent`、不發任何通知**（站內 + Telegram 皆停）。
- **操作**：`POST /admin/kill-switch` `{enabled, reason}`，限 admin（require `mfa_verified`），**reason 必填**；開/關各寫 audit（`kill_switch_enabled`/`kill_switch_disabled`，含 actor/time/reason）。`GET /admin/kill-switch` 回當前狀態。
- **生效**：evaluator 每輪評估前讀旗標（in-process cache 30s TTL + toggle 時主動失效）→ 數秒內生效。
- **限制（止血非重播）**：停止期間錯過的到價**不回補**；關閉後從當下最新 quote 重新評估，不回放暫停期間行情。

> ⚠️ **與 spec §18 的刻意落差（需知情）**：domain-spec §18 要求 kill switch **「必須分層控制」**，共 **5 層**：①全系統停止觸發 ②特定 symbol 停止觸發 ③停止 Telegram 發送 ④停止所有外部通知但保留站內 ⑤停止 corporate action adjustment 套用。
>
> **L2 只實作第 ① 層（全系統停止觸發）**，作為上線初期最小止血閥；**②③④⑤ 四層延後至 P5**。這是經產品確認的刻意範圍縮小（上線先有「全部喊停」一鍵即可），**非遺漏**。
>
> **後果**：上線時只有「全域停止」一顆鈕，**無法只停某一檔、只停 Telegram、或只停除息調整**——要嘛全停、要嘛全開。若上線後出現「只想停單一問題標的」的需求，需等 P5。
>
> **P5 交接**：P5 在本票的 `system_flags` 全域旗標上**擴充**其餘 4 層（per-symbol / telegram-only / external-only / corporate-action），**繼承不重建**。

## API（介面契約）

- 既有 `POST /trade-intents`、`POST /trade-intents/{id}/cancel` 改為**必帶** `Idempotency-Key` header：缺 → `IDEMPOTENCY_KEY_REQUIRED`(400)；衝突 → `IDEMPOTENCY_KEY_CONFLICT`(409)；重放 → 回原結果。
- 建立超限 → `USER_INTENT_LIMIT_EXCEEDED`(409) / `SYMBOL_INTENT_LIMIT_EXCEEDED`(409)。
- 任意 mutating endpoint 超頻 → `RATE_LIMITED`(429) + `Retry-After`。
- `POST /admin/kill-switch` / `GET /admin/kill-switch`（見上）。

> Error code 使用 §16 正典集合，勿自創同義碼（如勿用 `INTENT_LIMIT_REACHED`）。

## 驗收條件

- [ ] `audit_events` / `idempotency_keys` / `system_flags` migration 可 up/down。
- [ ] L1 的 audit stub callers 全部 refactor 到 `AuditEventWriter`，signature 不變，既有 tests 全綠。
- [ ] create/cancel 缺 idempotency key → 400；same key+same payload → 同結果不重複副作用；same key+diff payload → 409。
- [ ] 第 201 筆 active/scheduled intent → `USER_INTENT_LIMIT_EXCEEDED`；單 symbol 第 21 筆 → `SYMBOL_INTENT_LIMIT_EXCEEDED`。
- [ ] 超頻 mutating → `RATE_LIMITED` + `Retry-After`。
- [ ] **Kill switch on：evaluator 仍抓 quote 但不產 TriggerEvent、不發通知；數秒內生效；開/關寫 audit；reason 必填。**
- [ ] Kill switch off 後恢復評估，不回放暫停期間行情。
- [ ] 非 admin / 未 2fa 不能操作 kill switch。

## 測試要求

- Unit：idempotency hash 判定（same/diff payload）；建立上限邊界（200/201、20/21）；kill switch 旗標讀取 + cache 失效。
- Integration：audit writer 寫入 + 既有 callers refactor 後行為不變；create 重放冪等；kill switch on → evaluator 不觸發（注入 quote 達標也不發）→ off 恢復；rate limit 跨 endpoint。

## 工程注意事項

- audit 不可寫入明文密碼 / token / 完整 PII；metadata 只放必要欄位。
- idempotency response snapshot 不存敏感欄位。
- kill switch cache：in-process（對齊 Postgres-everywhere / 單機，勿引入 Redis），toggle endpoint 主動 invalidate。
- 限額 enforcement 用注入式 `IntentLimitProvider`（env 預設），P5 注入 admin 可調版覆蓋，本票不被 P5 阻塞。
- correlation id：本票先打通 command/audit，P2 outbox 接續同一 id。
