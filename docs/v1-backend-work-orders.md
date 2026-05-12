# V1 後端工單

估時為後端 engineer-hours。不包含前端實作、PM/legal review、生產 vendor 合約、基礎設施採購。

優先序定義：

- P0：可用且安全的 V1 後端核心路徑必需。
- P1：核心路徑穩定後，完整 V1 範圍必需。
- P2：正式上線前必需，但可在主要功能路徑後交付。

類型定義：

- AFK：可依本工單與規格直接實作，不需等待新產品決策。
- HITL：完成前需要人確認、憑證、vendor 細節或政策決策。

## BE-V1-01：專案基礎、Config、Health、CI Quality Scripts

- 類型：AFK
- 優先序：P0
- 預估：16h
- 依賴：無

### 要做什麼

建立 FastAPI 後端基礎，包含 uv dependencies、ruff 設定、application settings、request ID middleware、health endpoints，以及最小測試 / 格式化流程。

### 驗收條件

- [ ] App 可用 uv 在本機啟動。
- [ ] Ruff check 與 format commands 已文件化且通過。
- [ ] Health endpoint 回傳 service、version 與 dependency placeholders。
- [ ] 每個 request 都會產生或保留 `X-Request-Id`。
- [ ] Test harness 可執行 placeholder API test。

## BE-V1-02：Database Baseline、Alembic、Enums、Audit 與 Outbox Primitives

- 類型：AFK
- 優先序：P0
- 預估：32h
- 依賴：BE-V1-01

### 要做什麼

建立 PostgreSQL integration、Alembic migrations、核心 enum tables/types、audit event table、outbox event table，以及共用 timestamp/request-id conventions。

### 驗收條件

- [ ] Alembic 可建立並 downgrade 初始 schema。
- [ ] Audit events 支援 actor type/id、event type、metadata、occurred time、request/correlation id。
- [ ] Outbox rows 支援 status、available time、lock owner、lock expiry、attempt count、correlation id。
- [ ] DB timestamps 使用 UTC。
- [ ] Migration tests 可在 PostgreSQL 上執行。

## BE-V1-03：Auth、Sessions、CSRF、Invitations、Password Reset

- 類型：AFK
- 優先序：P0
- 預估：40h
- 依賴：BE-V1-02

### 要做什麼

實作 admin-created user invitations、account activation、login、refresh token rotation、logout、password reset、CSRF protection、origin checks、rate limits 與 session revocation。

### 驗收條件

- [ ] Admin 可建立 invited user 並發出 24 小時有效 invitation token。
- [ ] User 可啟用帳號並設定密碼。
- [ ] Login 回傳 short-lived access token 與 refresh cookie。
- [ ] Refresh token rotation 只保存 token hash。
- [ ] 偵測 refresh token reuse 時，撤銷該 user 所有 sessions 並寫 audit。
- [ ] Password reset 不透露 email 是否存在。
- [ ] State-changing requests 需要 CSRF protection。
- [ ] Login 與 reset rate limits 有測試覆蓋。

## BE-V1-04：Role 與 Owner-Scope Authorization

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V1-03

### 要做什麼

建立 `user` / `admin` 的集中式 authorization helpers，從 auth context 強制 owner scope，並補上 cross-user forbidden tests。

### 驗收條件

- [ ] User-facing APIs 不接受 client payload 中的 `owner_user_id`。
- [ ] Users 不能讀取、建立、取消或修改其他 user 的資源。
- [ ] Admin-only endpoints 拒絕 non-admin users。
- [ ] Authorization checks 集中實作，方便未來擴充角色。
- [ ] Cross-user access tests 覆蓋核心 resource types。

## BE-V1-05：Symbol Master Import、Validation、Autocomplete API

- 類型：HITL
- 優先序：P0
- 預估：32h
- 依賴：BE-V1-02

### 要做什麼

建立內部 symbol master tables、provider adapter boundary、importer job、admin override fields、validation service，以及 user-facing symbol autocomplete/lookup APIs。

### 驗收條件

