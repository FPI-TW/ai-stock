# BE-V1-01：正式 Auth、Sessions、CSRF、Invitation、Password Reset

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：35h
- 依賴：BE-V0.5-03
- 交付版本：V1

## 背景

V0.5 使用 `LocalUserContext` placeholder（BE-V0.5-03），所有 API 透過 `api/deps.py::get_current_user` 取一個固定 `local-user`。V1 必須以正式身份系統取代，符合 `docs/domain-spec.md` §13 的 session / refresh token rotation、CSRF、password reset、invitation 規格。

V0.5 已經建立的不變式必須延續：

- API 不接受 client 傳入 `owner_user_id`，永遠從 context 取得。
- `trade_intents`、`trigger_events`、`notifications` 已有 `owner_user_id`，schema 不需改動，僅 context source 換。
- Error envelope、request id、`make check` 品質門檻沿用。

V1 不開放公開註冊；帳號由 admin（BE-V1-13）建立並寄 invitation。本工單只實作 auth flow 與 placeholder admin endpoint stub，正式 admin user CRUD 留 BE-V1-13。

## 目標

- 新增 `users`、`refresh_tokens`、`invitations`、`password_resets`、`csrf_tokens`（或同等機制）DB tables。
- 短效 access JWT（15 分鐘）+ DB 持久化 refresh token rotation。
- Invitation 建立、寄送、消費（首次設定密碼）。
- Password reset request、token 驗證、密碼更新。
- Login / logout / refresh / password reset 全部寫 audit placeholder（BE-V1-16 補正式 audit log table，本工單只保留 stub 介面）。
- CSRF 防護：所有 state-changing requests 需驗 CSRF token，refresh 透過 HttpOnly cookie。
- 取代 `api/deps.py::get_current_user` 的 `LocalUserContext`，改為從 auth context 解析。
- Rate limit：登入失敗鎖定、password reset、invitation 重寄。**本工單把 `RateLimiter` token-bucket primitive 提前自 BE-V1-16 建立**（見下方 DB Schema `rate_limit_buckets` 與工程注意事項），但只接 auth 自己的 buckets；其餘 mutating endpoint 的 buckets 與 audit/idempotency/retention 仍由 BE-V1-16 負責。
- 確保 `LOCAL_MODE=true` 仍能跑 dev console，但 dev local auth 必須改走「測試 user」帳號（不再 hardcode `local-user`）。

## 非目標

- 不做 admin user CRUD（BE-V1-13）。
- 不做 audit log table（BE-V1-16），本工單僅以 logging.info / structured log 暫代。
- 不做 Telegram 通知（BE-V1-11）；password reset 與 invitation email 透過抽象 mailer interface，V1 可先 inject SMTP placeholder。
- 不做 2FA UI / 正式流程（BE-V1-13 admin 2FA）；user 端 V1 不啟用 2FA，但 schema 預留 `mfa_enabled` 欄位。
- 不做 OAuth 第三方登入。

## DB Schema

### `users`

- `id uuid primary key`
- `email citext not null unique`
- `password_hash text null`（invited 未啟用前為 null）
- `role text not null check (role in ('user', 'admin'))`
- `status text not null check (status in ('invited', 'active', 'disabled'))`
- `mfa_enabled boolean not null default false`
- `mfa_secret_encrypted bytea null`（V1 user 不開，預留）
- `terms_version_accepted text null`
- `terms_accepted_at timestamptz null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`
- `disabled_at timestamptz null`

Indexes：`email unique`、`(status, role)`。

### `invitations`

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `token_hash text not null unique`
- `expires_at timestamptz not null`
- `consumed_at timestamptz null`
- `revoked_at timestamptz null`
- `created_by_admin_id uuid not null references users(id)`
- `created_at timestamptz not null`

Rule：同一 user 只能存在一筆 `consumed_at is null and revoked_at is null and expires_at > now()` 的 invitation；admin 重寄時舊 invitation `revoked_at = now()`。

### `password_resets`

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `token_hash text not null unique`
- `expires_at timestamptz not null`
- `consumed_at timestamptz null`
- `requested_ip inet null`
- `created_at timestamptz not null`

