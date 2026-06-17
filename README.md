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
