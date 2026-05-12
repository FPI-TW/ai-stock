# V0.5 / V1 後端工單

估時為後端 engineer-hours。不包含前端實作、PM/legal review、生產 vendor 合約、基礎設施採購。

## 1. 拆分原則

`v0.5` 是本地可跑的最小正式垂直切片，不是 demo-only 功能集合。它不部署到 EC2/RDS，只在本地裝置跑通：

```text
建立單筆到價提醒 -> quote 達標 -> intent 觸發 -> 產生站內通知 -> 列表可查看
```

V0.5 原則：

- 只做後續 V1 會沿用的 domain/API/DB 核心。
- 還沒用到的表先不建。
- Audit、delivery attempts、import reports、retention records 這類紀錄型功能先不做。
- 先假設不會遇到瞬間大流量，不先做 worker scaling、outbox、queue claim/lock。
- Development quote adapter 只用於本地測試與展示，不包裝成正式產品功能。

`v1` 是正式上線版，部署於 EC2 + RDS，補齊 auth、CSV、Telegram、admin、真實資料來源、營運監控、安全與資料保留。

估時：

- V0.5：244h。
- V1 additional：804h。
- 總計：1048h。

## 2. 類型與優先序

類型：

- AFK：可依本工單與規格直接實作，不需等待新產品決策。
- HITL：完成前需要人確認、憑證、vendor 細節或政策決策。

優先序：

- P0：該版本主流程必需。
- P1：該版本完整範圍必需。
- P2：正式上線前必需，但可在主要功能後交付。

## 3. V0.5 工單

## BE-V0.5-01：本地專案基礎、Config、Health、Quality Scripts

- 類型：AFK
- 優先序：P0
- 預估：16h
- 依賴：無

### 要做什麼

建立本地可啟動的 FastAPI 後端基礎，包含 uv dependencies、ruff 設定、application settings、request ID middleware、health endpoint，以及最小測試 / 格式化流程。

### 驗收條件

- [ ] App 可用 uv 在本機啟動。
- [ ] Ruff check 與 format commands 已文件化且通過。
- [ ] Health endpoint 回傳 service、version 與 PostgreSQL connectivity。
- [ ] 每個 request 都會產生或保留 `X-Request-Id`。
- [ ] Test harness 可執行 placeholder API test。

## BE-V0.5-02：最小 Database Baseline、Alembic、Core Intent/Notification Tables

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V0.5-01

### 要做什麼

建立 PostgreSQL integration、Alembic migrations，以及 v0.5 主流程需要的最小 tables：symbols、trade intents、trigger records、notifications。

### 驗收條件

- [ ] Alembic 可建立並 downgrade v0.5 schema。
- [ ] Schema 支援 symbol validation、trade intent create/list/cancel、trigger metadata、notification list/read。
- [ ] 不建立 v0.5 未使用的 audit/outbox/delivery/import tables。
- [ ] DB timestamps 使用 UTC，並保存 `trading_date`。
- [ ] Migration tests 可在 PostgreSQL 上執行。

## BE-V0.5-03：Local User Context 與 Owner Scope Placeholder

- 類型：AFK
- 優先序：P0
- 預估：8h
- 依賴：BE-V0.5-02

### 要做什麼

建立固定 single local user context。V0.5 不做 auth，但所有 user-facing API 仍從 context 取得 owner。

### 驗收條件

- [ ] Local mode 自動提供固定 `owner_user_id`。
- [ ] User-facing APIs 不接受 client payload 中的 owner id。
- [ ] Config 明確標示 local mode 不可公開部署。
- [ ] 後續 V1 auth context 可替換此 provider。

## BE-V0.5-04：最小 Symbol Seed、Validation、Lookup API

- 類型：AFK
- 優先序：P0
- 預估：16h
- 依賴：BE-V0.5-02

### 要做什麼

建立最小 symbol seed、symbol validation service，以及 lookup API。Seed 只需覆蓋本地 demo 與測試用台股現股 / ETF 範例。

### 驗收條件