- [ ] Symbol master 保存 symbol、display name、market、instrument type、tradable status、update time。
- [ ] V1 intents 只接受台股現股與 ETF。
- [ ] CSV validation 只接受標準台股代號。
- [ ] UI API 支援用 symbol 或 display name 搜尋，並回傳 canonical symbol。
- [ ] Admin override changes 寫 audit。
- [ ] 在 production source 細節未定前，fake provider 可支援 deterministic tests。

## BE-V1-06：Market Calendar Service 與 Day-Intent Trading Date Rules

- 類型：AFK
- 優先序：P0
- 預估：32h
- 依賴：BE-V1-02

### 要做什麼

實作 market calendar tables、service methods、regular session logic、next trading day calculation、expiry time calculation、半日交易支援與 admin override readiness。

### 驗收條件

- [ ] Service 支援 trading day、next trading day、regular session、within-session、day-intent trading date、expiry。
- [ ] 盤前建立會對應到當日 trading date，狀態為 scheduled。
- [ ] 收盤後與假日建立會對應到下一個 trading date。
- [ ] Quote evaluator 可詢問 service 判斷是否允許評估。
- [ ] 半日交易與臨時休市情境可測試。

## BE-V1-07：Tick-Size 與 Decimal Price Domain Services

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V1-02

### 要做什麼

實作台股 tick-size validation、nearest legal price suggestions、Decimal price handling 與 round-away-from-trigger utilities。

### 驗收條件

- [ ] Persisted price values 不使用 floating point。
- [ ] 不合法 target price 回傳 `INVALID_TICK_SIZE` 與最接近合法價格。
- [ ] 除息調整後價格可 round away from trigger。
- [ ] Unit tests 覆蓋 tick table boundaries。

## BE-V1-08：單筆 Price Alert Create、Cancel、List APIs

- 類型：AFK
- 優先序：P0
- 預估：40h
- 依賴：BE-V1-03, BE-V1-05, BE-V1-06, BE-V1-07

### 要做什麼

實作 user APIs 與 commands，用於建立、取消、列表與查看 buy/sell price alerts；包含 idempotency、limits、duplicate detection 與 cursor pagination。

### 驗收條件

- [ ] 可建立 `buy_price_alert` 與 `sell_price_alert`。
- [ ] `quantity_lots` 必填且為正整數。
- [ ] V1 只接受 `time_in_force = day` 與 `execution_mode = notify_only`。
- [ ] Active/scheduled limits 由 admin-configurable values 控制。
- [ ] 重複建立 active/scheduled intent 回傳 `DUPLICATE_INTENT`。
- [ ] Cancel 使用 idempotency 與 status-guarded transaction updates。
- [ ] Lists 使用 cursor pagination，並符合 active/scheduled/history 語意。

## BE-V1-09：Quote Provider Adapter 與 Quote Validation

- 類型：HITL
- 優先序：P0
- 預估：32h
- 依賴：BE-V1-05

### 要做什麼

定義 quote provider adapter、normalized quote snapshot、quote validation service、fake/test provider 與 quote health tracking primitives。

### 驗收條件

- [ ] Adapter 回傳 symbol、bid、ask、last、quote time、source、latency label、raw reference/hash、received time。
- [ ] Validation 拒絕 stale、out-of-session、crossed、non-positive、insufficient quotes。
- [ ] 缺 bid/ask 時可 fallback 到 last price，且 metadata 明確標記。
- [ ] 單次 fetch failure 不觸發 intents。
- [ ] Fake provider 可驅動 deterministic trigger tests。

## BE-V1-10：Quote Evaluator Trigger Transaction 與 Immediate-Trigger Path

- 類型：AFK
- 優先序：P0
- 預估：48h
- 依賴：BE-V1-08, BE-V1-09

### 要做什麼

建立 quote evaluator worker loop、active symbol batching、strategy condition evaluation、create 時的 immediate-trigger behavior、trigger event persistence 與 outbox write transaction。

### 驗收條件

- [ ] Evaluator 每 1-5 秒抓取 active symbols。
- [ ] Evaluation 在 regular session 外永遠不觸發。
- [ ] Trigger transaction 會 atomic 更新 intent、寫 trigger event、寫 outbox。
- [ ] `TriggerEvent(trade_intent_id)` uniqueness 可防止 duplicate triggers。
- [ ] Regular session 內 create 時會抓 current quote，若已符合條件則立即觸發。
- [ ] Create 時 quote unavailable 會回傳 warning 並讓 intent 保持 active。
- [ ] Trigger/cancel race 透過 conditional status updates 保護。

