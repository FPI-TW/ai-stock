# V1 PM 規劃：交易意圖與到價通知

## 1. 產品目標

V1 是台股與台股 ETF 的 notify-only 交易意圖提醒後端。系統不串接券商、不送出真實委託，也不建立券商委託草稿。

產品承諾：

- 使用者可以建立台股與台股 ETF 的價格提醒意圖。
- 系統在台股一般盤交易時段監控近即時行情。
- 系統會針對支援的現金股利除息事件調整有效目標價。
- 當意圖觸發、暫停、無效、恢復、過期，或因帳號停用被取消時，系統會發送站內通知與可選的 Telegram 通知。
- Admin 可以操作核心資料、使用者帳號、健康監控、資料覆寫、稽核紀錄與 kill switch。

## 2. V1 範圍

### 範圍內

- 台股現股與台股 ETF。
- 整股張數。
- 透過 Web UI API 單筆建立提醒。
- 透過 Web UI API 進行 CSV validation、preview 與 all-or-nothing batch 建立。
- 買進與賣出到價提醒。
- 多單與使用者聲明空單持倉的停利 / 停損提醒。
- 停利 + 停損 OCO bracket alert group。
- 現金股利固定金額調整。
- 使用者層級通知設定。
- 站內通知與 Telegram 通知派送。
- Admin 建立使用者、邀請流程、密碼重設、admin 2FA、帳號停用。
- Symbol master、market calendar、corporate action 匯入與 admin override。
- Quote evaluator worker、notification worker、scheduled jobs、audit log、admin alerts、kill switches。

### 範圍外

- 券商串接。
- 真實下單。
- 市價單。
- TWAP。
- 移動停利 / 移動停損。
- 零股或金額模式。
- 空單進場。
- Line 通知。
- 從 Telegram 文字、語音或 CSV 建立提醒。
- 多帳戶、多投資組合、團隊協作或代理建立。
- 編輯已建立的交易意圖。
- 使用者資料匯出。

## 3. 主要使用者旅程

1. Admin 建立使用者並寄出 invitation。
2. 使用者啟用帳號、登入，並可選擇綁定 Telegram。
3. 使用者建立單筆買進或賣出到價提醒。
4. 使用者建立停利、停損或 OCO bracket alert。
5. 使用者上傳 CSV，檢視後端權威 preview，確認後批次建立。
6. Quote evaluator 偵測到有效觸發條件，透過 outbox 建立通知工作。
7. 使用者收到站內通知；若 Telegram 已綁定且啟用，也收到 Telegram 訊息。
8. 使用者查看 active、scheduled、paused、triggered、expired、cancelled、invalid intents。
9. 使用者取消 active 或 scheduled intents。
10. Admin 監控資料健康、通知失敗、job 狀態與 kill switches。

## 4. 需要明確呈現的產品文案規則

- 提醒僅為通知。
- 系統沒有下單。
- 通知不構成投資建議。
- 通知不保證即時、不保證下單、不保證成交。
- 若有除息調整，必須顯示原始目標價、調整金額與有效目標價。
- 若目前價格已符合條件，仍允許建立，且建立後可能立即觸發。
- 已建立的 intent 不可編輯；使用者必須取消並重建。
- Telegram 是可選通道；站內通知是必開通道。

## 5. 交付里程碑

### M1：後端基礎

目標：建立可部署的 FastAPI / PostgreSQL 後端骨架與核心 domain primitives。

包含 auth 基礎、migration、domain enums、idempotency、error envelope、command handler conventions、audit log 與測試框架。

### M2：單筆提醒完整路徑

目標：支援單筆 intent 從建立、觸發到通知的完整流程。

包含 symbol validation、market calendar、tick validation、quote adapter、create/cancel APIs、quote evaluator、trigger event、outbox、站內通知與列表 / 歷史 API。

### M3：策略完整性與資料調整

目標：完成 V1 策略語意。

包含停利 / 停損、OCO groups、現金股利 snapshot、不支援 corporate action 的 pause 行為、invalid-for-day、market status pause/resume。

### M4：CSV、Telegram 與 Admin 營運

目標：支援批次建立與營運準備。

包含 CSV preview/confirm、Telegram 綁定與派送、admin user management、symbol/corporate action/calendar overrides、monitoring dashboards、alerts、kill switches 與 retention jobs。

### M5：強化與 release readiness

目標：達到測試、SLO、安全與資料保留要求。

包含 adapter contract tests、integration tests、backup/restore 演練清單、rate limits、cross-user authorization tests 與 production readiness 文件。

## 6. 風險與待決策

