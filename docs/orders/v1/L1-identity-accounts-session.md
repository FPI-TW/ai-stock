# L1：身分 / 帳號 / Session（含 admin 建帳號）

## Metadata

- 分層：**上線前（必要）**
- 優先序：P0（地基，全體依賴）
- ROM：**L**（大票，建議票內 P0/P1 分階段）
- 依賴：無（V0.5 既有 schema）
- 交付版本：V1
- 併自舊工單：BE-V1-01-auth-sessions、BE-V1-02-role-authorization、BE-V1-13-admin-user-management（帳號生命週期部分）

## 背景

V0.5 使用 `LocalUserContext` placeholder，所有 API 透過 `api/deps.py::get_current_user` 取一個固定 `local-user`（`src/app/core/security.py`）。要上線給真人用，必須以正式身份系統取代，對齊 `docs/domain-spec.md` §13（使用者 / 角色 / 權限）、§16（owner scope）與 §20（條款接受版本）。

本票是**整個身分層**：帳號生命週期（admin 建帳號 → invitation → 首登設密 → active → 停用）、login/refresh/logout、password reset、role 授權、真 owner scoping、admin 2FA。把舊本拆散的 auth / role / admin-user-CRUD 合一，避免跨票邊界。

V0.5 已建立、必須延續的不變式：

- API 不接受 client 傳入 `owner_user_id`，永遠從 auth context 取得。
- `trade_intents` / `trigger_events` / `notifications` 已有 `owner_user_id`，schema 不動，只換 context source。
- Error envelope、request id、`make check` 品質門檻沿用。

## 目標

- DB：`users`、`refresh_tokens`、`invitations`、`password_resets` tables（CSRF 採無狀態 double-submit，不建表）。
- 帳號生命週期：admin 建帳號（填 email + role）→ status `invited` → 寄 invitation → 首登設密 → `active`；admin 停用 → `disabled` + cascade（見下）。
- 短效 access JWT（user 15min / admin 5min）+ DB 持久化 refresh token rotation + reuse 偵測。
- Password reset：request（不洩漏 email 存在）/ confirm（套密碼規則、revoke 既有 session）。
- Role 授權：`user` / `admin` 兩種；權限檢查**集中**在 `deps.py`（`require_role('admin')`），不散落 controller。
- 真 owner scoping：取代 `LocalUserContext`，全 user-facing API 從 token 取 user id；cross-user access forbidden。
- Admin TOTP 2FA：admin 首次啟用後必須設 2FA 才能進 admin 功能；2FA reset 需另一 admin 或離線流程。
- Audit：login / logout / refresh / invitation / password reset / account 建停用 全寫 audit；**本票直接建 `audit_events` 真表 + `AuditEventWriter`**，一開始就寫真稽核（後續票 L2/P1… 寫入同一張既有表，無 stub、無 refactor）。
- Rate limit：登入失敗鎖定、password reset、invitation 重寄。**RateLimiter token-bucket primitive 在本票建立**（`rate_limit_buckets` 表 + `RateLimiter.consume(bucket_key, capacity, refill_per_second, cost)`），L2 繼承擴充到所有 mutating endpoint。

## 非目標

- 不做跨 endpoint 通用限流（L2）；本票只接 auth 自己的 buckets（login / password_reset / invitation_resend）。`audit_events` 真表本票就建，L2 沿用同表寫自身事件。
- 不做 admin 監控 dashboard / 資料覆寫 / kill switch（P5）；本票 admin 只做「帳號 CRUD + 停用 cascade」。
- 不做 Telegram（P3）；invitation / reset 寄信走抽象 `Mailer` interface，V1 可注入 SMTP/log stub。
- 不做 user 端 2FA（schema 預留 `mfa_enabled`）；不做 OAuth 第三方登入。
- 不做通知通道設定（P3）。

## DB Schema

### `users`

- `id uuid pk`
- `email citext not null unique`（需 enable citext extension）
- `password_hash text null`（invited 未啟用前為 null）
- `role text not null check (role in ('user','admin'))`
- `status text not null check (status in ('invited','active','disabled'))`
- `mfa_enabled boolean not null default false`
- `mfa_secret_encrypted bytea null`（user 不開，admin 用）
- `terms_version_accepted text null` / `terms_accepted_at timestamptz null`
- `created_at` / `updated_at` / `disabled_at timestamptz null`

Indexes：`email unique`、`(status, role)`。

### `invitations`

- `id uuid pk`、`user_id fk users(id)`、`token_hash text unique`、`expires_at`（24h）、`consumed_at null`、`revoked_at null`、`created_by_admin_id fk users(id)`、`created_at`
- Rule：同 user 只能有一筆有效（未 consumed / 未 revoked / 未過期）invitation；admin 重寄時舊的 `revoked_at = now()`。

