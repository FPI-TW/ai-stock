# AI Stock 後端

V0.5 交易意圖與到價提醒流程的本地 FastAPI 後端。

V0.5 僅支援本地模式。不要將這份設定公開部署。

## 環境需求

- Python 3.13
- uv
- Docker，用於本地 PostgreSQL integration tests

## 安裝

```bash
cp .env.example .env
make install
```

## 本地執行

啟動 PostgreSQL：

```bash
docker compose up -d postgres
```

啟動 API：

```bash
make dev
```

健康檢查：

```bash
curl http://127.0.0.1:8100/health
```

前端串接本地主流程與必要 headers，見
[`docs/api/v0.5-local-flow.md`](docs/api/v0.5-local-flow.md)。`POST /trade-intents`
和 cancel 這類 mutating APIs 必須帶 `Idempotency-Key` header。

## 品質指令

```bash
make lint
make format-check
make typecheck
make test
```

`make check` 會執行不需要 PostgreSQL 的品質門檻：lint、format-check、typecheck、unit/API tests。

## Git hooks

本 repo 使用 `pre-commit` 管理 Git hooks。

macOS / Linux：

```bash
make install-hooks
```

Windows（Git for Windows）也可直接執行：

```powershell
git config --unset core.hooksPath
uv run pre-commit install --install-hooks --hook-type pre-commit --hook-type pre-push
```

- hook 設定集中在 `.pre-commit-config.yaml`。
- `pre-commit` 會執行 format、lint、format-check、typecheck。任一指令失敗時 commit 會被阻擋。
- 若 formatter 修改檔案，`pre-commit` 會中止 commit；請檢查並 stage 格式化後的檔案，再重新 commit。
- `pre-push` 會執行一般測試，等同 `uv run pytest`。測試失敗時 push 會被阻擋。
- `make test-integration` 需要本地 PostgreSQL，仍維持手動執行，不放入 push hook。

型別檢查使用 mypy 作為正式品質門檻：

- `make typecheck` 執行 `uv run mypy src tests`。
- mypy 覆蓋 `src` 與 `tests`，並使用 `mypy_path = "src"`。
- baseline 要求 function definition 有型別註記，並檢查未標註型別的 function body。
- baseline 暫不啟用完整 `strict = true` 或 `disallow_any_*`，因為 Alembic、SQLAlchemy、pytest fixtures 需要分階段強化。
- `make test-integration` 需要本地 PostgreSQL，因此維持獨立執行。

## Database Migrations

先啟動本地 PostgreSQL：

```bash
docker compose up -d postgres
make migrate
```

Downgrade 本地 schema：

```bash
make downgrade
```

Integration tests 需要本地 PostgreSQL：

```bash
docker compose up -d postgres
make test-integration
```

`make test-integration` 會注入 local compose 使用的 `DATABASE_URL`。

## Symbol Seed

V0.5 沒有正式 symbol importer，需要手動把 minimal symbol master 灌入 DB。**第一次 setup（或新建 DB）之後跑一次即可**：

```bash
make seed
```

內容（依 BE-V0.5-04）：

- 4 筆 production 標的：`2330` 台積電、`2317` 鴻海、`0050` 元大台灣50、`00878` 國泰永續高股息
- 2 筆測試 fixture：`9999`（halted）、`8888`（unsupported），用來驗證 service / API 層對非 `tradable` 標的的拒絕行為

`seed_symbols` 使用 `INSERT ... ON CONFLICT DO UPDATE`，重複跑不會炸 unique constraint。

未跑 seed 的後果：`GET /symbols` 回空陣列、`POST /trade-intents` 對任何 symbol 都會 422 `UNKNOWN_SYMBOL`。

> 設計取捨：seed 採手動指令而非 migration 自動植入 / app 啟動自動 seed。原因見 BE-V0.5-04 §Seed 建議 line 40（三選一）；V1 production importer 預期會取代這份 seed，現在不把 seed 綁進 migration 或 boot 流程，避免後續抽換時牽動部署腳本。

## Admin 帳號建立與 2FA

全新資料庫沒有任何可登入的 admin（`POST /admin/users` 需要已登入的 admin，雞生蛋）。下列指令直接在 DB 建一個 active admin（argon2 雜湊密碼），admin 再自行登入並註冊 2FA。

### 1. 建立 admin 帳號

**本地快速建立（env 式，單一帳號）：** 讀 `.env` 的 `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD`（未設時 `LOCAL_MODE` 用預設 `admin@example.com` / `password123`）。

```bash
make bootstrap-admin
```

**逐一建立（建議，可重複執行、每次一個）：** email 用參數帶入，密碼自動產生強密碼並印出一次。同 email 已存在會拒絕，不會覆蓋既有帳號。

```bash
make create-admin EMAIL=dev@example.com
# 或：PYTHONPATH=src uv run python -m app.db.create_admin --email dev@example.com
```

