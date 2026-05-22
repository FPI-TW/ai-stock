# V0.5 / V1 後端工單總清單

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
- Demo quote 來源以 Shioaji demo（BE-V0.5-13）為唯一 runtime；V1 切換到 licensed provider 時整檔刪除 demo 模組。

`v1` 是正式上線版，部署於 EC2 + RDS，補齊 auth、CSV、Telegram、admin、真實資料來源、營運監控、安全與資料保留。

## 2. 估時總覽

| 版本          | 工單數 |  小計 |
| ------------- | -----: | ----: |
| V0.5          |     12 | 227.5h |
| V1 additional |     19 | 582.5h |
| Total         |     31 | 810h |

V0.5 工單數：BE-V0.5-01 至 BE-V0.5-13 共 13 編號，其中 BE-V0.5-08 已由 BE-V0.5-13 取代，實際交付 12 張。

## 3. 類型與優先序

類型：

- AFK：可依本工單與規格直接實作，不需等待新產品決策。
- HITL：完成前需要人確認、憑證、vendor 細節或政策決策。

優先序：

- P0：該版本主流程必需。
- P1：該版本完整範圍必需。
- P2：正式上線前必需，但可在主要功能後交付。

## 4. V0.5 工單索引

詳細工單位於 [docs/orders/v0.5](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5)。

| ID         | 工單                                                             | 優先序 | 預估 | 狀態 | 詳細文件                                                                                                                                      |
| ---------- | ---------------------------------------------------------------- | ------ | ---: | ---- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| BE-V0.5-01 | 本地專案基礎、config、health、quality scripts                    | P0     |   15h | done | [BE-V0.5-01-project-foundation.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-01-project-foundation.md)             |
| BE-V0.5-02 | 最小 database baseline、Alembic、core intent/notification tables | P0     | 22.5h | done | [BE-V0.5-02-database-baseline.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-02-database-baseline.md)               |
| BE-V0.5-03 | Local user context 與 owner scope placeholder                    | P0     |  7.5h | done | [BE-V0.5-03-local-user-context.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-03-local-user-context.md)             |
| BE-V0.5-04 | 最小 symbol seed、validation、lookup API                         | P0     |   15h | done | [BE-V0.5-04-symbol-seed-validation.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-04-symbol-seed-validation.md)     |
| BE-V0.5-05 | Tick-size 與 Decimal price domain services                       | P0     | 22.5h | done | [BE-V0.5-05-tick-size-decimal-price.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-05-tick-size-decimal-price.md)   |
| BE-V0.5-06 | 基本 trading session / day-intent rules                          | P0     |   15h | done | [BE-V0.5-06-trading-session-rules.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-06-trading-session-rules.md)       |
| BE-V0.5-07 | 單筆 buy/sell price alert create/cancel/list APIs                | P0     |   30h | done | [BE-V0.5-07-price-alert-apis.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-07-price-alert-apis.md)                 |
| BE-V0.5-08 | ~~Development quote adapter 與 quote validation~~                | P0     |    -  | superseded by BE-V0.5-13 | [BE-V0.5-08-dev-quote-adapter.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-08-dev-quote-adapter.md)               |
| BE-V0.5-09 | Quote evaluation、trigger transaction、minimal notification      | P0     |   32h | done | [BE-V0.5-09-quote-evaluation-trigger.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-09-quote-evaluation-trigger.md) |
| BE-V0.5-10 | Notification list/read APIs                                      | P1     |   15h | pending | [BE-V0.5-10-notification-apis.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-10-notification-apis.md)               |
| BE-V0.5-11 | 本地主流程 API examples 與 frontend handoff                      | P1     |   11h | pending | [BE-V0.5-11-api-examples-handoff.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-11-api-examples-handoff.md)         |
| BE-V0.5-12 | Integration tests：create -> quote -> trigger -> notification    | P0     |   20h | done | [BE-V0.5-12-integration-tests.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-12-integration-tests.md)               |
| BE-V0.5-13 | Shioaji Demo Quote Provider（取代 BE-V0.5-08）                   | P0     |   22h | done | [BE-V0.5-13-shioaji-quote-provider.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v0.5/BE-V0.5-13-shioaji-quote-provider.md)     |

