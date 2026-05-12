# BE-V0.5-03：Local User Context 與 Owner Scope Placeholder

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：8h
- 依賴：BE-V0.5-02
- 交付版本：V0.5

## 背景

V0.5 不做 auth，但後續 V1 會有正式 owner scope。為避免 API 設計之後重做，V0.5 仍必須從 request context 取得 `owner_user_id`，禁止 client 在 payload 中傳 owner。

## 目標

建立 local user context provider：

- 本地模式固定回傳一個 `owner_user_id`。
- API dependencies 透過同一介面取得 current user / owner。
- 後續 V1 可把 provider 替換成 auth-based provider。

## 非目標

- 不做 login。
- 不做 user table。
- 不做 JWT。
- 不做 refresh token。
- 不做 CSRF。
- 不做 role permission。

## 技術要求

### Config

新增或使用：

- `LOCAL_MODE=true`
- `LOCAL_USER_ID=<uuid>`

若 `LOCAL_MODE=true` 且 `LOCAL_USER_ID` 缺失，app 啟動應 fail fast。

### Context Interface

建議定義：

```python
class RequestUser:
    user_id: UUID
    role: Literal["local"]

def get_current_user(...) -> RequestUser:
    ...

def get_owner_user_id(user: RequestUser) -> UUID:
    ...
```

V1 可將 `role` 擴充為 `user | admin`。

### API 規則

- 所有 user-facing endpoints 必須依賴 `get_current_user`。
- Create/list/cancel/notification APIs 都使用 context owner。
- Request body 不得包含 `owner_user_id`。
- 若 body 出現 `owner_user_id`，應回 `VALIDATION_ERROR` 或忽略並測試確認不會生效。建議直接拒絕。

## 驗收條件

- [ ] Local mode 自動提供固定 `owner_user_id`。
- [ ] User-facing APIs 不接受 client payload 中的 owner id。
- [ ] Config 明確標示 local mode 不可公開部署。
- [ ] 後續 V1 auth context 可替換此 provider。

## 測試要求

- Unit test：`LOCAL_MODE=true` 時 current user 等於設定的 UUID。
- Unit test：缺 `LOCAL_USER_ID` 時 settings validation 失敗。
- API test：create payload 若傳 `owner_user_id` 被拒絕。
- API test：created resource owner 來自 context。

## 工程注意事項

- 不要把固定 owner id 直接散落在 command 或 repository。
- 不要在 domain service 中讀環境變數。
- Owner scope 是 application boundary concern，由 API/command 傳入 domain。