## BE-V1-11：Notification Model、Outbox Worker、In-App Delivery

- 類型：AFK
- 優先序：P0
- 預估：40h
- 依賴：BE-V1-10

### 要做什麼

實作程式碼集中管理的 notification templates、notification 與 delivery models、outbox claiming、in-app delivery、retry state handling、read/unread primitives 與 rendered message snapshots。

### 驗收條件

- [ ] Notification 保存 template key/version、message data、rendered title/body、rendered time。
- [ ] In-app delivery 必開，使用者不可關閉。
- [ ] Notification failure 不會讓 triggered intents 回到 active。
- [ ] Outbox handler 是 idempotent。
- [ ] Delivery uniqueness 盡可能避免同 channel 重複派送。
- [ ] 訊息包含 notify-only、未下單、不保證成交等文案。

## BE-V1-12：Take-Profit 與 Stop-Loss Strategy Semantics

- 類型：AFK
- 優先序：P1
- 預估：32h
- 依賴：BE-V1-08, BE-V1-10

### 要做什麼

新增多單與使用者聲明空單持倉的 take-profit / stop-loss alert 建立與評估。

### 驗收條件

- [ ] Take-profit 與 stop-loss 必填 `position_side`。
- [ ] Order side 由系統推導，不接受 client 指定。
- [ ] Long/short trigger direction 符合 domain spec。
- [ ] V1 支援 short exit alerts，不支援 short entry。
- [ ] Immediate-trigger behavior 適用於這些策略。

## BE-V1-13：OCO Bracket Alert Group Behavior

- 類型：AFK
- 優先序：P1
- 預估：40h
- 依賴：BE-V1-12

### 要做什麼

實作 `TradeIntentGroup` bracket alerts、two-child creation、OCO validation、sibling cancellation、group cancellation 與 ambiguous trigger handling。

### 驗收條件

- [ ] Long OCO 要求 take-profit price 大於 stop-loss price。
- [ ] Short OCO 要求 take-profit price 小於 stop-loss price。
- [ ] 任一 leg 觸發時，同一 transaction 取消 sibling。
- [ ] 使用者取消任一 leg 時，取消整組。
- [ ] 同時兩腳成立時，group 與 children 標記 `ambiguous_trigger`。
- [ ] Ambiguous trigger 發 system notification，不發一般 price notification。

## BE-V1-14：Corporate Action Import、Cash Dividend Snapshot、Effective Price Preview

- 類型：HITL
- 優先序：P1
- 預估：56h
- 依賴：BE-V1-05, BE-V1-06, BE-V1-07

### 要做什麼

實作 corporate action provider adapter、importer、internal corporate action tables、admin override/audit、trading-day adjustment snapshot、effective target price calculation 與 preview API。

### 驗收條件

- [ ] V1 只套用 cash dividend fixed-amount adjustments。
- [ ] 不支援但會影響價格基準的 actions 以 unsupported 保存，且不做部分調整。
- [ ] Snapshot 於開盤前產生且 versioned。
- [ ] Strategy engine 只讀 internal snapshot data。
- [ ] Preview 回傳 original target、adjustment amount、effective target、tick rounding、explanation。
- [ ] 當使用者必須重新確認變更後調整時，create 回傳 `STALE_PRICE_CONTEXT`。

## BE-V1-15：Invalid-for-Day、Expiry、Activation、Pause/Resume Scheduled Jobs

- 類型：AFK
- 優先序：P1
- 預估：48h
- 依賴：BE-V1-06, BE-V1-10, BE-V1-14

### 要做什麼

實作 scheduled activation、expiry、invalid-for-day、quote unhealthy pause/resume、market-status pause/resume、temporary closure rescheduling、admin alert generation 與 idempotent job execution records。

### 驗收條件