## 5. V0.5 → V1 銜接重點

V0.5 已落地的設計決策影響 V1 工單拆解：

- **Quote provider demo/production 切分**（BE-V0.5-13）：上層 evaluator / intent service / API 只 import `app.services.quote.base` 與 `factory`，所有 Shioaji-specific 邏輯集中於 `app/services/quote/shioaji_demo/`。BE-V1-05 切換 licensed provider 時，新增檔案 + 從 factory mapping 刪除 demo provider + 刪除 `shioaji_demo/` 整個資料夾即可，**上層程式碼不動**。
- **Trigger transaction**（BE-V0.5-09）：原子寫入 `trade_intents` / `trigger_events` / `notifications` 三表的邊界清楚，BE-V1-06 把第 5 步 `notifications` 寫入改為寫 `outbox_events`，由 notification worker 派生 `notification_deliveries`。
- **Trading session**（BE-V0.5-06）：目前是 hardcode 9:00–13:30。BE-V1-04 接 market calendar importer 與 admin override，evaluator 與 day intent expiry / scheduled activation 都改為查 calendar service。
- **Symbol master**（BE-V0.5-04）：目前 5 檔 hardcoded seed。BE-V1-03 接 TWSE ISIN 匯入 + admin override readiness，需保留 BE-V0.5-13 demo allowlist 與 symbol seed 內容一致的不變式。
- **Owner scope**（BE-V0.5-03）：API 已不接受 client 傳入 `owner_user_id`，BE-V1-01/02 把 placeholder 替換為 auth context；所有既有 query / repository 不必改 owner-scoping 邏輯。
- **Decimal price + tick-size**（BE-V0.5-05）：BE-V1-09 的 round-away-from-trigger 直接擴充 tick-size service，不另開模組。

## 6. V1 Additional 工單索引

詳細工單位於 [docs/orders/v1](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1)。

