# 系統架構

本文件描述 `main` 目前已落地的架構。未來方向見 [產品定位](product.md)，尚未實作的停利／停損見 [工單](orders/stop-loss-take-profit.md)。

## 執行單元

應用是 Python 3.13 + FastAPI 的同步 PostgreSQL 後端：

```text
Web / Telegram
       │
       ▼
FastAPI（auth、admin、intent、notification）
       │
       ├── PostgreSQL（SQLAlchemy + psycopg + Alembic）
       ├── BrokerSessionPool ── Fubon Neo（每使用者一條 session）/ Shioaji demo / in-memory（共用）
       ├── Telegram Bot API
       └── AWS SES SMTP
```

production 使用單一 `app` container，同時承載 API、quote callback、TWAP scheduler、idempotency cleanup 與券商 session 重連迴圈。一個 process 持有 N 條券商 session（每位綁定使用者一條）。這是目前單機、小流量及券商登入連線上限下的刻意選擇；不可直接增加 Uvicorn workers，否則每個 process 都會重複登入每位使用者的券商帳號、訂閱行情並啟動背景排程。

## 主要模組

- `api/routes`：HTTP transport、依賴注入與 response schema；不直接承擔 domain state transition。
- `commands`：交易邊界與 use case，例如建單、取消、觸發、帳號與 kill switch。
- `domain`：價格、交易時段、quote evaluation、intent 與 TWAP 的純規則。
- `repositories`：SQLAlchemy 查詢與持久化，不自行 commit 跨步驟 use case。
- `services/quote`：provider abstraction、Fubon adapter、Shioaji demo adapter、in-memory adapter 與訂閱協調。
- `services/broker_session_pool.py`：每位使用者一條券商 session 的持有者；`services/broker_session_reconnect.py`：交易時段內修復斷線的迴圈。
- `services/notification_template.py`：通知 title/body 的唯一文字來源。

## 交易意圖資料流

1. 已認證使用者透過 typed endpoint 或 Telegram draft 建立非 TWAP intent。
2. command 驗證 symbol、tick、交易時段、建立上限與去重，向 `BrokerSessionPool` 取得 owner 本人的行情 session（per-user 模式未綁定券商即 409 `BROKER_ACCOUNT_NOT_BOUND`，不落列），再在 `trade_intent_core` 及對應衛星表同一 transaction 寫入。
3. 新標的在 commit 前向本人 session 訂閱；無法訂閱時建單一併 rollback。
4. 若建單當下已有新鮮且達標的 quote，command 在同一 transaction 寫入 trigger、notification 並直接轉為 `triggered`。
5. 後續 quote callback 交給 `TradeIntentCoreDispatcher`，以短 transaction 鎖定 active intent、評估、更新狀態並建立通知。per-user 模式下同一 symbol 會從 N 條 session 各推一次，pool 以 owner 綁定 listener，每次只評估該 session 主人的 intent；shared 模式掃全部 owner。
6. intent 進入 terminal state 後，subscription reconciler 在該 symbol 無其他有效 intent 時取消訂閱：per-user 模式只數本人的 intent（別人的單不在本人的 session 上），shared 模式數全站。

全域 kill switch 開啟時仍可建立 intent 與接收 quote，但 inline trigger、dispatcher 與 TWAP worker 不產生新觸發或通知；關閉後從最新行情繼續，不回放停止期間行情。

## 持久層

現行對外 API 使用：

- `trade_intent_core`：跨策略核心欄位與 `dedup_key`。
- `trade_intent_price_params`：到價與限價策略價格。
- `trade_intent_trailing_params`：移動出場參數及動態 baseline。
- `trade_intent_twap_params`：已建立但在 `main` 尚未接通的 TWAP 新軌參數表。
- `trade_intent_triggers`：新軌不可變的觸發快照。
- `notifications`：新舊軌共用的使用者收件匣。

`broker_accounts`：每位使用者一筆券商綁定（`user_id` unique），登入四件套以 `MFA_ENCRYPTION_KEY` Fernet 加密成單一 `credentials_encrypted` blob，只有 `BrokerAccountRepository` 會加解密；`last_error` 只存 `app.domain.broker_account` 的白名單訊息。

`users`、refresh tokens、invitation、password reset、audit、idempotency keys 與 system flags 是共用基礎設施。TWAP confirm／slice worker 仍讀寫舊 `trade_intents` 與 `twap_slices`；其他舊單表路徑也仍存在但已凍結，詳見 [已知技術債](technical-debt.md)。

## 外部整合

