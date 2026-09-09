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
       ├── QuoteProvider ── Shioaji demo / in-memory
       ├── Telegram Bot API
       └── AWS SES SMTP
```

production 使用單一 `app` container，同時承載 API、quote callback、TWAP scheduler 與 idempotency cleanup。這是目前單機、小流量及 broker 單一登入限制下的刻意選擇；不可直接增加 Uvicorn workers，否則每個 process 都會重複登入券商、訂閱行情並啟動背景排程。

## 主要模組

- `api/routes`：HTTP transport、依賴注入與 response schema；不直接承擔 domain state transition。
- `commands`：交易邊界與 use case，例如建單、取消、觸發、帳號與 kill switch。
- `domain`：價格、交易時段、quote evaluation、intent 與 TWAP 的純規則。
- `repositories`：SQLAlchemy 查詢與持久化，不自行 commit 跨步驟 use case。
- `services/quote`：provider abstraction、Shioaji demo adapter、in-memory adapter 與訂閱協調。
- `services/notification_template.py`：通知 title/body 的唯一文字來源。

## 交易意圖資料流

1. 已認證使用者透過 typed endpoint 或 Telegram draft 建立非 TWAP intent。
2. command 驗證 symbol、tick、交易時段、建立上限與去重，在 `trade_intent_core` 及對應衛星表同一 transaction 寫入。
3. 新標的在 commit 前向 quote provider 訂閱；無法訂閱時建單一併 rollback。
4. 若建單當下已有新鮮且達標的 quote，command 在同一 transaction 寫入 trigger、notification 並直接轉為 `triggered`。
5. 後續 quote callback 交給 `TradeIntentCoreDispatcher`，以短 transaction 鎖定 active intent、評估、更新狀態並建立通知。
6. intent 進入 terminal state 後，subscription reconciler 在該 symbol 無其他有效 intent 時取消訂閱。

全域 kill switch 開啟時仍可建立 intent 與接收 quote，但 inline trigger、dispatcher 與 TWAP worker 不產生新觸發或通知；關閉後從最新行情繼續，不回放停止期間行情。

## 持久層

現行對外 API 使用：

- `trade_intent_core`：跨策略核心欄位與 `dedup_key`。
- `trade_intent_price_params`：到價與限價策略價格。
- `trade_intent_trailing_params`：移動出場參數及動態 baseline。
- `trade_intent_twap_params`：已建立但在 `main` 尚未接通的 TWAP 新軌參數表。
- `trade_intent_triggers`：新軌不可變的觸發快照。
- `notifications`：新舊軌共用的使用者收件匣。

`users`、refresh tokens、invitation、password reset、audit、idempotency keys 與 system flags 是共用基礎設施。TWAP confirm／slice worker 仍讀寫舊 `trade_intents` 與 `twap_slices`；其他舊單表路徑也仍存在但已凍結，詳見 [已知技術債](technical-debt.md)。

## 外部整合

- **Shioaji demo**：目前 runtime quote provider，adapter 細節隔離在 `services/quote/shioaji_demo/`；provider 上限與 allowlist 由設定控制。
- **Telegram outbound**：trigger／TWAP transaction 內建立 notification row 後直接 best-effort 呼叫 Bot API，失敗只寫安全 warning並繼續完成 DB transaction。
- **Telegram inbound**：webhook secret + chat allowlist，使用 DeepSeek 將文字分類成四種受支援 intent，確認後呼叫相同 core command。
- **AWS SES**：四個 SES 設定全有值時使用 SMTP，否則使用 logging stub；部分設定會在啟動時 fail fast。

目前沒有 transactional outbox、notification retry worker、corporate action importer、正式富邦 adapter 或真實下單路徑。

## 部署拓撲

GitHub Actions 測試後建置映像並推至 GHCR，再以 SSH 將 Compose、Nginx 與生成的 `.env.prod` 送至 EC2。Compose 先執行一次 Alembic migration，再啟動 app；Nginx 等待 healthcheck 通過後提供 80/443。PostgreSQL 位於外部 RDS／Aurora，不在 production Compose 內啟動。