| ID       | 工單                                                                       | 類型 | 優先序 |  預估 | 依賴                                       | 詳細文件 |
| -------- | -------------------------------------------------------------------------- | ---- | ------ | ----: | ------------------------------------------ | -------- |
| BE-V1-01 | 正式 Auth、sessions、CSRF、invitation、password reset                      | AFK  | P0     |   35h | BE-V0.5-03                                 | [BE-V1-01-auth-sessions.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-01-auth-sessions.md) |
| BE-V1-02 | 正式 role 與 owner-scope authorization                                     | AFK  | P0     |   20h | BE-V1-01, BE-V0.5-07                       | [BE-V1-02-role-authorization.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-02-role-authorization.md) |
| BE-V1-03 | Production symbol importer 與 admin override readiness                     | HITL | P0     |   30h | BE-V0.5-04                                 | [BE-V1-03-symbol-importer.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-03-symbol-importer.md) |
| BE-V1-04 | Production market calendar importer、scheduled activation/expiry           | HITL | P0     |   35h | BE-V0.5-06                                 | [BE-V1-04-market-calendar.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-04-market-calendar.md) |
| BE-V1-05 | Licensed quote provider adapter、quote health、pause/resume                | HITL | P0     | 42.5h | BE-V0.5-09, BE-V0.5-13, BE-V1-06           | [BE-V1-05-licensed-quote-provider.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-05-licensed-quote-provider.md) |
| BE-V1-06 | Outbox、notification delivery attempts、worker retry                       | AFK  | P0     |   35h | BE-V1-01, BE-V0.5-09, BE-V0.5-10           | [BE-V1-06-outbox-notification-worker.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-06-outbox-notification-worker.md) |
| BE-V1-07 | Take-profit / stop-loss strategy semantics                                 | AFK  | P1     |   30h | BE-V0.5-07, BE-V0.5-09                     | [BE-V1-07-take-profit-stop-loss.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-07-take-profit-stop-loss.md) |
| BE-V1-08 | OCO bracket alert group behavior                                           | AFK  | P1     |   35h | BE-V1-06, BE-V1-07                         | [BE-V1-08-oco-bracket.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-08-oco-bracket.md) |
| BE-V1-09 | Corporate action importer、cash dividend snapshot、effective price preview | HITL | P1     |   45h | BE-V0.5-05, BE-V1-03, BE-V1-04             | [BE-V1-09-corporate-action.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-09-corporate-action.md) |
| BE-V1-10 | CSV preview、draft、confirm 與 batch metadata                              | AFK  | P1     |   15h | BE-V1-02, BE-V1-07, BE-V1-08, BE-V1-09     | [BE-V1-10-csv-batch.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-10-csv-batch.md) |
| BE-V1-11 | Telegram bind/unbind 與 Telegram delivery worker                           | HITL | P1     |   20h | BE-V1-01, BE-V1-06                         | [BE-V1-11-telegram.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-11-telegram.md) |
| BE-V1-12 | User notification settings 與 notification center hardening                | AFK  | P1     |   20h | BE-V1-06, BE-V1-11                         | [BE-V1-12-notification-settings.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-12-notification-settings.md) |
| BE-V1-13 | Admin user management 與 account disable command                           | AFK  | P1     |   35h | BE-V1-01, BE-V1-02, BE-V0.5-07             | [BE-V1-13-admin-user-management.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-13-admin-user-management.md) |
| BE-V1-14 | Admin data overrides：symbols、calendar、corporate actions                 | AFK  | P2     |   30h | BE-V1-03, BE-V1-04, BE-V1-09               | [BE-V1-14-admin-data-overrides.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-14-admin-data-overrides.md) |
| BE-V1-15 | Admin monitoring、alerts、backlog metrics、kill switches                   | AFK  | P2     |   40h | BE-V1-04, BE-V1-05, BE-V1-06, BE-V1-09, BE-V1-11 | [BE-V1-15-admin-monitoring.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-15-admin-monitoring.md) |
| BE-V1-16 | Audit log、rate limits、retention、privacy anonymization                   | AFK  | P2     |   40h | BE-V1-01, BE-V1-06, BE-V1-13               | [BE-V1-16-audit-rate-limit-retention.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-16-audit-rate-limit-retention.md) |
| BE-V1-17 | EC2/RDS deployment readiness、backup/restore、production hardening         | AFK  | P2     |   20h | BE-V1-01, BE-V1-05, BE-V1-15               | [BE-V1-17-deployment-hardening.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-17-deployment-hardening.md) |
| BE-V1-18 | Production contract/integration/adapter test hardening                     | AFK  | P2     |   40h | BE-V1-05, BE-V1-11, BE-V1-16               | [BE-V1-18-production-tests.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-18-production-tests.md) |
| BE-V1-19 | Production OpenAPI examples 與 frontend contract fixtures                  | AFK  | P1     |   15h | BE-V1-10, BE-V1-12                         | [BE-V1-19-openapi-fixtures.md](/Users/hezongyu/Desktop/repository/ai-stock/docs/orders/v1/BE-V1-19-openapi-fixtures.md) |

## 7. V1 工單群組與建議交付順序

V1 19 張工單建議分成三批交付，每批內可並行：

### R1：身份與 owner scope 基線（P0 必須先完成）

- BE-V1-01 Auth / sessions / CSRF
- BE-V1-02 Role / authorization
- BE-V1-03 Symbol importer
- BE-V1-04 Market calendar + scheduled jobs

R1 完成後，V0.5 的 local user context 與 hardcoded session / symbol 全部退場。

### R2：真實資料流與策略擴充（P0 + P1 主流程）

- BE-V1-06 Outbox + notification delivery
- BE-V1-05 Licensed quote provider（取代 BE-V0.5-13 demo）
- BE-V1-07 Take-profit / stop-loss
- BE-V1-08 OCO bracket
- BE-V1-09 Corporate action importer + snapshot
- BE-V1-10 CSV preview / confirm
- BE-V1-11 Telegram bind / delivery
- BE-V1-12 Notification settings

R2 完成後，V1 對外完整功能（不含 admin / 監控）可上線測試。

### R3：營運、安全、部署（P2 + 最終測試）

- BE-V1-13 Admin user management
- BE-V1-14 Admin data overrides
- BE-V1-15 Admin monitoring / alerts / kill switches
- BE-V1-16 Audit log / rate limits / retention
- BE-V1-17 EC2/RDS deployment readiness
- BE-V1-18 Production tests
- BE-V1-19 OpenAPI examples / contract fixtures

R3 完成後可進入正式上線階段。
