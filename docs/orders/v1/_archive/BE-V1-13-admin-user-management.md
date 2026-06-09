# BE-V1-13：Admin User Management 與 Account Disable Command

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：35h
- 依賴：BE-V1-01, BE-V1-02, BE-V0.5-07
- 交付版本：V1

## 背景

V1 帳號由 admin 建立，user 不開放公開註冊（domain-spec §13）。本工單把 admin user CRUD 與帳號停用 cascade 落地。停用帳號要 cascade：

- 取消該 user 所有 active / scheduled intents → `cancelled_by_account_disabled`。
- Telegram binding revoked。
- Pending notification deliveries `skip_reason = skipped_account_disabled`。
- 保留歷史與 audit。

V1 admin 必須使用 TOTP 2FA：首次啟用 admin 帳號後須設定 2FA 才能進 admin 功能。Admin 2FA reset 需另一個 admin 或離線人工流程。

V1 admin 不可代建 / 代取消 user 的 intent。

## 目標

- 新增 admin user CRUD（建立、寄 invitation、重寄、停用、查看 aggregate）。
- 新增 admin 2FA setup / verify / reset 流程。
- 新增 `DisableUserAccount` command + cascade。
- 新增 admin user lookup（搜尋 + aggregate stats）。
- Admin 查看單一 user 詳細 intent / notification 必須帶 reason 並寫 audit（read-with-reason 路徑）。
- 強制 admin endpoint 必須通過 2FA verification gate。

## 非目標

- 不做 admin role 細分（domain-spec §13：V1 只保留 `user` / `admin`）。
- 不做 admin 代建 trade intent。
- 不做 audit table 完整 schema（BE-V1-16），仍以 stub interface + structured log。
- 不做 admin 監控 dashboard（BE-V1-15）。
- 不做 admin 自助 password reset（須另一 admin 或離線）。

## DB Schema

### `users` 變動

- 加 `mfa_method text null check (mfa_method in ('totp', null))`。
- 加 `mfa_secret_encrypted bytea null`。
- 加 `mfa_enabled_at timestamptz null`。
- 加 `disabled_at timestamptz null`、`disabled_by_admin_id uuid null references users(id)`、`disabled_reason text null`。

### `admin_2fa_resets`

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `requested_by_admin_id uuid not null references users(id)`
- `reason text not null`
- `status text not null check (status in ('pending', 'approved', 'completed', 'rejected'))`
- `approved_by_admin_id uuid null references users(id)`
- `approved_at timestamptz null`
- `completed_at timestamptz null`
- `created_at timestamptz not null`

### Audit stubs（BE-V1-16 補 table）

事件：

- `account_invited`
- `account_activated`
- `account_disabled`
- `account_2fa_enabled`
- `account_2fa_reset_requested`
- `account_2fa_reset_completed`
- `admin_user_detail_viewed`（帶 reason）

## API

### `POST /admin/users`

Auth: admin（2FA verified）。

Body：`{ "email": "...", "role": "user" | "admin" }`

Behavior：

- 建立 `users` row（status = invited）。
- 走 BE-V1-01 invitation flow 寄信。
- Audit `account_invited`。

### `POST /admin/users/{id}/resend-invitation`

- Revoke 舊 invitation、產生新的。
- Audit 同上。

### `POST /admin/users/{id}/disable`

Body：`{ "reason": "..." }`

Cascade：

1. Lock user。
2. user.status = disabled、disabled_at / by / reason。
3. 對所有 owner 為該 user、status in `('scheduled', 'active', 'paused_data_issue', 'paused_market_status')` 的 trade_intents 與 trade_intent_groups，status = `cancelled_by_account_disabled`、cancelled_at = now()。
4. Telegram binding active → status = revoked、revoked_reason = account_disabled。
5. 對該 user 的 outbox events（status = pending）標示 skip user-facing delivery（每 channel delivery skip_reason = skipped_account_disabled），但 outbox 本身設 `status = completed`。
6. Revoke 所有 refresh tokens（reuse detection chain）。
7. Audit `account_disabled`。
8. 不寄通知給 user（帳號已停用，無 channel 可用）。

### `GET /admin/users`（list with search）

Query：`q`（email substring）、`role`、`status`、cursor。

Response：每 user `id` / `email` / `role` / `status` / `mfaEnabled` / `disabledAt` / aggregate（`activeIntentCount`、`triggeredCount30d`、`notificationCount30d`）。

### `GET /admin/users/{id}`

- 回 user profile，不含 intent / notification 內容。
- Audit `admin_user_profile_viewed`（自動寫，no reason）。

### `POST /admin/users/{id}/detail-access`