- [ ] Seed data 包含台股現股與 ETF 範例。
- [ ] V0.5 intents 只接受 seed 中支援的 stock/ETF。
- [ ] API 支援用 symbol 查詢。
- [ ] Create intent 只接收 canonical symbol。
- [ ] Unknown 或 unsupported symbol 回傳正式 error envelope。

## BE-V0.5-05：Tick-Size 與 Decimal Price Domain Services

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V0.5-02

### 要做什麼

實作台股 tick-size validation、nearest legal price suggestions 與 Decimal price handling。

### 驗收條件

- [ ] Persisted price values 不使用 floating point。
- [ ] 不合法 target price 回傳 `INVALID_TICK_SIZE` 與最近合法價格。
- [ ] Unit tests 覆蓋 tick table boundaries。
- [ ] API 與 domain service 使用相同 tick validation。

## BE-V0.5-06：基本 Trading Session / Day-Intent Rules

- 類型：AFK
- 優先序：P0
- 預估：16h
- 依賴：BE-V0.5-02

### 要做什麼

實作 v0.5 所需的基本 trading session 與 `day` intent rules。V0.5 不做正式 market calendar importer 或 scheduled jobs。

### 驗收條件

- [ ] Service 可判斷一般盤 session。
- [ ] Create intent 可推導 `trading_date`。
- [ ] Evaluator 在 session 外不得觸發。
- [ ] V0.5 文件明確標示不做 activation/expiry scheduled jobs。

## BE-V0.5-07：單筆 Buy/Sell Price Alert Create/Cancel/List APIs

- 類型：AFK
- 優先序：P0
- 預估：32h
- 依賴：BE-V0.5-03, BE-V0.5-04, BE-V0.5-05, BE-V0.5-06

### 要做什麼

實作單筆 `buy_price_alert` / `sell_price_alert` 的 create、cancel、list、detail APIs。

### 驗收條件

- [ ] 可建立 `buy_price_alert` 與 `sell_price_alert`。
- [ ] `quantity_lots` 必填且為正整數。
- [ ] V0.5 固定 `time_in_force = day` 與 `execution_mode = notify_only`。
- [ ] 支援 active、scheduled、triggered、cancelled 查詢。
- [ ] Cancel 使用 status-guarded transaction update。
- [ ] 重複 active/scheduled intent 會被拒絕。

## BE-V0.5-08：Development Quote Adapter 與 Quote Validation

- 類型：AFK
- 優先序：P0
- 預估：20h
- 依賴：BE-V0.5-04, BE-V0.5-06

### 要做什麼

建立本地 development quote adapter，供 API 或測試推進 bid/ask/last/quote_time，並實作 quote validation。

### 驗收條件

- [ ] 本地 API 或測試可設定指定 symbol quote。
- [ ] Normalized quote 包含 symbol、bid、ask、last、quote_time、received_at。
- [ ] Validation 拒絕 out-of-session、crossed、non-positive、insufficient quotes。
- [ ] 缺 bid/ask 時可 fallback 到 last price，並記錄 metadata。
- [ ] Development adapter 不被描述為正式 quote product feature。

## BE-V0.5-09：Quote Evaluation、Trigger Transaction、Minimal Notification

- 類型：AFK
- 優先序：P0
- 預估：36h
- 依賴：BE-V0.5-07, BE-V0.5-08

### 要做什麼

實作 quote evaluation domain logic、trigger transaction、trigger metadata persistence，以及 minimal in-app notification creation。V0.5 不做 outbox。

### 驗收條件

- [ ] Evaluator 可依 development quote adapter 評估 active intents。
- [ ] `buy_price_alert` 使用 ask <= target，必要時 fallback last。
- [ ] `sell_price_alert` 使用 bid >= target，必要時 fallback last。
- [ ] Trigger transaction atomic 更新 intent、保存 trigger metadata、建立 notification。
- [ ] Unique constraint 或 status guard 防止 duplicate trigger。
- [ ] 盤中 create 時若 current quote 已符合條件，可立即觸發。

## BE-V0.5-10：Notification List/Read APIs