### `refresh_tokens`

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `token_hash text not null unique`
- `parent_token_id uuid null references refresh_tokens(id)`（rotation chain）
- `issued_at timestamptz not null`
- `expires_at timestamptz not null`
- `revoked_at timestamptz null`
- `revoked_reason text null`（`rotated` | `logout` | `password_reset` | `reuse_detected` | `account_disabled`）
- `user_agent text null`
- `ip inet null`

Indexes：`(user_id, revoked_at)`、`token_hash unique`。

Refresh token reuse 偵測：若一筆 `token_hash` 被消費且 `revoked_reason = rotated` 後再被使用，整條 user 的 refresh chain 全部 revoke（`reuse_detected`）並產生 admin alert（透過 structured log，BE-V1-15 接收）。

### `rate_limit_buckets`（primitive 提前自 BE-V1-16）

簡單 DB-backed token bucket（schema 與 BE-V1-16 一致，由本工單建立、BE-V1-16 沿用勿重建）：

- `bucket_key text primary key`
- `tokens double precision not null`
- `last_refill_at timestamptz not null`

`app/services/rate_limit/limiter.py`：

```python
class RateLimiter:
    def consume(self, *, bucket_key: str, capacity: int, refill_per_second: float, cost: int = 1) -> bool: ...
```

- 採 token bucket（`refill_per_second`），天然無固定窗邊界問題，滿足「time-window 不能跨界算錯」。
- **本工單只接 auth buckets**：`login:email:<email>`（capacity 5 / 5 per 15min）、`login:ip:<ip>`（30 / 15min）、`password_reset:email:<email>`（3 / 1h）、`password_reset:ip:<ip>`（10 / 1h）、invitation/bind 重寄。
- 其餘 mutating endpoint buckets（`create_intent`、`csv_confirm`、`webhook:telegram` …）+ env override + `Retry-After` 由 BE-V1-16 擴充。
- DB-backed 為 V1 簡化版（PostgreSQL row lock + update）；Redis swap 延到 BE-V1-17 後評估。

### `trade_intents` / `trigger_events` / `notifications` 既有欄位

- 不改 schema。
- `owner_user_id` FK 變成指向 `users(id)`，需要 migration 寫 FK 約束（BE-V0.5-02 原本沒加 FK，預設 owner_user_id 是任意 uuid）。
- Local user 資料 migration：以 `LOCAL_USER_ID` 為 email seed 一筆 `users` row（status=active），讓既有 v0.5 資料能對齊；migration 必須 idempotent。

## Auth Token 設計

### Access JWT

- 演算法：`HS256` 或 `EdDSA`；V1 採 `HS256` + 32-byte secret，env `JWT_ACCESS_SECRET`。
- TTL：user 15 分鐘，admin 5 分鐘。
- Claims：`sub`（user id）、`role`、`session_id`、`iat`、`exp`、`mfa_verified`（admin only）。
- 不放敏感資訊，不放 email。

### Refresh Token

- 32-byte random，存 base64url。
- DB 只存 `sha256(token)` 為 `token_hash`，不存明文。
- TTL：user 30 天，admin 12 小時。
- Cookie：`HttpOnly`、`Secure`（local mode 例外）、`SameSite=Lax`、path `/auth/refresh`。
- 每次 refresh rotation：舊 token `revoked_reason = rotated`，新 token `parent_token_id` 指向舊 token。

### CSRF

- Cookie 流程同時需 CSRF token。
- 採 double-submit cookie：發出 `csrf_token` cookie（non-HttpOnly）與要求 client 在 state-changing requests 帶 `X-CSRF-Token` header，後端比對相同值。
- GET 不需 CSRF；POST/PATCH/DELETE 必須。
- Backend 同時檢查 `Origin` / `Referer` 對齊允許清單。

## API

### `POST /auth/invitations/accept`

Body：
```json
{ "token": "...", "password": "...", "termsVersion": "v1-2026-01-01" }
```

Rules：

- 驗 token：`token_hash` 存在、未 consumed、未 revoked、未過期。
- 套用密碼規則（≥8 chars，無大小寫/數字/符號強制；後續再補強）。
- 寫 `password_hash`（argon2id）、`status = active`、`terms_*` 欄位。
- `consumed_at = now()`。
- 同 transaction 建立第一筆 refresh token，回 access token + CSRF token。
- Audit stub：`account_activated`。