### `password_resets`

- `id uuid pk`、`user_id fk`、`token_hash text unique`、`expires_at`（30min）、`consumed_at null`、`requested_ip inet null`、`created_at`

### `refresh_tokens`

- `id uuid pk`、`user_id fk`、`token_hash text unique`、`parent_token_id null`（rotation chain）、`issued_at`、`expires_at`、`revoked_at null`、`revoked_reason text null`（`rotated`|`logout`|`password_reset`|`reuse_detected`|`account_disabled`|`2fa_reset`）、`user_agent null`、`ip null`
- Indexes：`(user_id, revoked_at)`、`token_hash unique`。
- Reuse 偵測：已 `rotated` 的 token_hash 再被使用 → revoke 整條 user chain（`reuse_detected`）+ admin alert（structured log，P5 接收）。

### `rate_limit_buckets`（RateLimiter primitive）

- `bucket_key text pk`、`tokens numeric`、`updated_at timestamptz`
- `RateLimiter.consume(bucket_key, capacity, refill_per_second, cost) -> bool`：PG row-lock token bucket（無固定窗跨界問題）。本票只接 auth buckets。

### `audit_events`（稽核真表，本票建立）

- `id uuid pk`、`event_type text not null`、`actor_type text check (in 'user','system','admin')`、`actor_id uuid null`、`occurred_at timestamptz not null`、`metadata jsonb`、`request_id text null`
- Indexes：`(event_type, occurred_at)`、`(actor_id, occurred_at)`
- 對齊 domain-spec §17 五欄位。本票寫入 auth 相關事件：`account_invited`/`account_activated`/`account_disabled`/`invitation_resent`/`login_success`/`login_failed`/`password_reset_completed`/`refresh_reuse_detected`/`admin_2fa_enabled`/`admin_2fa_reset`。其餘 §17 事件（intent_*、notification_*、kill_switch_* 等）由 L2/P1/P2 等票寫入**同一張既有表**，不重建、不 refactor。

### 既有表 owner FK

- `trade_intents` / `trigger_events` / `notifications` schema 不改，`owner_user_id` 加 FK 指向 `users(id)`。
- Local user 資料 migration：以 `LOCAL_USER_ID` seed 一筆 `users`（status=active），idempotent，讓 V0.5 資料對齊。

## Auth Token 設計

- **Access JWT**：`HS256` + 32-byte secret（env `JWT_ACCESS_SECRET`，production 缺失 fail-fast）；TTL user 15min / admin 5min；claims `sub`/`role`/`session_id`/`iat`/`exp`/`mfa_verified`(admin)；不放 email/PII。
- **Refresh**：32-byte random，DB 只存 `sha256`；TTL user 30d / admin 12h；cookie `HttpOnly`+`Secure`(local 例外)+`SameSite=Lax`，path `/auth/refresh`；每次 rotation 舊 token `rotated`、新 token `parent_token_id` 指舊。
- **CSRF**：double-submit cookie（`csrf_token` non-HttpOnly + `X-CSRF-Token` header 比對）；GET 免、POST/PATCH/DELETE 必須；後端另檢查 `Origin`/`Referer` 對允許清單。

## API（介面契約）

### 使用者 auth
- `POST /auth/invitations/accept` `{token, password, termsVersion}` → 驗 token、套密碼規則(≥8)、寫 hash(argon2id)+`status=active`+terms、建第一筆 refresh、回 access+CSRF。Audit `account_activated`。Errors：`INVITATION_INVALID`/`INVITATION_EXPIRED`/`INVITATION_CONSUMED`/`WEAK_PASSWORD`/`TERMS_NOT_ACCEPTED`。
- `POST /auth/login` `{email, password}` → 同 email 連 5 失敗鎖 15min（token-bucket，回 `LOGIN_LOCKED`）；成功回 access + set refresh/CSRF cookie；非 active 或密碼錯一律 `LOGIN_FAILED`（不洩漏存在）。Audit `login_success`/`login_failed`。
- `POST /auth/refresh` → cookie 取 refresh、驗 CSRF、rotation、reuse 偵測。Errors `REFRESH_INVALID`/`REFRESH_REUSE_DETECTED`。
- `POST /auth/logout` → revoke 當前 refresh（`logout`）、clear cookies、無 token 也回 204。
- `POST /auth/password-reset/request` `{email}` → 一律回 202；rate limit（email 3/h、IP 10/h）；active 才產 token（30min）；寄信走 `Mailer`。
- `POST /auth/password-reset/confirm` `{token, newPassword}` → 驗 token、套密碼規則、revoke 該 user 全 refresh（`password_reset`）。Audit `password_reset_completed`。
- `GET /auth/me` → 回 `id/email/role/status/mfa_enabled`。

