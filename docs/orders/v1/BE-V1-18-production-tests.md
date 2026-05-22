# BE-V1-18：Production Contract/Integration/Adapter Test Hardening

## Metadata

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-05, BE-V1-11, BE-V1-16
- 交付版本：V1

## 背景

V0.5 integration tests（BE-V0.5-12）覆蓋本地主流程，使用 in-memory quote provider。V1 上線前必須補：

- 多 user / cross-user forbidden（domain-spec §13）。
- OCO sibling cancellation（domain-spec §5）。
- Corporate action adjustment / snapshot disputed（domain-spec §9）。
- Outbox idempotency 與 worker retry。
- Account disable cascade。
- Market calendar override。
- Quote unhealthy pause/resume。
- Telegram adapter contract。
- Symbol / corporate-action / calendar importer adapter contract。

domain-spec §23 規定的測試矩陣本工單必須全覆蓋。本工單是 V1 release gate，不是 R1/R2 主流程前置條件；各功能工單仍需在自己的範圍內交付單元與整合測試。

## 目標

- 把 V0.5 integration test suite 升級成 V1 contract / integration / adapter test，分層清楚。
- 新增 adapter contract tests（quote / telegram / corporate action / symbol / calendar）。
- 新增 cross-cutting integration tests 覆蓋 V1 corner case。
- 補 staging smoke test script，BE-V1-17 部署前用。
- 新增 CI matrix：unit / integration / adapter contract / staging smoke。
- 新增 test data factories：避免每個 test 自己 build 龐大 fixture。

## 非目標

- 不做 load testing / performance benchmarks（後續做）。
- 不做 UI E2E（前端 repo 負責）。
- 不做 chaos / network failure injection。
- 不做 vendor live integration test（vendor credentials 不入 CI）。

## 測試層次與目錄

```
tests/
  unit/              # 純邏輯，無 IO（domain / strategy / template / health monitor）
  integration/       # PostgreSQL + 真 DB + in-memory adapters
  contract/          # vendor adapter contract（mock vendor server / fixture）
  staging/           # 對 staging environment 跑 smoke + happy path
  e2e_api/           # full app stack + auth + outbox worker（同 process）
  conftest.py
```

## Coverage 矩陣

### Cross-user forbidden（BE-V1-02 base）

- User A 不能 GET / cancel User B 的 intent、notification、bracket。
- Admin user 嘗試 `POST /trade-intents/{B-id}/cancel` 仍 403。
- Admin 沒帶 reason 嘗試取 user detail → 400。

### Auth full flow

- Invitation → accept → login → refresh rotation → reuse detection → password reset → logout。
- Login lockout 5 次後解除 reset 行為。
- CSRF 缺失 / mismatched / replay。
- MFA gate：admin 沒 verify mfa → 403 admin endpoints。

### OCO

- Long bracket：tp 觸發 → sl cancelled、group triggered。
- Long bracket：sl 觸發 → tp cancelled。
- Short bracket：tp 觸發 → sl cancelled。
- Ambiguous trigger（quote 同 step 跨兩個 target）→ group + 兩腳 ambiguous_trigger，無 trigger_events，admin alert 寫入。
- User cancel 一腳 → group + sibling cancelled。

### Corporate action

- Cash dividend snapshot 套用 → effective price 更新。
- Unsupported corporate action → 相關 active intents paused_data_issue + 通知。
- Snapshot disputed → paused + 通知。
- Effective price preview / create mismatch → 409 stale_price_context。
- Round away from trigger 對 8 個 strategy × position_side 組合。

### Outbox & notifications

- Trigger transaction → outbox event → worker → in_app + telegram delivery。
- Telegram retryable error → retry 進入 backoff，最終 sent。
- Telegram permanent error → binding revoked + telegram_binding_failed 通知。
- Outbox claim SKIP LOCKED 防多 worker double process（兩個 worker 同時 claim 同 event → 只一個拿到）。
- Outbox restart：claim 過 timeout 後另一 worker 可重 claim。

### Account disable cascade

- Disable user → active / scheduled intents cancelled_by_account_disabled、telegram binding revoked、pending delivery skipped、refresh tokens revoked。
- Disable 期間有 trigger 同時嘗試 → race condition 對 disable 勝出。

### Market calendar override

- Admin override holiday → scheduled intents reschedule，發 `market_closed_rescheduled` 通知。
- 半日盤 override → expire job 使用新 session_close。
- 非交易日 evaluator 不觸發。

### Quote unhealthy pause/resume

- 連續 3 次 invalid → symbol unhealthy → active intents paused_data_issue + 通知。
- 恢復 1 筆有效 quote 未達標 → intent_resumed 通知。
- 恢復 1 筆有效 quote 達標 → 立即 triggered（trigger_context = resumed_from_data_issue）。
- Provider 全域 unhealthy → evaluator 不 trigger，恢復後自動繼續。

### Kill switch

- System scope → 觸發停止，quote feed 繼續。
- Symbol scope → 指定 symbol 不觸發，其他正常。
- Telegram scope → outbox telegram skip。