Errors：`INVITATION_INVALID`、`INVITATION_EXPIRED`、`INVITATION_CONSUMED`、`WEAK_PASSWORD`。

### `POST /auth/login`

Body：`{ "email": "...", "password": "..." }`

Rules：

- 同 email 連續 5 次失敗鎖定 15 分鐘（rate limit by email + IP）。
- 成功：回 access token、set refresh cookie、CSRF cookie。
- 錯誤訊息一律不洩漏 email 是否存在（`LOGIN_FAILED`）。
- 未 active 帳號（invited / disabled）一律回 `LOGIN_FAILED`。
- Audit stub：`login_success` / `login_failed`。

### `POST /auth/refresh`

- 從 HttpOnly cookie 取 refresh token；驗 CSRF。
- Rotation：revoke 舊 token、issue 新 token。
- Reuse detection：若舊 token 已 `revoked_reason = rotated` 再被使用，revoke 整條 chain。
- 回新 access token + 新 refresh cookie。

Errors：`REFRESH_INVALID`、`REFRESH_REUSE_DETECTED`。

### `POST /auth/logout`

- Revoke 當前 refresh token（`revoked_reason = logout`）。
- Clear cookies。
- 不錯誤 fail：即使無 token 也回 204。

### `POST /auth/password-reset/request`

Body：`{ "email": "..." }`

Rules：

- 不論 email 是否存在都回 202（不洩漏）。
- Rate limit：同 email 每小時最多 3 次，同 IP 每小時最多 10 次。
- 若 email active 才產生 token，token TTL 30 分鐘。
- 寄信由抽象 `Mailer` interface 處理（V1 stub 可寫 log）。

### `POST /auth/password-reset/confirm`

Body：`{ "token": "...", "newPassword": "..." }`

Rules：

- 驗 token 同 invitation 規則。
- 成功後 revoke 該 user 所有 refresh token（`revoked_reason = password_reset`）。
- Audit stub：`password_reset_completed`。

### `GET /auth/me`

- 回當前 user：`id`、`email`、`role`、`status`、`mfa_enabled`。
- 主要供前端初始化使用。

## 取代 V0.5 Placeholder

- `api/deps.py::get_current_user`：原本回 `LocalUserContext`，改為從 access token 解析。
- `LOCAL_MODE=true` 時保留 dev console 入口，但需要一個 `dev` user record（migration seed），不再以 `LOCAL_USER_ID` env 直接造假 context。
- `commands/trade_intent.py` 等已使用 `current_user.id` 的呼叫端不需改 signature。
- 既有 v0.5 integration tests 改用 `auth_client` fixture 取代 `local_client`。

## Configuration

新增 env：

- `JWT_ACCESS_SECRET`：32-byte secret，缺失 fail-fast（production）。
- `JWT_ACCESS_TTL_SECONDS`：user 預設 900，admin 預設 300。
- `REFRESH_TOKEN_TTL_DAYS`：user 預設 30，admin 預設 0.5。
- `PASSWORD_HASH_PEPPER`：選用，argon2id 額外 pepper。
- `AUTH_COOKIE_SECURE`：預設 true；local mode 可 false。
- `AUTH_COOKIE_DOMAIN`：選用。
- `LOGIN_RATE_LIMIT_PER_EMAIL`：預設 5 / 15min。
- `LOGIN_RATE_LIMIT_PER_IP`：預設 30 / 15min。

`LOCAL_MODE=true` 時：

- Refresh cookie `Secure=false` 允許 http localhost。
- `JWT_ACCESS_SECRET` 可預設值，但啟動 log 必須警告。

## Error Codes

新增：

- `LOGIN_FAILED`：401。
- `LOGIN_LOCKED`：429。`login:email` 失敗鎖定（連續密碼錯誤）專用——使用者有感的帳號層鎖定。
- `INVITATION_INVALID`：400。
- `INVITATION_EXPIRED`：410。
- `INVITATION_CONSUMED`：409。
- `WEAK_PASSWORD`：422。
- `REFRESH_INVALID`：401。
- `REFRESH_REUSE_DETECTED`：401（同時觸發 admin alert log）。
- `CSRF_FAILED`：403。
- `RATE_LIMITED`：429。純流量節流（`login:ip` abuse、password reset、其餘 endpoint）；與 `LOGIN_LOCKED` 分流。
- `UNAUTHENTICATED`：401。
- 既有 `FORBIDDEN`：403（保留給 BE-V1-02 authorization）。

