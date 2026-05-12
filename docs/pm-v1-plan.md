# V0.5 / V1 PM 規劃：交易意圖與到價通知

## 1. 產品目標

產品最終 V1 是台股與台股 ETF 的 notify-only 交易意圖提醒後端。系統不串接券商、不送出真實委託，也不建立券商委託草稿。

因為有 demo 需求，交付拆成兩版：

- `v0.5`：本地可跑的最小正式垂直切片。無註冊、無登入、無 admin，不部署到 EC2/RDS；在本地裝置跑通「建立單筆到價提醒 → quote 達標 → intent 觸發 → 產生站內通知 → 列表可查看」。
- `v1`：正式上線版，部署於 EC2 + RDS，補齊 auth、CSV、Telegram、admin、真實資料來源、營運監控、安全與資料保留。

V0.5 的設計原則是盡量避免 demo-only 或做一半的產品功能：

- 只做後續 V1 會沿用的 domain/API/DB 核心。
- 還沒用到的表先不建。
- Audit、delivery attempts、import reports、retention records 這類紀錄型功能先不做。
- 先假設不會遇到瞬間大流量，不先做 worker scaling、outbox、queue claim/lock。
- Demo quote 只作為本地 development adapter，不包裝成正式產品功能。

產品承諾：

- 使用者可以建立台股與台股 ETF 的價格提醒意圖。
- 系統在台股一般盤交易時段監控近即時行情。
- 系統會針對支援的現金股利除息事件調整有效目標價。
- 當意圖觸發、暫停、無效、恢復、過期，或因帳號停用被取消時，系統會發送站內通知與可選的 Telegram 通知。
- Admin 可以操作核心資料、使用者帳號、健康監控、資料覆寫、稽核紀錄與 kill switch。

## 2. V0.5 範圍

### V0.5 範圍內

- 本地執行 FastAPI + PostgreSQL。
- 固定 single local user context，不做註冊、登入、session、CSRF。
- 最小 symbol master，支援台股現股 / ETF 範例標的與 symbol validation。
- Tick-size validation 與 Decimal price handling。
- 單筆買進 / 賣出到價提醒建立、取消、列表。
- `day` time-in-force 的基本 trading date 與一般盤判斷。
- Development quote adapter，用於本地推進 quote，驅動同一套 quote evaluation domain logic。
- Quote evaluation 與 trigger transaction。
- 最小站內通知：intent 觸發後建立可列表查看的 notification。
- 最小 API examples，供前端 repo 串本地主流程。
- Integration tests 覆蓋主流程。

### V0.5 明確不做

- 註冊、登入、invitation、password reset、refresh token、CSRF。
- Admin dashboard 與 admin APIs。
- Telegram 綁定與 Telegram delivery。
- CSV preview / confirm。
- 停利 / 停損、OCO bracket alert group。
- Corporate action importer、cash dividend adjustment、effective price preview。
- 真實 quote vendor、真實 symbol importer、真實 market calendar importer。
- Audit log、outbox、notification delivery attempts、import reports。
- Activation / expiry scheduled jobs；v0.5 可在 API 查詢或 evaluator 前用 session check 保護，不做背景 job。
- Pause/resume、quote unhealthy recovery、market status recovery。
- Kill switches、monitoring、alerts、retention、privacy anonymization。
- EC2/RDS 部署與 production hardening。

### V0.5 可接受的取捨

- 不部署，僅本地裝置展示。
- 無身份系統，因此不展示多使用者與權限隔離。
- 不處理大流量與多 worker 併發。
- 不保證通知 delivery 語意，只展示站內通知資料已建立。
- 不處理正式行情 vendor failure；development quote adapter 僅用於本地測試與展示。
- 不展示除息調整、OCO 或 CSV，避免把尚未完整產品化的功能做一半。

## 3. V1 正式範圍

### V1 範圍內

- 部署於 EC2 + RDS。
- 台股現股與台股 ETF。
- 整股張數。
- Auth、invitation、password reset、session / refresh token rotation、CSRF。
- Owner-scope authorization。
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
- Rate limits、retention、privacy anonymization、production hardening。

### V1 範圍外

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

## 4. V0.5 本地主流程

