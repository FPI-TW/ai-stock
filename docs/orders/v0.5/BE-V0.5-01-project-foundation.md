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
- 不做 EC2/RDS deployment。
- 不做 CI provider 設定，除非 repo 已有現成 CI。

## 技術要求

### 建議目錄

```text
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

後續工單可在此基礎上擴充 `domain/`、`commands/`、`adapters/`。

### Settings

至少支援：

- `APP_ENV`
- `APP_NAME`
- `APP_VERSION`
- `DATABASE_URL`
- `LOCAL_USER_ID`
- `LOCAL_MODE`
- `REQUEST_ID_HEADER`

`LOCAL_MODE=true` 是 V0.5 本地模式。文件需標示不可公開部署。

### Health API

`GET /health`

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

- `INTERNAL_ERROR`
- `VALIDATION_ERROR`
- `DATABASE_UNAVAILABLE`

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

## 工程注意事項

- 不要在這張工單引入業務 tables。
- 不要將 `LOCAL_MODE` 做成 security bypass 到處散落；先集中在 config/context 層。
- 不要使用 print 作為正式 logging；可先建立 Python logging 基礎。