- **Shioaji demo**：共用系統帳號的示範模式 quote provider，adapter 細節隔離在 `services/quote/shioaji_demo/`；provider 上限與 allowlist 由設定控制。
- **Fubon Neo**：per-user 行情 provider，隔離在 `services/quote/fubon/`（`client.py` 是唯一 `import fubon_neo` 的模組，wheel 只有 Linux 版，本機測試以 fake SDK 注入）。`QUOTE_PROVIDER=fubon` 沒有系統帳號，每位綁定使用者一條 session，登入材料是該使用者的 `FubonCredentials`（`domain/broker_account.py`）。
- **BrokerSessionPool**（`services/broker_session_pool.py`，掛在 `app.state.broker_sessions`）是所有行情來源的唯一入口：`fubon` 為 per-user 模式（`BROKER_MAX_SESSIONS` 為應用層上限，綁定採 `prepare` → 訂閱／落庫 → `activate` 的兩階段切換，重綁失敗不影響現行 session）；`in_memory`／demo provider 為 shared 模式，所有人共用同一個 provider。`pool.require(user_id)` 是建單、`GET /quotes/current-price`、`/dev/*` 與 TWAP 取行情的唯一途徑。
- **per-user 模式的生命週期**：lifespan 對 `broker_accounts` 每人一個 worker 平行登入（各自開 DB session；10 人的開機時間約等於一次登入而非十次；可還原的綁定數超過 `BROKER_MAX_SESSIONS` 時依綁定時間先來先登入，超額者標 `session_pool_full`、不碰券商，等有人解綁或停用空出位子由重連迴圈補進），每人 `prepare` → 訂閱該使用者的有效標的 → `mark_login_ok` + commit → `activate`；單人登入失敗只把該列標為 `login_failed`（安全訊息）並繼續，無人綁定時也能啟動、行情全停等管理員綁第一位。停用帳號登出其 session、列保留，開機與重連都會先檢查使用者狀態，停用中的綁定不登入；復權時以同一流程重登，登入失敗不讓復權失敗。關機 `stop_all` 平行登出。`BrokerSessionReconnectLoop` 每 30 秒一次、只在交易日台北 08:30～13:35 動作：行情 WS 斷線（富邦收盤後會主動斷、SDK 不重連）呼叫 `reconnect_realtime()` 重連並重訂；斷線期間（盤後、週末、盤中接回前）建單只把標的記進 provider 的訂閱集、不對死 socket 送出，重連時整組補訂，所以盤後建 scheduled 單不會 503；登入斷線（交易端事件 300／301／304）或根本沒有 session 的綁定（啟動時登入失敗、崩潰重啟撞上券商殘留 session 窗口、綁定 activate 失敗）以存放的金鑰重登並原子替換，失敗下個 tick 再試、不退避；`lastError` 屬「券商拒絕登入／憑證無效／金鑰無法解密」者不重試，等管理員重綁。迴圈每輪拿 `broker_accounts` 對照當下 live 的 session、不跨輪記憶，被停用／解綁（session 已登出）或已重綁（新 session 健康）的人不會被重登；重登與綁定、停用、解綁搶同一個 per-user token，且拿到 token 後會再確認使用者仍為 active 且綁定列仍在，才 commit 與切換。迴圈只做修復，不產生健康狀態或通知。
- **舊軌在 per-user 模式**：沒有系統 session，舊軌 `QuoteEvaluationDispatcher` 不掛；`TwapSliceScheduler` 注入空的 `InMemoryQuoteProvider`，slice 照時間通知但沒有參考價，見 [已知技術債](technical-debt.md)。
- **Telegram outbound**：trigger／TWAP transaction 內建立 notification row 後直接 best-effort 呼叫 Bot API，失敗只寫安全 warning並繼續完成 DB transaction。
- **Telegram inbound**：webhook secret + chat allowlist，使用 DeepSeek 將文字分類成四種受支援 intent，確認後呼叫相同 core command。
- **AWS SES**：四個 SES 設定全有值時使用 SMTP，否則使用 logging stub；部分設定會在啟動時 fail fast。

目前沒有 transactional outbox、notification retry worker、corporate action importer、正式富邦 adapter 或真實下單路徑。

## 部署拓撲

GitHub Actions 測試後建置映像並推至 GHCR，再以 SSH 將 Compose、Nginx 與生成的 `.env.prod` 送至 EC2。Compose 先執行一次 Alembic migration，再啟動 app；Nginx 等待 healthcheck 通過後提供 80/443。PostgreSQL 位於外部 RDS／Aurora，不在 production Compose 內啟動。
