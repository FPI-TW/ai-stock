# 正式環境維運

## 拓撲

production 採單機 EC2 + Docker Compose：

```text
Cloudflare / Internet
        │  HTTPS
        ▼
      Nginx
        │
        ▼
FastAPI app（API + quote + schedulers） ── AWS RDS / Aurora
        ├── GHCR image
        ├── Shioaji demo
        ├── AWS SES SMTP
        └── Telegram / DeepSeek
```

production Compose 不自架 PostgreSQL。`migrate` service 先對外部 DB 執行 `alembic upgrade head`，成功後才啟動單一 app；Nginx 等待 app healthcheck 通過。

## 設定與機密

`.env.prod.example` 是欄位清單，CD 由 GitHub Variables／Secrets 逐項產生 `.env.prod` 並以 `0600` 放到 EC2。production 必須：

- `LOCAL_MODE=false`，避免掛載 `/dev/*`。
- 提供 `DATABASE_URL`、`JWT_ACCESS_SECRET`、`MFA_ENCRYPTION_KEY`。`MFA_ENCRYPTION_KEY` 同時保護 admin TOTP 與 `broker_accounts` 的券商金鑰；`QUOTE_PROVIDER=fubon` 時不論 `LOCAL_MODE` 都必須設定（無 dev fallback），未設定時綁定回 500 `BROKER_CREDENTIAL_KEY_INVALID`。輪替金鑰後既有綁定無法解密：啟動、復權與重連時會把該列標為 `login_failed`、`lastError` 為「券商金鑰無法解密（加密金鑰已輪替），請重新綁定」，需請管理員重新綁定。
- 將 `CORS_ALLOW_ORIGINS`、`APP_BASE_URL` 改為正式前端網域。
- 讓 `TRUSTED_PROXY_IPS` 與 Compose backend subnet／實際代理鏈一致。
- 不在 log、shell history、Docker argv 或 Git 中保存原始 token、密碼與私鑰。

Quote、SES、Telegram 與 DeepSeek 設定皆以環境變數注入。SES 四個欄位必須全填或全空；啟用 Telegram inbound 時 production 會驗證必要欄位是否齊全。

## CI/CD

push 至 `main` 後 `.github/workflows/cd.yml` 依序：

1. 呼叫共用品質 workflow。
2. 建置 image，使用 commit SHA 與 `latest` 推到 GHCR。
3. 將 Compose、Nginx 與產生的 `.env.prod` 透過 SSH／SCP 送到 EC2。
4. 在遠端執行 `scripts/deploy.sh`，pull 指定 SHA、migration、啟動及 healthcheck。
5. app 健康後設定並驗證 Telegram webhook。

部署採序列化，不取消進行中的舊部署。EC2 不需要 Git checkout，也不在主機上 build image。

## TLS 與代理

- 對外只暴露 Nginx 80/443；app 只存在 Compose internal network。
- origin certificate 與 private key 位於 `/home/ubuntu/etc/ai-stock/tls/`，不得放入 repository。
- `deploy.sh` 在任何 Docker 操作前確認憑證存在且非空。
- Nginx 還原可信任代理提供的 client IP；設定改動需同步檢查 `TRUSTED_PROXY_IPS`，避免所有使用者共用一個限流 IP。

## 部署失敗與回滾

部署腳本會記錄目前 app image。migration、Compose 啟動或 healthcheck 失敗時，輸出近期 log 並以 `--no-deps` 嘗試回到前一個 app image，避免重新執行舊 migration。

Schema 已前進時 app image rollback 不代表 DB downgrade；migration 必須保持向前相容，無法安全回滾時由維運者人工處理。首次部署沒有前一版 image，也需人工介入。

## 健康與程序限制

- `GET /health` 會檢查 DB；失敗回 503。
- app container 的 `stop_grace_period` 為 40 秒：關機時 lifespan 會平行登出所有券商 session（每條最壞約 3 秒等 close frame），Docker 預設 10 秒 SIGKILL 會讓殘留 session 占住富邦連線上限。
- app container 使用單一 Uvicorn process。未先拆出 broker／scheduler ownership 或加入 leader election 前，不得增加 workers 或複本。
- TWAP、idempotency cleanup 與券商 session 重連迴圈在 app process 中運行；排程故障會記錄 log，但目前沒有獨立 worker dashboard。

## per-user 券商 session（`QUOTE_PROVIDER=fubon`）

- 一個 process 持有每位綁定使用者一條富邦 SDK session，記憶體隨綁定人數線性成長；`BROKER_MAX_SESSIONS`（1..10，預設 2）是應用層上限，達上限時綁定回 409。富邦登入連線硬上限 10 是每帳號還是每應用程式尚未實測；單一 SDK 實例 RSS 也待 Linux 量測後再調預設。
- 每次部署或重啟全員重登券商；異常關機（OS kill）殘留的富邦端 session 會佔用連線額度，等其 pong 逾時（約 30 秒 × 2）才釋放，重啟後若登入被拒可稍候再啟動。
- `GET /me/broker-account` 的 `status=login_failed` 表示最近一次登入（綁定、啟動、復權或重連）失敗，`lastError` 是白名單訊息。啟動或綁定當下的失敗不會自動重試，需重新綁定或重啟；交易時段內斷線後的重登由重連迴圈每 30 秒再試。
- 重連迴圈只在交易日台北 08:30～13:35 動作（目前只排除週末，平日休市仍會嘗試）；行情斷線到接回之間最多約 30 秒加重連耗時收不到行情，價位在這段時間掃過又回頭的觸發會漏掉，只通知階段接受。
- SDK 的 Rust 核心 panic 攔不住，會殺掉整個 process，所有使用者一起斷；Compose 的 restart policy 負責拉起來並全員重登。

## 尚未完成的維運能力

Repository 內尚未提供可驗證的每日備份、PITR policy、restore drill 紀錄、完整 production smoke runbook 或 SLO 儀表板。這些只能視為已知缺口，不得宣稱已具備；詳見 [已知技術債](technical-debt.md)。