### Admin 帳號管理（本票最小 admin）
- `POST /admin/users` `{email, role}`（require admin + mfa_verified）→ 建 `invited` user + invitation + 寄信。Audit `account_invited`。
- `POST /admin/users/{id}/resend-invitation` → 舊 invitation revoke、發新（rate limit）。Audit `invitation_resent`。
- `POST /admin/users/{id}/disable` → status `disabled` + **cascade**：取消該 user 所有 active/scheduled intents（status `cancelled_by_account_disabled`）、停用 pending notification deliveries、revoke 全 refresh（`account_disabled`）。Audit `account_disabled`。保留歷史與 audit。
- `GET /admin/users` → aggregate 列表（不含他人 intent 內容；查內容屬 P5 support 操作）。

### Admin 2FA
- `POST /admin/2fa/setup` / `POST /admin/2fa/verify`（TOTP）→ admin 首次啟用後強制；未設 2FA 不得進 admin 功能（`MFA_REQUIRED`）。2FA reset 需另一 admin 或離線流程，enable/disable/reset 全寫 audit。

## 取代 V0.5 Placeholder

- `api/deps.py::get_current_user`：改從 access token 解析；新增 `require_role('admin')` 與 `require_mfa`。
- `LOCAL_MODE=true` 仍走完整 auth flow（只放寬 cookie secure），用 migration seed 的 `dev` user，不再 hardcode `local-user`。
- 既有 integration tests 改用 `auth_client` fixture。

## 驗收條件

- [ ] 6 張 table（`users` / `refresh_tokens` / `invitations` / `password_resets` / `rate_limit_buckets` / `audit_events`）migration 可 upgrade/downgrade；citext extension 啟用。
- [ ] auth 事件（login/invitation/account 建停用/reset/reuse/2fa）寫入 `audit_events`，五欄位齊全。
- [ ] V0.5 `local-user` 資料 migration 為 `users` row，既有 integration tests 全綠。
- [ ] login 正確密碼回 access + cookies；連 5 失敗回 `LOGIN_LOCKED`，成功後 counter 回補。
- [ ] refresh rotation 後舊 token 再用 → 整條 chain revoke + warning。
- [ ] invitation 消費後二次呼叫回 `INVITATION_CONSUMED`；24h 過期回 `INVITATION_EXPIRED`。
- [ ] password reset request 不論 email 是否存在都回 202；confirm 成功 revoke 全 refresh。
- [ ] 既有 routes 改 auth required，未帶 token 回 `UNAUTHENTICATED`；`/health`、`/auth/*` 免 auth。
- [ ] CSRF 缺失/不符 → `CSRF_FAILED`。
- [ ] admin 建帳號 → 受邀者 accept → login 全鏈路通。
- [ ] admin 停用帳號 → 該 user intents 轉 `cancelled_by_account_disabled`、refresh 全失效、無法登入。
- [ ] cross-user：A 不能讀/改 B 的 intent。**實作回 `404 NOT_FOUND`（刻意偏離 spec 字面 403）**：邀請制、各人資料完全隔離、intent id 為不可猜 UUID，「不洩漏存在」比 403 更安全且與既有 `/notifications` 一致；安全屬性「A 無法存取 B」仍成立。
- [ ] admin 未設 2FA 不能進 admin endpoint（`MFA_REQUIRED`）。

## 測試要求

- Unit：JWT encode/decode + claim；argon2id hash/verify；refresh rotation chain + reuse；RateLimiter token-bucket（不跨界）；TOTP 驗證。
- Integration：invitation accept→login→refresh→logout 全鏈；password reset→舊 refresh 失效；login 5 失敗→鎖；reuse→chain revoke；admin 建/停用帳號 cascade；cross-user forbidden；CSRF 缺失→403；auth 事件落 `audit_events`；V0.5 既有 tests 全綠（migration 後）。

## 工程注意事項

- argon2id 用 `argon2-cffi`（`time_cost=3, memory_cost=65536, parallelism=4`，env override）。
- log 不可印明文 token / 密碼 / refresh，只印 token id。
- access JWT 不放 email/PII；前端取 profile 走 `/auth/me`。
- refresh cookie path `/auth/refresh`，避免其他 API 被瀏覽器自動帶。
- 所有 state mutation 走 command handler，controller 只 transport validation。
- 權限檢查集中 `deps.py`，為未來細分角色（system_admin/support_admin…）預留，不散落。
- `AuditEventWriter.write(event_type, actor_type, actor_id, metadata, request_id)`：直接寫 `audit_events` 表；後續票（L2/P1…）沿用同 signature 寫入既有表，不重建、不 refactor callers。
- `Mailer(Protocol).send(message)`：V1 stub 印 log，P3/R3 接 SES/SMTP。
