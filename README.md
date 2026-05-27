# AI Stock 後端

V0.5 交易意圖與到價提醒流程的本地 FastAPI 後端。

V0.5 僅支援本地模式。不要將這份設定公開部署。

## 環境需求

- Python 3.13
- uv
- Docker，用於本地 PostgreSQL integration tests
- Chromium（僅執行 UI smoke 測試時需要，一次性安裝：`uv run playwright install chromium`）

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
curl http://127.0.0.1:8000/health
```

`LOCAL_MODE=true`（預設）時另外掛了一組僅供開發的工具頁：

- `GET /test` — 內建 API 測試控制台（Dev / 使用者 / 服務端 三模式切換、推假行情、
  時鐘控制、跨使用者通知聚合）。詳見 [`docs/dev/test-page.md`](docs/dev/test-page.md)。
- `GET /test/flows` — 系統流程圖文件頁（Mermaid，可列印）。
- `POST /dev/*` — `/dev/push-quote`、`/dev/set-clock`、`/dev/evaluate-quotes`、
  `/dev/server-state`，皆為未授權的本地調試用端點。

`LOCAL_MODE=false` 時上述路徑一律回 404；切勿在非 loopback 介面上啟用 LOCAL_MODE。

## 品質指令

```bash
make lint
make format-check
make typecheck
make test
make test-ui          # 需 PostgreSQL + chromium
make test-integration # 需 PostgreSQL
```

`make check` 會執行不需要 PostgreSQL 的品質門檻：lint、format-check、typecheck、
unit/API tests；pytest 預設透過 `-m 'not integration and not ui'` 排除 integration
與 UI smoke。

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
- `pre-push` 會執行 `make pre-push-check`（單元/API 測試 + Playwright UI smoke）。
  測試失敗時 push 會被阻擋。
- UI smoke 需要本地 PostgreSQL 與 chromium；缺 prereq 的環境可用
  `SKIP=pytest-ui git push` 暫時略過。
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

等價 uv 指令：

```bash
uv sync
uv run uvicorn app.main:app --app-dir src --reload
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run alembic upgrade head
uv run alembic downgrade base
uv run pytest
DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock uv run pytest -m integration
uv run playwright install chromium
DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock uv run pytest -m ui
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