- 類型：AFK
- 優先序：P1
- 預估：16h
- 依賴：BE-V0.5-09

### 要做什麼

實作最小 notification list/read APIs，讓前端可展示 trigger 後產生的站內通知。

### 驗收條件

- [ ] Notification list 只回傳 local user context 的通知。
- [ ] Notification 包含 rendered title/body、created_at、read_at。
- [ ] User 可將 notification 標記為 read。
- [ ] Read status 不影響 trade intent status。

## BE-V0.5-11：本地主流程 API Examples 與 Frontend Handoff

- 類型：AFK
- 優先序：P1
- 預估：12h
- 依賴：BE-V0.5-07, BE-V0.5-10

### 要做什麼

發布本地主流程 API examples，供前端 repo 串接 v0.5。

### 驗收條件

- [ ] Examples 覆蓋 create、quote update、evaluate、cancel、list、notification list/read。
- [ ] Error envelope examples 包含 v0.5 核心 error codes。
- [ ] 文件清楚標示 v0.5 無 auth、不部署、不公開。
- [ ] 前端可用 examples 跑通本地主流程。

## BE-V0.5-12：Integration Tests：Create -> Quote -> Trigger -> Notification

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V0.5-07, BE-V0.5-09

### 要做什麼

建立 v0.5 主流程 integration tests，固定本地展示路徑。

### 驗收條件

- [ ] Tests 覆蓋 create -> quote update -> evaluate -> trigger -> notification。
- [ ] Tests 覆蓋 immediate trigger。
- [ ] Tests 覆蓋 cancel active intent。
- [ ] Tests 覆蓋 invalid tick 與 unknown symbol。
- [ ] Tests 可在 PostgreSQL 測試環境穩定執行。

## 4. V1 Additional 工單

## BE-V1-01：正式 Auth、Sessions、CSRF、Invitation、Password Reset

- 類型：AFK
- 優先序：P0
- 預估：40h
- 依賴：BE-V0.5

### 要做什麼

以正式 auth 取代 local user context，實作 admin-created user invitations、account activation、login、refresh token rotation、logout、password reset、CSRF protection、origin checks、rate limits 與 session revocation。

### 驗收條件

- [ ] Admin 可建立 invited user 並發出 24 小時有效 invitation token。
- [ ] User 可啟用帳號並設定密碼。
- [ ] Login 回傳 short-lived access token 與 refresh cookie。
- [ ] Refresh token rotation 只保存 token hash。
- [ ] 偵測 refresh token reuse 時，撤銷該 user 所有 sessions 並寫 audit。
- [ ] Password reset 不透露 email 是否存在。
- [ ] State-changing requests 需要 CSRF protection。

## BE-V1-02：正式 Role 與 Owner-Scope Authorization

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V1-01

### 要做什麼

將 v0.5 local user context 替換為正式 auth context，建立集中式 `user` / `admin` authorization helpers 與 cross-user forbidden tests。

### 驗收條件

- [ ] User-facing APIs 不接受 client payload 中的 `owner_user_id`。
- [ ] Users 不能讀取、建立、取消或修改其他 user 的資源。
- [ ] Admin-only endpoints 拒絕 non-admin users。
- [ ] Authorization checks 集中實作。
- [ ] Cross-user access tests 覆蓋核心 resources。

## BE-V1-03：Production Symbol Importer 與 Admin Override Readiness

- 類型：HITL
- 優先序：P0
- 預估：32h
- 依賴：BE-V0.5-04

### 要做什麼

在 v0.5 symbol seed 之外，補上 production symbol provider adapter、importer job、import report 與 admin override readiness。

### 驗收條件

- [ ] Provider adapter 可匯入 TWSE/TPEx stock/ETF symbol master。
- [ ] Importer 可 normalize 並 upsert 內部 symbol master。
- [ ] Import report 保存新增、更新、失敗統計。
- [ ] Admin override 欄位與 audit metadata 可支援後續 admin API。

## BE-V1-04：Production Market Calendar Importer、Scheduled Activation/Expiry