1. 本地啟動 API 與 PostgreSQL。
2. 系統使用固定 local user context。
3. 前端或 API client 建立單筆買進 / 賣出到價提醒。
4. 後端驗證 symbol、quantity、tick size、trading date、duplicate。
5. 本地 development quote adapter 更新 quote。
6. Quote evaluator 使用與 V1 相同的 domain logic 判斷條件成立。
7. 同一個 transaction 將 intent 轉為 `triggered`，並建立最小站內 notification。
8. 前端或 API client 透過列表看到 intent 狀態與 notification。

## 5. V1 完整使用者旅程

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

## 6. 需要明確呈現的產品文案規則

- 提醒僅為通知。
- 系統沒有下單。
- 通知不構成投資建議。
- 通知不保證即時、不保證下單、不保證成交。
- 若有除息調整，必須顯示原始目標價、調整金額與有效目標價。
- 若目前價格已符合條件，仍允許建立，且建立後可能立即觸發。
- 已建立的 intent 不可編輯；使用者必須取消並重建。
- Telegram 是可選通道；站內通知是必開通道。

## 7. 交付里程碑

### D1：V0.5 本地後端骨架

目標：建立可本地啟動的 FastAPI / PostgreSQL 後端、migration、固定 local user context、基本 symbol seed。

### D2：V0.5 單筆到價提醒主流程

目標：跑通單筆 price alert create、cancel、list、development quote update、quote evaluation、trigger、notification list。

### D3：V0.5 前端串接與回歸測試

目標：提供最小 API examples，讓前端 repo 能串接本地主流程，並以 integration tests 固定 demo path。

### R1：V1 Auth 與正式 owner scope

目標：將 local user context 替換成正式 invitation、login、session、CSRF、authorization。

### R2：V1 策略、CSV、Telegram 與正式資料來源

目標：補齊停利 / 停損、OCO、CSV batch、Telegram binding/delivery、licensed quote provider、symbol/calendar/corporate-action importers。

### R3：V1 Admin、監控與 release hardening

目標：補齊 admin user management、data overrides、monitoring、alerts、kill switches、rate limits、retention、privacy、EC2/RDS deployment readiness 與正式測試。

## 8. 風險與待決策

- Quote vendor 尚未選定。`v0.5` 只使用本地 development quote adapter；`v1` 正式上線仍依賴合法授權 vendor，且需提供 bid、ask、last、quote time、TWSE 與 TPEx 覆蓋。
- `v0.5` 無登入且不部署，不應公開到網路。若必須遠端展示，應以環境層 access control 保護，而不是在 v0.5 內臨時做半套 auth。
- Corporate action source 已有政策方向，但 importer 細節需要用 TWSE/TPEx 實際資料格式驗證；`v0.5` 不先做除息調整。
- 前端會另開 repo。`v0.5` 只需提供本地主流程 API examples；`v1` 再交付完整 OpenAPI/fixtures。
- Admin support workflows 需要確認：查看完整使用者 intent 詳情與 2FA reset 的政策。
- V1 SLO 依賴 EC2/RDS 規格、worker scheduling、database sizing、backup 與 monitoring。

## 9. V0.5 工單簡表

估時為後端 engineer-hours，不包含前端頁面實作、vendor 合約談判、生產基礎設施採購、PM/legal review。