### CSV batch

- Preview → confirm full happy path。
- Confirm 同 idempotency-key + same payload → 回原 result。
- Confirm 不同 payload + same key → 409。
- Draft expired → 410。
- Row 內衝突（同 batch duplicate）→ all-or-nothing reject。

### Idempotency

- `POST /trade-intents` 同 key + payload → 第二次回 cached。
- 同 key + different payload → 409。
- Missing key → 400。

### Rate limit

- Login 5 次失敗 → 第 6 次 429 + `Retry-After`。
- Password reset 同 email > 3 / hour → 429。
- Create intent burst → 429。

### Audit log

- 核心 event（intent_created / triggered / cancelled / account_disabled / kill_switch_enabled / admin_user_detail_viewed）寫入。
- Audit 與 business mutation 同 transaction（business rollback 時 audit 也 rollback）。

## Adapter contract tests

### Quote provider adapter

- Mock vendor server fixture（HTTP / websocket）：
  - login success / failure。
  - subscribe success / quota exceeded（vendor-specific）。
  - quote callback payload → QuoteSnapshot normalize 正確。
  - reconnect after disconnect。
- 對 in-memory provider：滿足 same protocol。

### Telegram adapter

- Mock Telegram Bot API：
  - send success → external_message_id。
  - 429 rate limit → retryable + retry_after。
  - chat_not_found / bot_blocked → permanent。
  - 5xx → retryable。

### Corporate action adapter

- TWSE / TPEx mock HTML / JSON fixture：
  - 正常 fetch。
  - 缺欄位。
  - 非支援 action_type。

### Symbol importer adapter

- TWSE ISIN mock HTML fixture：
  - 正常解析。
  - column shift / 欄位異動 → fail with diagnostic。

### Calendar importer adapter

- TWSE holiday mock fixture。

## Staging smoke

`tests/staging/`：

- 對 staging 環境跑 happy path（auth → 建 intent → manual evaluate → notification）。
- 透過 env `STAGING_BASE_URL` / `STAGING_ADMIN_TOKEN` 注入。
- 不自動 run 在 CI，由部署 runbook 手動觸發。

## CI Matrix

`.github/workflows/test.yml`（或 GitLab equivalent）：

| Stage              | Command                                                         |
| ------------------ | --------------------------------------------------------------- |
| `lint`             | `make lint && make format-check`                                |
| `typecheck`        | `make typecheck`                                                |
| `unit`             | `make test` (`pytest -m 'not integration and not contract'`)     |
| `integration`      | `make test-integration` (`pytest -m integration`)               |
| `contract`         | `pytest -m contract`                                            |
| `e2e_api`          | `pytest tests/e2e_api`                                          |
| `staging-smoke`    | manual job (`pytest tests/staging` against staging env)          |

`integration` + `contract` 依賴 PostgreSQL service container。

## 驗收條件

- [ ] 上面測試矩陣全綠（CI 紀錄）。
- [ ] Adapter contract tests 對 quote / telegram / corporate action / symbol / calendar 都存在。
- [ ] Outbox SKIP LOCKED 行為被 PostgreSQL integration test 驗證。
- [ ] Cross-user forbidden test 覆蓋所有 user-facing routes。
- [ ] OCO ambiguous trigger 被 integration test 證明會寫 admin alert。
- [ ] Quote unhealthy pause / resume 全鏈路測試通過。
- [ ] Test data factories（`tests/factories/`）避免 fixture 重複。
- [ ] Staging smoke script 對 staging 環境一次跑綠。

## 測試要求

- 本工單 deliverable 即測試本身；deliverable 的 acceptance 也是 test 跑綠。
- 引入 `pytest-asyncio`（若 worker 內有 async loop）、`pytest-postgresql` 或 testcontainers。
- 不可在 CI 打真 vendor / Telegram 網路。
- Test concurrency：對 PostgreSQL SKIP LOCKED 測試需要 multi-connection；使用 separate engines。

## 工程注意事項

- 不要每個 test 都做完整 auth flow；conftest 提供 `auth_client(role)` fixture，內部使用 BE-V1-01 helper。
- 對時間敏感測試（expire job / market session / quote freshness）統一注入 `Clock` 抽象，禁止 `datetime.now()` 散落。
- 整套 test 跑時間應在合理範圍（CI < 10 分鐘）。Integration 用 transactional rollback strategy 重置 DB（每 test transaction rollback）。
- Adapter contract test 使用 `responses` / `vcr.py` 或自寫 mock server；不要打真網路。
- Outbox SKIP LOCKED 測試需要 2 個 raw connection 模擬兩 worker；用 `asyncio.gather` 或 thread pool。
- E2E api test 包含啟動 in-process worker（短暫），verify 完整 outbox → in_app → telegram 鏈路。
- staging smoke 不能依賴 prod data；用獨立 staging user。
- 對 audit log 寫入失敗的 negative test：mock writer 故意 raise → business transaction 也 rollback。
- BE-V1-19 對前端的 contract fixture 是本工單 contract test 的衍生產物，可在同一個 fixture 倉統一。
