# 開發指南

## 環境需求與安裝

- Python 3.13
- uv
- Docker／Docker Compose（本地 PostgreSQL 與 integration tests）

```bash
cp .env.example .env
make install
docker compose up -d postgres
make migrate
make seed
make bootstrap-admin
make dev
```

本地 API 預設為 `http://127.0.0.1:8100`。`.env.example` 是完整本地設定範本；至少要把 `LOCAL_USER_ID` 換成有效 UUID。Shioaji、SES、Telegram 與 DeepSeek 憑證都不可提交至 Git。

## 資料庫

本地 Compose 對外使用 port `5434`，Makefile 的預設 `DATABASE_URL` 已對齊。

```bash
make migrate
make downgrade
make seed
make test-integration
```

Alembic migrations 位於 `src/app/db/migrations/versions/`。migration 不自動 seed；`make seed` 會以 idempotent upsert 建立 demo symbols。新增 schema 時需同時更新 SQLAlchemy model、upgrade／downgrade 與 PostgreSQL integration tests。

## Admin 帳號

本地首次啟動可依 `.env` 的 `INITIAL_ADMIN_EMAIL`／`INITIAL_ADMIN_PASSWORD` 建立 initial admin：

```bash
make bootstrap-admin
```

建立指定 email 的 admin，密碼由互動提示輸入或安全產生：

```bash
make create-admin EMAIL=dev@example.com
```

新 admin 登入後需完成：

1. `POST /admin/2fa/setup` 取得 `provisioningUri` 與一次性 secret。
2. 將 secret 加入 authenticator app。
3. `POST /admin/2fa/verify` 送出六位數 TOTP，取得帶 `mfa_verified` 的新 access token。

不要把 TOTP secret 貼到線上 QR 產生器。production 的建立方式與 secret 管理見 [維運指南](operations.md)。

## 品質指令

```bash
make lint
make format-check
make typecheck
make test
make check
make test-integration
```

- `make check`：ruff lint、format check、mypy、Shioaji adapter 隔離與非 PostgreSQL tests。
- `make test-integration`：需要本地 PostgreSQL，執行 `integration` marker tests。
- `make format` 會改寫檔案；執行後需重新檢查並 stage。

## Git hooks

```bash
make install-hooks
```

pre-commit 執行 format-check 與 typecheck；pre-push 執行 format-check、typecheck 與一般 tests。PostgreSQL integration tests 維持手動執行。

Git、PR 與文件維護規則見根目錄 [AGENTS.md](../AGENTS.md)。