| ID         | 工單                                                             | 優先序 |  預估 | 依賴                                           |
| ---------- | ---------------------------------------------------------------- | ------ | ----: | ---------------------------------------------- |
| BE-V0.5-01 | 本地專案基礎、config、health、quality scripts                    | P0     |   15h | 無                                             |
| BE-V0.5-02 | 最小 database baseline、Alembic、core intent/notification tables | P0     | 22.5h | BE-V0.5-01                                     |
| BE-V0.5-03 | Local user context 與 owner scope placeholder                    | P0     |  7.5h | BE-V0.5-02                                     |
| BE-V0.5-04 | 最小 symbol seed、validation、lookup API                         | P0     |   15h | BE-V0.5-02                                     |
| BE-V0.5-05 | Tick-size 與 Decimal price domain services                       | P0     | 22.5h | BE-V0.5-02                                     |
| BE-V0.5-06 | 基本 trading session / day-intent rules                          | P0     |   15h | BE-V0.5-02                                     |
| BE-V0.5-07 | 單筆 buy/sell price alert create/cancel/list APIs                | P0     |   30h | BE-V0.5-03, BE-V0.5-04, BE-V0.5-05, BE-V0.5-06 |
| BE-V0.5-08 | Development quote adapter 與 quote validation                    | P0     |   18h | BE-V0.5-04, BE-V0.5-06                         |
| BE-V0.5-09 | Quote evaluation、trigger transaction、minimal notification      | P0     |   32h | BE-V0.5-07, BE-V0.5-08                         |
| BE-V0.5-10 | Notification list/read APIs                                      | P1     |   15h | BE-V0.5-09                                     |
| BE-V0.5-11 | 本地主流程 API examples 與 frontend handoff                      | P1     |   11h | BE-V0.5-07, BE-V0.5-10                         |
| BE-V0.5-12 | Integration tests：create → quote → trigger → notification       | P0     |   20h | BE-V0.5-07, BE-V0.5-09                         |

V0.5 小計：223.5 engineer-hours。

## 10. V1 Additional 工單簡表

| ID       | 工單                                                                       | 優先序 |  預估 | 依賴                           |
| -------- | -------------------------------------------------------------------------- | ------ | ----: | ------------------------------ |
| BE-V1-01 | 正式 Auth、sessions、CSRF、invitation、password reset                      | P0     |   35h | BE-V0.5                        |
| BE-V1-02 | 正式 role 與 owner-scope authorization                                     | P0     |   20h | BE-V1-01                       |
| BE-V1-03 | Production symbol importer 與 admin override readiness                     | P0     |   30h | BE-V0.5-04                     |
| BE-V1-04 | Production market calendar importer、scheduled activation/expiry           | P0     |   35h | BE-V0.5-06                     |
| BE-V1-05 | Licensed quote provider adapter、quote health、pause/resume                | P0     | 42.5h | BE-V0.5-08, BE-V0.5-09         |
| BE-V1-06 | Outbox、notification delivery attempts、worker retry                       | P0     |   35h | BE-V0.5-09, BE-V0.5-10         |
| BE-V1-07 | Take-profit / stop-loss strategy semantics                                 | P1     |   30h | BE-V0.5-07, BE-V0.5-09         |
| BE-V1-08 | OCO bracket alert group behavior                                           | P1     |   35h | BE-V1-07                       |
| BE-V1-09 | Corporate action importer、cash dividend snapshot、effective price preview | P1     |   45h | BE-V1-03, BE-V1-04             |
| BE-V1-10 | CSV preview、draft、confirm 與 batch metadata                              | P1     |   15h | BE-V1-07, BE-V1-08, BE-V1-09   |
| BE-V1-11 | Telegram bind/unbind 與 Telegram delivery worker                           | P1     |   20h | BE-V1-01, BE-V1-06             |
| BE-V1-12 | User notification settings 與 notification center hardening                | P1     |   20h | BE-V1-06, BE-V1-11             |
| BE-V1-13 | Admin user management 與 account disable command                           | P1     |   35h | BE-V1-01, BE-V1-02, BE-V0.5-07 |
| BE-V1-14 | Admin data overrides：symbols、calendar、corporate actions                 | P2     |   30h | BE-V1-03, BE-V1-04, BE-V1-09   |
| BE-V1-15 | Admin monitoring、alerts、backlog metrics、kill switches                   | P2     |   40h | BE-V1-05, BE-V1-06             |
| BE-V1-16 | Audit log、rate limits、retention、privacy anonymization                   | P2     |   40h | BE-V1-01, BE-V1-06, BE-V1-13   |
| BE-V1-17 | EC2/RDS deployment readiness、backup/restore、production hardening         | P2     |   20h | BE-V1-01, BE-V1-05, BE-V1-15   |
| BE-V1-18 | Production contract/integration/adapter test hardening                     | P0     |   40h | BE-V1-05, BE-V1-11, BE-V1-16   |
| BE-V1-19 | Production OpenAPI examples 與 frontend contract fixtures                  | P1     |   15h | BE-V1-10, BE-V1-12             |

V1 additional 小計：582.5 engineer-hours。

總估時：806 engineer-hours。