- 類型：HITL
- 優先序：P0
- 預估：44h
- 依賴：BE-V0.5-06

### 要做什麼

補上 production market calendar importer、半日交易、臨時休市、scheduled activation、expiry job 與 override readiness。

### 驗收條件

- [ ] Importer 可匯入交易日與休市日。
- [ ] Calendar model 支援半日交易與特殊 session。
- [ ] Scheduled intents 可於 regular session open 轉 active。
- [ ] Day intents 可於 regular session close 後過期。
- [ ] Jobs 使用 advisory lock 或 unique execution key。

## BE-V1-05：Licensed Quote Provider Adapter、Quote Health、Pause/Resume

- 類型：HITL
- 優先序：P0
- 預估：56h
- 依賴：BE-V0.5-08, BE-V0.5-09

### 要做什麼

接上正式授權 quote provider adapter，並補齊 production quote health tracking、quote unhealthy、pause/resume。

### 驗收條件

- [ ] Adapter 回傳 bid、ask、last、quote time、source、latency label、raw reference/hash、received time。
- [ ] Provider failure、stale quote、invalid quote 會被記錄。
- [ ] Symbol-level invalid count / invalid duration 可追蹤。
- [ ] Quote unhealthy 會暫停受影響 active intents。
- [ ] 恢復後從最新有效 quote 繼續，不回放 missed quotes。

## BE-V1-06：Outbox、Notification Delivery Attempts、Worker Retry

- 類型：AFK
- 優先序：P0
- 預估：40h
- 依賴：BE-V0.5-09, BE-V0.5-10

### 要做什麼

將 v0.5 trigger -> notification transaction 升級為 transactional outbox 與 notification delivery worker。

### 驗收條件

- [ ] Trigger transaction 寫入 outbox event。
- [ ] Outbox row 支援 status、available_at、locked_by、locked_until、attempt_count。
- [ ] Notification worker 可 claim、retry、mark success/failure。
- [ ] Handler idempotent。
- [ ] Delivery attempts 保存 correlation id 與 error metadata。

## BE-V1-07：Take-Profit / Stop-Loss Strategy Semantics

- 類型：AFK
- 優先序：P1
- 預估：32h
- 依賴：BE-V0.5-07, BE-V0.5-09

### 要做什麼

新增多單與使用者聲明空單持倉的 take-profit / stop-loss alert 建立與評估。

### 驗收條件

- [ ] Take-profit 與 stop-loss 必填 `position_side`。
- [ ] Order side 由系統推導，不接受 client 指定。
- [ ] Long/short trigger direction 符合 domain spec。
- [ ] V1 支援 short exit alerts，不支援 short entry。
- [ ] Immediate-trigger behavior 適用於這些策略。

## BE-V1-08：OCO Bracket Alert Group Behavior

- 類型：AFK
- 優先序：P1
- 預估：40h
- 依賴：BE-V1-07

### 要做什麼

實作 `TradeIntentGroup` bracket alerts、two-child creation、OCO validation、sibling cancellation、group cancellation 與 ambiguous trigger handling。

### 驗收條件

- [ ] Long OCO 要求 take-profit price 大於 stop-loss price。
- [ ] Short OCO 要求 take-profit price 小於 stop-loss price。
- [ ] 任一 leg 觸發時，同一 transaction 取消 sibling。
- [ ] 使用者取消任一 leg 時，取消整組。
- [ ] 同時兩腳成立時，group 與 children 標記 `ambiguous_trigger`。
- [ ] Ambiguous trigger 發 system notification，不發一般 price notification。

## BE-V1-09：Corporate Action Importer、Cash Dividend Snapshot、Effective Price Preview

- 類型：HITL
- 優先序：P1
- 預估：56h
- 依賴：BE-V1-03, BE-V1-04

### 要做什麼

實作 corporate action provider adapter、importer、cash dividend snapshot、effective target price calculation、preview API、unsupported action handling。

### 驗收條件

