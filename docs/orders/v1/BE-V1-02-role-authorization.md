# BE-V1-02：正式 Role 與 Owner-Scope Authorization

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：20h
- 依賴：BE-V1-01, BE-V0.5-07
- 交付版本：V1

## 背景

BE-V1-01 取代了 V0.5 的 `LocalUserContext`，但 role-based access control 與 owner-scope check 仍未集中。V0.5 因為只有一個 user，repository 多以 `owner_user_id = current_user.id` 隱式 filter。V1 需要：

- 明確切分 `user` / `admin` role。
- 確保 user-facing API 永遠以 auth context 的 `user_id` filter，不接受 client 傳入 `owner_user_id`（v0.5 已守，需測試覆蓋）。
- 確保 admin API 與 user API 分開路徑，admin 操作另寫 audit reason。
- 防止 cross-user IDOR：直接以 id 取 trade intent / notification 時，必須 enforce ownership。

`docs/domain-spec.md` §13 規定：V1 先只 `user` / `admin`，不細分 `system_admin` / `support_admin`，但權限檢查必須集中。

## 目標

- 提供統一 authorization decorator / dependency：`require_role`、`require_self_or_admin`、`require_owner`。
- 建立 `OwnerScope` 抽象：repository / query layer 都從 `OwnerScope.user_id` filter，避免散落。
- Refactor V0.5 既有 routes 改用統一 decorator，移除散落的 `owner_user_id = current_user.id` 重複。
- 補 cross-user forbidden tests。
- Admin sentinel route：例如 `GET /admin/ping`，僅 `admin` role 可進；本工單只放骨架，正式 admin endpoints 由 BE-V1-13 / 14 / 15 補。
- Permission failure 統一回 `FORBIDDEN` 403；未認證仍由 BE-V1-01 的 `UNAUTHENTICATED` 401 處理。

## 非目標

- 不做正式 admin endpoints（BE-V1-13/14/15）。
- 不做 admin 2FA gate（BE-V1-13 補）。
- 不做 fine-grained permission（如 `notifications.read.own`）；V1 用 role + ownership 兩層即可。
- 不做 audit log（BE-V1-16），本工單仍以 structured log 暫代 admin 操作。

## 設計

### Role hierarchy

- `user`：可操作自己的資源。
- `admin`：可進 admin endpoints；對於 user 資源 read-only（V1 不允許 admin 代建/代改 trade intent，見 domain-spec §13）。
- 未來擴充：role 仍保留 text，避免 enum migration。

### Owner scope

新增 `app/core/authz.py`：

```python
@dataclass(frozen=True)
class OwnerScope:
    user_id: UUID

@dataclass(frozen=True)
class AdminContext:
    admin_id: UUID
```

Repository 接受 `OwnerScope` 參數，回傳資料一律已被 `owner_user_id = scope.user_id` filter。舊 code 直接傳 `current_user.id` 的呼叫端改為傳 `OwnerScope`。

### Decorators / dependencies

`app/api/deps.py`：

```python
def require_user() -> AuthContext: ...
def require_admin() -> AdminContext: ...
def require_owner(resource_loader) -> AuthContext: ...
```

`require_owner` 用於需要載入 resource 再驗 owner 的 endpoint：

```python
@router.post("/trade-intents/{intent_id}/cancel")
def cancel_intent(
    intent_id: UUID,
    ctx: AuthContext = Depends(require_user),
    intent: TradeIntent = Depends(load_owned_intent),
):
    ...
```

`load_owned_intent` 透過 path param 載入 + 比對 owner，不符合直接 403（不分 404）。

> 一致性：對 owner 不符的 GET / mutation 一律回 `FORBIDDEN`，不回 404，避免 enumerate 帳號資料。

### Admin sentinel

`app/api/routes/admin/__init__.py` 建立 router，prefix `/admin`，dependency `require_admin`。本工單只放：

- `GET /admin/ping`：回 `{ "data": { "ok": true } }`，便於前端驗證 admin login。

正式 endpoints 由 BE-V1-13 / 14 / 15 補。

## API

### 既有 V0.5 routes 變動

| Route                                    | 變動                                                |
| ---------------------------------------- | --------------------------------------------------- |
| `POST /trade-intents`                    | `Depends(require_user)`，owner 從 ctx 取            |
| `GET /trade-intents` / `/{id}`           | `Depends(require_user)`，repository 帶 `OwnerScope` |
| `POST /trade-intents/{id}/cancel`        | `Depends(load_owned_intent)`                        |
| `GET /symbols`                           | `Depends(require_user)`（symbol master 對 user 暴露） |
| `POST /dev/evaluate-quotes`              | `LOCAL_MODE` only + `Depends(require_admin)`        |
| `GET /notifications` / `/{id}/read`      | `Depends(require_user)`                             |

### 新增 admin sentinel

- `GET /admin/ping`：`Depends(require_admin)`。

## Errors

不新增 error code。`FORBIDDEN` 403 已在 V0.5 定義。

`message`（中文）：

- `FORBIDDEN`：`沒有權限執行此操作`。

## 驗收條件

- [ ] `require_user` / `require_admin` / `require_owner` 各自有單獨 tests 覆蓋成功 + 失敗。
- [ ] 既有 V0.5 routes 改用統一 decorator，code review 無散落 `owner_user_id = current_user.id`。
- [ ] Repository / query layer 接受 `OwnerScope`，呼叫端不可直接傳 raw `user_id`（用 type alias 強制）。
- [ ] Cross-user GET / mutation 對不屬於自己的 resource 一律 403。
- [ ] Admin user 操作 user-facing route 時，視為一般 user（domain-spec §13：admin 不可代建 / 代取消）。
- [ ] `GET /admin/ping` 對 `user` role 回 403，對 `admin` role 回 200。
- [ ] `POST /dev/evaluate-quotes` 在 non-local mode 直接拒絕。

## 測試要求

- Integration：user A 取 user B 的 intent → 403。
- Integration：user A cancel user B 的 intent → 403，DB 不變。
- Integration：user 嘗試呼叫 `/admin/*` → 403。
- Integration：admin 嘗試 `POST /trade-intents` → 仍會建立，但 owner 是自己（不能代建他人）。
- Integration：admin 取消他人 intent → 403（V1 不允許）。
- Unit：`load_owned_intent` 對未存在 / 他人 / 自己各回 403 / 403 / object。
- Unit：repository query 缺少 `OwnerScope` 時 type checker 阻擋（mypy）。

## 工程注意事項

- 不要在多處重複寫 `if intent.owner_user_id != current_user.id: raise 403`；統一進 `load_owned_intent`。
- Cross-user 失敗回 403 而非 404 是經過討論的取捨（一致性 > 模糊化）；測試文件需註記原因，未來政策若改需同步改測試。
- `OwnerScope` 用 frozen dataclass，不要用 raw UUID，避免呼叫端不小心傳錯型別。
- Admin 對 user 資源的 read 能力（如 BE-V1-13 將實作）必須另外 `require_admin_read` + audit reason，不能讓 `require_admin` 自動穿透 owner scope。
- 既有 V0.5 tests 改 fixture 後若大量改寫，建議在 conftest 增加 `auth_client(role='user')` helper，避免 tests 散落。