- [ ] Scheduled intents 於 regular session open 轉 active。
- [ ] Day intents 於 regular session close 後過期。
- [ ] 即使 expiry job 延遲，evaluator 仍阻擋 session 外觸發。
- [ ] Daily limit 或 market rule invalidity 會讓 intent/group 轉 `invalid_for_day` 並通知 user。
- [ ] Quote unhealthy 會暫停受影響 active intents 並通知 users。
- [ ] Recovery 從最新有效 quote 繼續，不回放 missed quotes。
- [ ] Resumed-and-triggered path 只送一則帶 resumed context 的 price notification。
- [ ] Jobs 使用 advisory lock 或 unique execution key。

## BE-V1-16：CSV Preview、Draft、Confirm、Batch Metadata

- 類型：AFK
- 優先序：P1
- 預估：48h
- 依賴：BE-V1-08, BE-V1-12, BE-V1-13, BE-V1-14

### 要做什麼

實作 price alerts 與 position alerts 的 backend CSV preview/confirm APIs、batch drafts、all-or-nothing creation、row errors 與 batch history。

### 驗收條件

- [ ] 支援兩種 template，且不可混用。
- [ ] Preview 保存 `csv_batch_draft` 15 分鐘。
- [ ] Preview 回傳 normalized rows、effective target prices、warnings、row errors。
- [ ] Confirm 逐列重新驗證，並拒絕 expired 或 stale drafts。
- [ ] 任一 row error 會阻止整批建立。
- [ ] Batch size limit 100 有被強制執行。
- [ ] Metadata 保存 source row number、file name、raw row hash、batch import id。

## BE-V1-17：Telegram Bind/Unbind 與 Telegram Delivery Worker

- 類型：HITL
- 優先序：P1
- 預估：40h
- 依賴：BE-V1-03, BE-V1-11

### 要做什麼

實作 Telegram binding code flow、bot command handling for `/start` and `/bind <code>`、unbind、delivery adapter、retry rules、permanent failure handling 與 audit events。

### 驗收條件

- [ ] Bind code 一次性使用、10 分鐘有效、每 user 同時間只有一個 active code。
- [ ] Code attempts 有 rate limit。
- [ ] Telegram chat id 在 users 間唯一。
- [ ] 不使用 username 作為身份。
- [ ] 不支援的 Telegram commands/text/files 回覆請到 Web UI 操作。
- [ ] Retryable Telegram errors 最多 retry 3 次並使用 backoff。
- [ ] Permanent Telegram errors 標記 binding failed/revoked，並保留 in-app delivery。

## BE-V1-18：User Notification Settings 與 Notification Center APIs

- 類型：AFK
- 優先序：P1
- 預估：24h
- 依賴：BE-V1-11, BE-V1-17

### 要做什麼

實作 user-level notification settings、notification center list/read APIs、unread count 與 delivery-decision recording。

### 驗收條件

- [ ] In-app channel 永遠啟用。
- [ ] Telegram 只能在 binding active 時啟用。
- [ ] 觸發當下的 active settings 決定 delivery channels。
- [ ] Telegram 因 disabled、unbound、failed binding 被 skipped 時有紀錄。
- [ ] Users 可列出 notifications 並標記 read。
- [ ] Read status 不影響 intent status。

## BE-V1-19：Admin User Management 與 Account Disable Command

- 類型：AFK
- 優先序：P1
- 預估：40h
- 依賴：BE-V1-03, BE-V1-04, BE-V1-08

### 要做什麼

實作 admin user creation、invitation resend、disablement、admin TOTP 2FA enforcement 與 account disable domain command。

### 驗收條件

- [ ] Admin 可建立 user 並重寄 invitation。
- [ ] 重寄 invitation 會使舊 link 失效並寫 audit。
- [ ] Admin features 需要 TOTP setup。
- [ ] Disable account 會將 active/scheduled intents 轉 `cancelled_by_account_disabled`。
- [ ] Pending notification deliveries 會標記 skipped。
- [ ] Telegram binding 會被停用。
- [ ] Re-enable account 不恢復舊 intents。

## BE-V1-20：Admin Data Overrides：Symbols、Calendar、Corporate Actions

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-05, BE-V1-06, BE-V1-14