- [ ] V1 只套用 cash dividend fixed-amount adjustments。
- [ ] 不支援但會影響價格基準的 actions 以 unsupported 保存，且不做部分調整。
- [ ] Snapshot 於開盤前產生且 versioned。
- [ ] Strategy engine 只讀 internal snapshot data。
- [ ] Preview 回傳 original target、adjustment amount、effective target、tick rounding、explanation。

## BE-V1-10：CSV Preview、Draft、Confirm 與 Batch Metadata

- 類型：AFK
- 優先序：P1
- 預估：48h
- 依賴：BE-V1-07, BE-V1-08, BE-V1-09

### 要做什麼

實作 price alerts 與 position alerts 的 backend CSV preview/confirm APIs、batch drafts、all-or-nothing creation、row errors 與 batch history。

### 驗收條件

- [ ] 支援兩種 template，且不可混用。
- [ ] Preview 保存 `csv_batch_draft` 15 分鐘。
- [ ] Preview 回傳 normalized rows、effective target prices、warnings、row errors。
- [ ] Confirm 逐列重新驗證，並拒絕 expired 或 stale drafts。
- [ ] 任一 row error 會阻止整批建立。
- [ ] Batch size limit 100 有被強制執行。

## BE-V1-11：Telegram Bind/Unbind 與 Telegram Delivery Worker

- 類型：HITL
- 優先序：P1
- 預估：40h
- 依賴：BE-V1-01, BE-V1-06

### 要做什麼

實作 Telegram binding code flow、bot command handling for `/start` and `/bind <code>`、unbind、delivery adapter、retry rules、permanent failure handling 與 audit events。

### 驗收條件

- [ ] Bind code 一次性使用、10 分鐘有效、每 user 同時間只有一個 active code。
- [ ] Code attempts 有 rate limit。
- [ ] Telegram chat id 在 users 間唯一。
- [ ] 不使用 username 作為身份。
- [ ] Retryable Telegram errors 最多 retry 3 次並使用 backoff。
- [ ] Permanent Telegram errors 標記 binding failed/revoked，並保留 in-app delivery。

## BE-V1-12：User Notification Settings 與 Notification Center Hardening

- 類型：AFK
- 優先序：P1
- 預估：24h
- 依賴：BE-V1-06, BE-V1-11

### 要做什麼

在 v0.5 notification center 上補齊 user-level notification settings、Telegram channel decision、delivery skipped reason 與 owner-scoped read/unread behavior。

### 驗收條件

- [ ] In-app channel 永遠啟用。
- [ ] Telegram 只能在 binding active 時啟用。
- [ ] 觸發當下的 active settings 決定 delivery channels。
- [ ] Telegram 因 disabled、unbound、failed binding 被 skipped 時有紀錄。
- [ ] Read status 不影響 intent status。

## BE-V1-13：Admin User Management 與 Account Disable Command

- 類型：AFK
- 優先序：P1
- 預估：40h
- 依賴：BE-V1-01, BE-V1-02, BE-V0.5-07

### 要做什麼

實作 admin user creation、invitation resend、disablement、admin TOTP 2FA enforcement 與 account disable domain command。

### 驗收條件

- [ ] Admin 可建立 user 並重寄 invitation。
- [ ] 重寄 invitation 會使舊 link 失效並寫 audit。
- [ ] Admin features 需要 TOTP setup。
- [ ] Disable account 會將 active/scheduled intents 轉 `cancelled_by_account_disabled`。
- [ ] Pending notification deliveries 會標記 skipped。
- [ ] Re-enable account 不恢復舊 intents。

## BE-V1-14：Admin Data Overrides：Symbols、Calendar、Corporate Actions

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-03, BE-V1-04, BE-V1-09

### 要做什麼

實作 symbol master、market calendar、corporate actions、disputed snapshots 的 admin override APIs，並要求 reason 與 audit trail。

### 驗收條件

- [ ] Override operations 需要 admin role 與 reason。
- [ ] Overrides 寫 audit，包含 before/after metadata。
- [ ] Corporate action snapshot 可標記 disputed。
- [ ] Disputed snapshot 會暫停受影響 active intents。
- [ ] Market calendar override 可表示 temporary closure 與 half day。