若要指定自訂密碼，CLI **不收**密碼參數（會洩漏到 `ps`、shell history、docker 事件）。改成在終端機**互動輸入**：下面第一行的 `read -rsp` 會跳出提示讓你當場打密碼，存進當前 shell session 的暫時變數 `CREATE_ADMIN_PASSWORD`（**不需要、也不要寫進 `.env`**），再用 `-e` 只帶變數「名稱」傳進容器（值不出現在命令列），最後 `unset` 清掉。

> EC2 Ubuntu 預設 bash。`read` 那行**需單獨先貼、輸入密碼按 Enter 後**，再貼後面兩行——整段一起貼會讓 `read` 把 docker 那行當成密碼吃掉。

```bash
read -rsp "Admin 密碼：" CREATE_ADMIN_PASSWORD; echo   # 跳提示、當場輸入、不回顯、不進 history
export CREATE_ADMIN_PASSWORD
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  run --rm -e CREATE_ADMIN_PASSWORD app python -m app.db.create_admin --email dev@example.com
unset CREATE_ADMIN_PASSWORD
```

> 此步驟只是建好可登入的 admin 帳號（`mfa_enabled=false`）。該 admin **首次登入後仍須自行註冊 2FA**（見下方〈2. admin 自助註冊 2FA〉），否則 admin 端點會被擋（`403 MFA_REQUIRED`）。

**Production（在 EC2 上，對 compose 內的 DB 跑一次性容器，零停機）：**

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  run --rm app python -m app.db.create_admin --email dev@example.com
```

每位需要 admin 的成員各跑一次（換 `--email` 即可，不需改設定檔或重啟服務）。

### 2. admin 自助註冊 2FA

新建的 admin 為 `mfa_enabled=false`，**登入後仍會被 admin gate 擋住，直到完成 2FA 註冊**（`403 MFA_REQUIRED`）。production 一律要求 2FA。流程如下（`<BASE>` 為服務位址，本地為 `http://127.0.0.1:8100`）：

**① 登入取得 access token**

```bash
curl -X POST <BASE>/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"<建立時的密碼>"}'
# 回應含 { "accessToken": "...", "tokenType": "Bearer", "expiresIn": ... }
```

**② 申請 2FA 設定（帶上 access token）**

```bash
curl -X POST <BASE>/admin/2fa/setup \
  -H 'Authorization: Bearer <accessToken>'
# 回應 { "provisioningUri": "otpauth://totp/...", "secret": "<base32 secret>" }
```

**③ 把 secret 加進 authenticator app**（secret 只在此步回傳一次，請當場處理）

- 手動輸入：Google Authenticator / Authy →「新增帳號」→「輸入設定金鑰」→ 貼上 `secret`。
- 或掃 QR：QR 就是 `provisioningUri` 的圖像。有前端時由前端渲染；無前端可在本機產生（**勿貼到線上 QR 產生器，會外洩密鑰**）：
  ```bash
  qrencode -t ANSIUTF8 "otpauth://totp/...（貼上 provisioningUri）"
  ```

**④ 用 app 上的 6 位數碼驗證，升級 session**

```bash
curl -X POST <BASE>/admin/2fa/verify \
  -H 'Authorization: Bearer <accessToken>' \
  -H 'Content-Type: application/json' \
  -d '{"code":"123456"}'
# 回應含新的 accessToken（已帶 mfa_verified），之後 admin 端點請改用這個新 token
```

完成後該 admin 即可使用所有 admin 端點。之後每次重新登入都需再驗一次 2FA 碼（`/admin/2fa/verify`）以取得已驗證的 token。

> 安全：production 強制 admin 2FA（無 bypass）；`mfa_verified` 綁在登入 session、非帳號欄位。每位 admin 用各自帳號、各自裝置註冊，便於稽核與離職時停用單一帳號。

## 等價 uv 指令

```bash
uv sync
uv run uvicorn app.main:app --app-dir src --reload --port 8100
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run alembic upgrade head
uv run alembic downgrade base
PYTHONPATH=src uv run python -m app.db.seed
PYTHONPATH=src uv run python -m app.db.create_admin --email dev@example.com
uv run pytest
DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock uv run pytest -m integration
```

## 環境變數

**V0.5 本地模式無 auth，禁止公開部署。** 詳見上方說明。

- `APP_ENV`，預設 `local`
- `APP_NAME`，預設 `ai-stock-api`
- `APP_VERSION`，預設 `0.5.0`
- `DATABASE_URL`，本地範例見 `.env.example`
- `LOCAL_USER_ID`，必填 UUID，無預設值；`LOCAL_MODE=true` 時缺漏會在啟動時拋 `ValidationError`
- `LOCAL_MODE`，預設 `true`
- `REQUEST_ID_HEADER`，預設 `X-Request-Id`
