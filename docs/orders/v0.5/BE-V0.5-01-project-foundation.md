# BE-V0.5-01：本地專案基礎、Config、Health、Quality Scripts

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：15h
- 依賴：無
- 交付版本：V0.5

## 背景

V0.5 需要先建立可本地啟動的 FastAPI + PostgreSQL 後端骨架。這不是 throwaway demo，後續 V1 會沿用同一套專案結構、設定方式、request id、error envelope、測試與 quality scripts。

V0.5 不需要 production deployment，不需要 EC2/RDS 設定，不需要 auth/security stack，但專案基礎要避免後續重做。

## 目標

建立最小可運行後端：

- `uv` 管理 Python dependencies。
- FastAPI app 可啟動。
- PostgreSQL connectivity 可檢查。
- 統一 settings/config 載入。
- 每個 request 產生或保留 `X-Request-Id`。
- 統一 error envelope 基礎。
- `ruff`、format、test 指令可執行。

## 非目標

- 不做 auth、JWT、CSRF。
- 不做 Alembic schema，這由 BE-V0.5-02 負責。
- 不做 Docker production image。
- 不做 app container；V0.5 只允許 local PostgreSQL `docker-compose.yml` 作為開發依賴。
- 不做 EC2/RDS deployment。
- 不做 CI provider 設定，除非 repo 已有現成 CI。
- 不在 BE-V0.5-01 加入 `mypy` 或 `pyright`；待 ORM/domain model 穩定後再評估。

## 技術要求

### Database Driver

V0.5 採用 SQLAlchemy sync engine 與 `psycopg` 3 driver：

- SQLAlchemy URL 使用 `postgresql+psycopg://...`。
- BE-V0.5-01 只做 connectivity check，不建立 schema。
- async DB engine 與 `asyncpg` 暫不納入 V0.5；若 V1 出現高併發 DB I/O 需求再評估。

### Local PostgreSQL

BE-V0.5-01 建立 local-only `docker-compose.yml` 供開發啟動 PostgreSQL，不建立 app container。

- service：`postgres`
- image：`postgres:17`
- database/user/password：`ai_stock` / `ai_stock` / `ai_stock`
- port：`5432:5432`
- storage：named volume
- `.env.example` 使用 `DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock`

### Quality Scripts

BE-V0.5-01 建立 `Makefile` 作為本地開發與品質檢查的穩定入口，README 記錄等價 `uv` 指令。

- `make install` -> `uv sync`
- `make dev` -> `uv run uvicorn app.main:app --app-dir src --reload --port 8100`
- `make lint` -> `uv run ruff check .`
- `make format` -> `uv run ruff format .`
- `make format-check` -> `uv run ruff format --check .`
- `make test` -> `uv run pytest`
- `make test-integration` -> `uv run pytest -m integration`

BE-V0.5-01 只要求 `ruff` 與 `pytest`；typed Python baseline 暫不納入，避免在 SQLAlchemy models 尚未建立前過早固定型別策略。

### 建議目錄

```text
src/
  app/
    main.py
    api/
      errors.py
      deps.py
    core/
      config.py
      ids.py
    db/
      session.py
tests/
```

後續工單可在 `src/app/` 基礎上擴充 `domain/`、`commands/`、`adapters/`。

### Settings

使用 `pydantic-settings` 建立集中式 typed settings，支援 `.env` 載入與測試 override。

至少支援：

- `APP_ENV`，預設 `local`
- `APP_NAME`，預設 `ai-stock-api`
- `APP_VERSION`，預設 `0.5.0`
- `DATABASE_URL`，`.env.example` 提供本地 PostgreSQL 範例。
- `LOCAL_USER_ID`，預設 `local-user`
- `LOCAL_MODE`，預設 `true`
- `REQUEST_ID_HEADER`，預設 `X-Request-Id`

`LOCAL_MODE=true` 是 V0.5 本地模式。文件需標示不可公開部署。

V0.5 local mode 允許 app 在缺少 `DATABASE_URL` 時啟動；DB connectivity check 必須視為 unavailable，`GET /health` 回 `503 DATABASE_UNAVAILABLE` error envelope。Production deployment 不在 V0.5 範圍。

### Health API

`GET /health`

V0.5 只保留單一 `/health` endpoint，語意為 readiness，必須檢查 DB connectivity。暫不拆 `/live` / `/ready`；V1 deployment readiness 再評估拆分。

Response example：

```json
{
  "data": {
    "service": "ai-stock-api",
    "version": "0.5.0",
    "environment": "local",
    "database": "ok"
  }
}
```

若 DB 無法連線，回傳 503，且仍使用 error envelope。

### Request ID

- 若 request 帶 `X-Request-Id`，保留該值。
- 若未帶，產生 UUID。
- Response header 回傳 `X-Request-Id`。
- Error envelope 內包含 `requestId`。
- Header 名稱由 `REQUEST_ID_HEADER` 設定決定，預設 `X-Request-Id`。
- Client 傳入 request id 時，長度必須為 1..128。空字串視為未帶；超過 128 回 `400 VALIDATION_ERROR`。

### Error Envelope

所有 API error 使用：

```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "發生未預期錯誤",
    "details": {},
    "requestId": "..."
  }
}
```

V0.5 先支援：

- `INTERNAL_ERROR`：`發生未預期錯誤`
- `VALIDATION_ERROR`：`請求資料不合法`
- `DATABASE_UNAVAILABLE`：`資料庫暫時無法使用`

`code` 是前端邏輯判斷的穩定契約；`message` 是中文 user-facing 顯示文字。

未處理 exception 不得在 response `details` 暴露內部錯誤內容：

- Response 使用 `INTERNAL_ERROR` 與 `details: {}`。
- Server log 記錄 exception stack、path、method、request id。
- 測試只驗證 envelope 形狀與 request id，不依賴內部 exception 訊息。

後續工單會加入 domain error codes。

## 驗收條件

- [ ] `uv` 可安裝 dependencies 並啟動 FastAPI app。
- [ ] `GET /health` 在 DB 正常時回傳 200。
- [ ] DB 斷線時 `GET /health` 回傳 503 與 error envelope。
- [ ] 每個 response 都帶 `X-Request-Id`。
- [ ] 未處理 exception 會轉成統一 error envelope。
- [ ] `ruff check`、`ruff format --check`、`pytest` 可執行。
- [ ] README 或 docs 記錄本地啟動指令與必要環境變數。

## 測試要求

- API test：`GET /health` success。
- API test：帶入 `X-Request-Id` 時 response 保留。
- API test：未帶 request id 時 response 自動產生。
- Unit/API test：error handler 回傳 `requestId`。
- `make test` 預設跑不需要 Docker 的快速 unit/API tests，可 mock DB ping。
- 建立 `integration` pytest marker 與 `make test-integration`，要求 local PostgreSQL 已啟動，打真 DB 驗證 `GET /health` success。
- README 記錄 `docker compose up -d postgres` 後執行 integration tests。

## 工程注意事項

- 不要在這張工單引入業務 tables。
- 不要將 `LOCAL_MODE` 做成 security bypass 到處散落；先集中在 config/context 層。
- 不要使用 print 作為正式 logging；可先建立 Python logging 基礎。