## BE-V1-15：Admin Monitoring、Alerts、Backlog Metrics、Kill Switches

- 類型：AFK
- 優先序：P2
- 預估：48h
- 依賴：BE-V1-05, BE-V1-06

### 要做什麼

實作 admin monitoring APIs、alert records、worker backlog metrics、quote/data health summaries、Telegram failure summaries 與分層 kill switches。

### 驗收條件

- [ ] Admin 可查看 aggregate intent、failure、symbol、quote、notification health。
- [ ] Alerts 覆蓋 quote source stale/failure、symbol quote validation failures、Telegram failure rate、import failure、missing snapshot、worker backlog。
- [ ] Kill switch 可停止 all triggers、symbol triggers、Telegram sending、all external notifications、corporate action adjustments。
- [ ] Trigger kill switch active 時，quote fetching 仍可繼續。
- [ ] Kill switch changes 寫 audit。

## BE-V1-16：Audit Log、Rate Limits、Retention、Privacy Anonymization

- 類型：AFK
- 優先序：P2
- 預估：48h
- 依賴：BE-V1-01, BE-V1-06, BE-V1-13

### 要做什麼

完成 audit log、rate limits、retention jobs、anonymization support、technical log retention hooks、terms acceptance records。

### 驗收條件

- [ ] 核心 user/admin/system events 寫 audit。
- [ ] Login、password reset、invite resend、bind code、Telegram bind attempts 都有 rate limit。
- [ ] Retention windows 覆蓋 intents、notifications、CSV normalized rows、audit events、technical logs。
- [ ] Account anonymization 可移除 email 與 Telegram chat id，同時保留 product history。
- [ ] Terms/risk-disclosure acceptance version 有被保存。

## BE-V1-17：EC2/RDS Deployment Readiness、Backup/Restore、Production Hardening

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-01, BE-V1-05, BE-V1-15

### 要做什麼

補齊 EC2 + RDS 部署準備、環境設定、backup/restore checklist、production hardening。

### 驗收條件

- [ ] Production config 支援 EC2 + RDS。
- [ ] Secrets 不寫入 repo。
- [ ] DB migration 流程可在 RDS 執行。
- [ ] Backup/restore rehearsal checklist 已文件化。
- [ ] Health check 可支援部署監控。

## BE-V1-18：Production Contract/Integration/Adapter Test Hardening

- 類型：AFK
- 優先序：P0
- 預估：48h
- 依賴：BE-V1-05, BE-V1-11, BE-V1-16

### 要做什麼

在 v0.5 tests 上補齊 production domain、command、adapter contract 與 authorization tests。

### 驗收條件

- [ ] Domain tests 覆蓋 tick size、dividend adjustment、round-away-from-trigger、strategy direction、OCO、status transitions。
- [ ] Integration tests 覆蓋 CSV all-or-nothing、account disable、disputed snapshot、market calendar override。
- [ ] Adapter contract tests 覆蓋 quote、Telegram、corporate action、symbol providers。
- [ ] Cross-user forbidden tests 覆蓋 user-owned resources。
- [ ] Development adapters 仍可產生 deterministic frontend contract fixtures。

## BE-V1-19：Production OpenAPI Examples 與 Frontend Contract Fixtures

- 類型：AFK
- 優先序：P1
- 預估：24h
- 依賴：BE-V1-10, BE-V1-12

### 要做什麼

將 v0.5 examples 擴充成正式 OpenAPI schema、request/response examples、error codes、warnings、pagination、idempotency、CSV row errors 與 notification payload examples。

### 驗收條件

- [ ] OpenAPI schema 可產出，並 check in 或透過穩定 endpoint 提供。
- [ ] Examples 覆蓋 single create、OCO create、CSV preview/confirm、cancel、list、notification center、Telegram binding、admin health。
- [ ] Error envelope examples 包含核心 V1 error codes。
- [ ] 前端可建立 forms，而不需重作權威 tick/corporate-action logic。
- [ ] Contract docs 明確說明前端負責解析 CSV file，但後端擁有 validation 與 authoritative preview。