Body：`{ "reason": "..." }`

- Reason mandatory，min length 10 chars。
- Audit `admin_user_detail_viewed` with reason、target_user_id、admin_id、request_id。
- Response：發出短效 view token（5 分鐘）。

### `GET /admin/users/{id}/intents`、`GET /admin/users/{id}/notifications`

- 需要 view token（透過 header `X-Admin-View-Token` 帶入）。
- Token expire 後重新走 detail-access。
- Audit 每次 access：`admin_user_intent_listed`、`admin_user_notification_listed`。

### Admin 2FA setup

- `POST /admin/me/2fa/setup`：產生 totp secret + provisioning URI。
- `POST /admin/me/2fa/verify`：input code → 啟用。
- 啟用前 admin 只能 access setup / verify / `/auth/me`，其他 admin endpoint 一律 403 `MFA_REQUIRED`。

### Admin 2FA reset

- `POST /admin/users/{id}/2fa-reset/request`：admin A 對 target user 提出 reset。
- `POST /admin/users/{id}/2fa-reset/approve`：另一個 admin B 批准。
- 批准後 target user MFA 重置；下次 login 走 setup。
- Reject 與 audit 全寫。

### MFA verification gate

- Admin login 完成後須走 `POST /auth/mfa-verify`：input TOTP code。
- 通過後 access JWT claim `mfa_verified = true`。
- 所有 `/admin/*`（除 setup/verify）需要 `mfa_verified = true`。

## Error Codes

新增：

- `MFA_REQUIRED`：403。
- `MFA_INVALID_CODE`：401。
- `MFA_NOT_ENABLED`：409。
- `ADMIN_REASON_REQUIRED`：400。
- `ADMIN_VIEW_TOKEN_INVALID`：401。

## 驗收條件

- [ ] Admin 可建立 user 並寄 invitation；舊 invitation 重寄會 revoke 舊 link。
- [ ] Admin 首次登入未設 2FA → 嘗試 `/admin/*` 回 `MFA_REQUIRED`。
- [ ] Admin 2FA 成功 verify 後 access JWT claim `mfa_verified = true`。
- [ ] `POST /admin/users/{id}/disable` cascade 正確：
  - intent status 變 `cancelled_by_account_disabled`
  - telegram binding revoked
  - refresh token revoked
  - outbox 對該 user delivery skip
- [ ] Admin 對他人 intent 嘗試 `POST /trade-intents/{id}/cancel` 仍 403（V1-02 守備）。
- [ ] Admin 嘗試查看 user 詳細資料需 reason，audit 寫入有 reason / target / actor。
- [ ] 2FA reset 需另一 admin approve；同一 admin 不能 self-approve。

## 測試要求

- Unit：disable cascade 邏輯（mock repo）。
- Unit：TOTP 驗證、time skew tolerance（±1 step）。
- Integration：建 user → invitation → activate → login → 顯示 in user list。
- Integration：disable user → 所有 cascade 正確。
- Integration：admin without 2FA → `/admin/*` 403。
- Integration：admin self-approve 2FA reset → 403。
- Integration：admin 對自己 disable → 拒絕（不允許 self-disable）。
- Integration：disable 後 user 既有 access / refresh token 立即失效。
- Integration：disable 過 user 的 historical data 仍存在（不 hard delete）。

## 工程注意事項

- TOTP secret 用 AES-GCM 加密保存，key 來自 env `MFA_SECRET_ENCRYPTION_KEY`。
- 2FA reset 雙人 approve：approver 必須是另一個 admin id，schema 與 controller 同時守備。
- Cascade 對大量 intent 的 user：建議分批 update（每批 100 row），避免單 transaction lock 過久；或用 advisory lock 防止 cascade 期間有新 trigger。
- Disable 時對「正在被 trigger transaction 處理中」的 intent 要小心：用 `SELECT ... FOR UPDATE` + status guard，避免 cascade 與 trigger race。
- Read-with-reason audit 是合規重點；reason 不可省、不可只填空白。
- Admin 不可代取消 user intent 的限制不能只放 UI 層，必須在 V1-02 / 本工單的 controller 守備。
- `cancelled_by_account_disabled` 對 outbox / notification：domain-spec §13 規定不寄通知給 user，因為帳號已停用。Outbox event 對 trigger 已 fired 但未 dispatch 的情境也應 skip。
- Disable 不是 hard delete；歷史 trade_intent / notification / audit 全保留。
- 重新啟用帳號不是本工單範圍（domain-spec §13：重新啟用不恢復舊 intent）；保留 enable endpoint stub 但不在 V1 提供。