- Quote vendor 尚未選定。後端應先交付 adapter contracts 與 fake/test adapter，但正式上線依賴合法授權 vendor，且需提供 bid、ask、last、quote time、TWSE 與 TPEx 覆蓋。
- Corporate action source 已有政策方向，但 importer 細節需要用 TWSE/TPEx 實際資料格式驗證。
- 前端會另開 repo。後端需要及早提供穩定 API contracts、error codes 與範例。
- Admin support workflows 需要確認：查看完整使用者 intent 詳情與 2FA reset 的政策。
- V1 SLO 依賴基礎設施選型，例如 worker scheduling、database sizing、backup 與 monitoring。

## 7. V1 工單簡表

估時為後端 engineer-hours，不包含前端頁面實作、vendor 合約談判、生產基礎設施採購、PM/legal review。

| ID       | 工單                                                                     | 優先序 | 預估 | 依賴                                   |
| -------- | ------------------------------------------------------------------------ | ------ | ---: | -------------------------------------- |
| BE-V1-01 | 專案基礎、config、health、CI quality scripts                             | P0     |  16h | 無                                     |
| BE-V1-02 | Database baseline、Alembic、enums、audit/outbox primitives               | P0     |  32h | BE-V1-01                               |
| BE-V1-03 | Auth、sessions、CSRF、invitation、password reset                         | P0     |  40h | BE-V1-02                               |
| BE-V1-04 | Role 與 owner-scope authorization                                        | P0     |  24h | BE-V1-03                               |
| BE-V1-05 | Symbol master import、validation、autocomplete API                       | P0     |  32h | BE-V1-02                               |
| BE-V1-06 | Market calendar service 與 day-intent trading date rules                 | P0     |  32h | BE-V1-02                               |
| BE-V1-07 | Tick-size 與 Decimal price domain services                               | P0     |  24h | BE-V1-02                               |
| BE-V1-08 | 單筆 price alert create/cancel/list APIs                                 | P0     |  40h | BE-V1-03, BE-V1-05, BE-V1-06, BE-V1-07 |
| BE-V1-09 | Quote provider adapter 與 quote validation                               | P0     |  32h | BE-V1-05                               |
| BE-V1-10 | Quote evaluator trigger transaction 與 immediate-trigger path            | P0     |  48h | BE-V1-08, BE-V1-09                     |
| BE-V1-11 | Notification model、outbox worker、in-app delivery                       | P0     |  40h | BE-V1-10                               |
| BE-V1-12 | Take-profit / stop-loss strategy semantics                               | P1     |  32h | BE-V1-08, BE-V1-10                     |
| BE-V1-13 | OCO bracket alert group behavior                                         | P1     |  40h | BE-V1-12                               |
| BE-V1-14 | Corporate action import、cash dividend snapshot、effective price preview | P1     |  56h | BE-V1-05, BE-V1-06, BE-V1-07           |
| BE-V1-15 | Invalid-for-day、expiry、activation、pause/resume scheduled jobs         | P1     |  48h | BE-V1-06, BE-V1-10, BE-V1-14           |
| BE-V1-16 | CSV preview、draft、confirm 與 batch metadata                            | P1     |  48h | BE-V1-08, BE-V1-12, BE-V1-13, BE-V1-14 |
| BE-V1-17 | Telegram bind/unbind 與 Telegram delivery worker                         | P1     |  40h | BE-V1-03, BE-V1-11                     |
| BE-V1-18 | User notification settings 與 notification center APIs                   | P1     |  24h | BE-V1-11, BE-V1-17                     |
| BE-V1-19 | Admin user management 與 account disable command                         | P1     |  40h | BE-V1-03, BE-V1-04, BE-V1-08           |
| BE-V1-20 | Admin data overrides：symbols、calendar、corporate actions               | P2     |  40h | BE-V1-05, BE-V1-06, BE-V1-14           |
| BE-V1-21 | Admin monitoring、alerts、backlog metrics、kill switches                 | P2     |  48h | BE-V1-10, BE-V1-11, BE-V1-15           |
| BE-V1-22 | Rate limits、retention、privacy anonymization、release hardening         | P2     |  40h | BE-V1-03, BE-V1-11, BE-V1-19           |
| BE-V1-23 | Contract/integration test suite 與 fake adapters                         | P0     |  56h | BE-V1-08, BE-V1-10, BE-V1-11           |
| BE-V1-24 | API contract examples 與 frontend handoff package                        | P1     |  24h | BE-V1-08, BE-V1-16, BE-V1-18           |

後端總估時：900 engineer-hours。