### 要做什麼

實作 symbol master、market calendar、corporate actions、disputed snapshots 的 admin override APIs，並要求 reason 與 audit trail。

### 驗收條件

- [ ] Override operations 需要 admin role 與 reason。
- [ ] Overrides 寫 audit，包含 before/after metadata。
- [ ] Corporate action snapshot 可標記 disputed。
- [ ] Disputed snapshot 會暫停受影響 active intents。
- [ ] Market calendar override 可表示 temporary closure 與 half day。

## BE-V1-21：Admin Monitoring、Alerts、Backlog Metrics、Kill Switches

- 類型：AFK
- 優先序：P2
- 預估：48h
- 依賴：BE-V1-10, BE-V1-11, BE-V1-15

### 要做什麼

實作 admin monitoring APIs、alert records、worker backlog metrics、quote/data health summaries、Telegram failure summaries 與分層 kill switches。

### 驗收條件

- [ ] Admin 可查看 aggregate intent、failure、symbol、quote、notification health。
- [ ] Alerts 覆蓋 quote source stale/failure、symbol quote validation failures、Telegram failure rate、import failure、missing snapshot、worker backlog。
- [ ] Kill switch 可停止 all triggers、symbol triggers、Telegram sending、all external notifications、corporate action adjustments。
- [ ] Trigger kill switch active 時，quote fetching 仍可繼續。
- [ ] Kill switch changes 寫 audit。

## BE-V1-22：Rate Limits、Retention、Privacy Anonymization、Release Hardening

- 類型：AFK
- 優先序：P2
- 預估：40h
- 依賴：BE-V1-03, BE-V1-11, BE-V1-19

### 要做什麼

完成 rate limits、retention jobs、anonymization support、technical log retention hooks、terms acceptance records 與 release readiness checks。

### 驗收條件

- [ ] Login、password reset、invite resend、bind code、Telegram bind attempts 都有 rate limit。
- [ ] Retention windows 覆蓋 intents、notifications、CSV normalized rows、audit events、technical logs。
- [ ] Account anonymization 可移除 email 與 Telegram chat id，同時保留 product history。
- [ ] Terms/risk-disclosure acceptance version 有被保存。
- [ ] Backup/restore rehearsal checklist 已文件化。

## BE-V1-23：Contract 與 Integration Test Suite，含 Fake Adapters

- 類型：AFK
- 優先序：P0
- 預估：56h
- 依賴：BE-V1-08, BE-V1-10, BE-V1-11

### 要做什麼

建立完整後端測試，覆蓋 domain services、command handlers、DB transactions、adapters 與 API contracts。

### 驗收條件

- [ ] Domain tests 覆蓋 tick size、dividend adjustment、round-away-from-trigger、strategy direction、OCO、status transitions。
- [ ] Integration tests 覆蓋 create intent、CSV all-or-nothing、quote trigger transaction、notification outbox、account disable、disputed snapshot、market calendar override。
- [ ] Adapter contract tests 覆蓋 quote、Telegram、corporate action、symbol providers。
- [ ] Fake adapters 可產生 deterministic frontend contract fixtures。
- [ ] Cross-user forbidden tests 覆蓋 user-owned resources。

## BE-V1-24：API Contract Examples 與 Frontend Handoff Package

- 類型：AFK
- 優先序：P1
- 預估：24h
- 依賴：BE-V1-08, BE-V1-16, BE-V1-18

### 要做什麼

發布後端擁有的 API contract documentation 與 fixtures，供獨立前端 repo 使用；內容包含 request/response examples、error codes、warnings、pagination、idempotency、CSV row errors 與 notification payload examples。

### 驗收條件

- [ ] OpenAPI schema 可產出，並 check in 或透過穩定 endpoint 提供。
- [ ] Examples 覆蓋 single create、OCO create、CSV preview/confirm、cancel、list、notification center、Telegram binding、admin health。
- [ ] Error envelope examples 包含核心 V1 error codes。
- [ ] 前端可建立 forms，而不需重作權威 tick/corporate-action logic。
- [ ] Contract docs 明確說明前端負責解析 CSV file，但後端擁有 validation 與 authoritative preview。