`message` 中文 user-facing：例如 `登入失敗，請確認帳號密碼` / `連續登入失敗次數過多，請稍後再試`。

## 驗收條件

- [ ] `users` / `invitations` / `password_resets` / `refresh_tokens` migration 可 upgrade / downgrade。
- [ ] V0.5 既有 `local-user` 資料透過 migration 套用為 `users` row，integration tests 仍可跑通。
- [ ] `POST /auth/login` 在密碼正確時回 access token + set HttpOnly cookie + CSRF cookie。
- [ ] 連續 5 次失敗回 `LOGIN_LOCKED`，下次成功後 counter reset。
- [ ] `POST /auth/refresh` rotation 後舊 token 再使用會觸發整條 chain revoke 並 log warning。
- [ ] `POST /auth/logout` clear cookies 並 revoke refresh。
- [ ] `POST /auth/invitations/accept` 消費 token 後第二次呼叫回 `INVITATION_CONSUMED`。
- [ ] Password reset request 不論 email 是否存在都回 202。
- [ ] Password reset confirm 成功會 revoke 該 user 所有 refresh token。
- [ ] 既有 V0.5 routes（`/trade-intents`、`/notifications`、`/symbols`）改為 auth required，未帶 token 回 `UNAUTHENTICATED`。
- [ ] `GET /health` / `POST /auth/*` 不需 auth。
- [ ] CSRF 缺失 / 不匹配時 state-changing requests 回 `CSRF_FAILED`。

## 測試要求

- Unit：JWT encode / decode + claim validation。
- Unit：argon2id hash 與 verify。
- Unit：refresh token rotation chain 與 reuse detection。
- Unit：rate limit counter 行為（time-window 不能跨界算錯）。
- Integration：invitation accept → login → refresh → logout 全鏈路。
- Integration：password reset → 舊 refresh token 失效。
- Integration：login 失敗 5 次 → 第 6 次 lock。
- Integration：refresh reuse → 整條 chain revoke。
- Integration：cross-user query forbidden（搭配 BE-V1-02 補完）。
- Integration：CSRF token 缺失 / 對不上 → 403。
- Integration：V0.5 既有 integration tests 全綠（migration 後）。

## 工程注意事項

- argon2id 用 `argon2-cffi`，參數 `time_cost=3`, `memory_cost=65536`, `parallelism=4`（可由 env override）。
- 不可在 log 印明文 token、明文密碼、refresh token；只能印 token id。
- 不要在 access JWT 放 email 或 role 以外的 PII；前端要取 profile 應透過 `/auth/me`。
- Refresh cookie path `/auth/refresh`，避免在其他 API 被瀏覽器自動帶上。
- `LOCAL_MODE=true` 下仍須走完整 auth flow，不可短路 `get_current_user`；只允許 cookie secure 放寬。
- `users.email` 用 `citext`（PostgreSQL extension），migration 需 enable。
- Mailer interface：`class Mailer(Protocol): def send(self, message: EmailMessage) -> None`。V1 stub 印 log，BE-V1-11 / R3 再接 SES / SMTP。
- 所有 state mutation 都要透過 command handler，controller 只 transport validation。
- BE-V1-02 接手 role/owner-scope authorization，本工單只在 `deps.py` 提供 `current_user` 與 `require_role('admin')` 的最小骨架。
- **RateLimiter 邊界（與 BE-V1-16 協調）**：`rate_limit_buckets` table 與 `RateLimiter.consume()` primitive **由本工單建立**，schema/signature 對齊 BE-V1-16；本工單只接 auth buckets。BE-V1-16 直接沿用此 primitive、勿重建/勿另開表，只負責擴充其餘 endpoint buckets 與 audit/idempotency/retention。**錯誤碼分流（已定案）**：`login:email` 失敗鎖定回 `LOGIN_LOCKED`；`login:ip` abuse 與其餘節流回 `RATE_LIMITED`；兩者皆 429 + `Retry-After`，差別在 `code` 供前端決定訊息。BE-V1-16 沿用同一分流。
