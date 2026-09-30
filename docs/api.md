# API 指南

API 預設 base URL 為 `http://127.0.0.1:8100`。完整 request／response schema、欄位限制與錯誤碼以執行期 `/openapi.json` 或 `/docs` 為準；本文件只整理共通契約與主要使用流程。

## 共通契約

- 除登入、邀請接受、密碼重設、health、symbol 查詢與 Telegram webhook 外，業務 API 需帶 `Authorization: Bearer <accessToken>`。行情來源是呼叫者本人的券商 session，所以 `GET /quotes/current-price` 與 `/dev/*` 也需登入。
- mutating intent API 需帶 `Idempotency-Key`；相同 key + 相同 payload 重放原結果，相同 key + 不同 payload 回衝突。
- response 使用 camelCase；列表採 cursor pagination，常用欄位為 `cursor`、`pageSize` 與 `nextCursor`。
- 每個 response 都會回傳設定指定的 request ID header，預設 `X-Request-Id`。
- 錯誤 envelope 為 `{ "error": { "code", "message", "details", "requestId" } }`；前端邏輯應依 `code`，不要解析 message。
- `VALIDATION_ERROR` 的 `details.errors[]` 固定為 `type`、`loc`、`msg`、`ctx` 四鍵：不回傳原始值（`input`）；`ctx` 只留 schema 常數（`max_length`、`min_length`、`expected` 等），不含 `actual_length` 之類由輸入推得的值；`msg` 只在訊息純為 schema 常數模板的錯誤型別沿用 pydantic 原文，其餘一律固定為 `Invalid value`。避免密碼、身分證字號或憑證的內容或長度在 422 回應中回流。
- owner 由 access token 決定；跨使用者資源以 not found 語意處理，不允許 client 指定 owner。

## Session 與 CSRF

`POST /auth/login` 與邀請接受會回 access token，並設定 HttpOnly refresh cookie 與可讀的 CSRF cookie。`POST /auth/refresh`、`POST /auth/logout` 需要：

- cookies 隨 request 傳送；
- `X-CSRF-Token` 與 CSRF cookie 相同；
- `Origin`／`Referer` 符合 `CORS_ALLOW_ORIGINS`。

Access token 過期時以 refresh 取得新 session。refresh token rotation 偵測到重用會撤銷該使用者的 sessions。Admin API 另要求 admin role 與已驗證 2FA。

## Endpoint 分組

### Health

- `GET /health`：檢查資料庫並回 service、version、environment；DB 不可用時回 503。

### Auth

- `POST /auth/login`
- `POST /auth/invitations/accept`
- `POST /auth/refresh`
- `POST /auth/password-reset/request`
- `POST /auth/password-reset/confirm`
- `POST /auth/logout`
- `GET /auth/me`

### Admin

- `POST /admin/users`：建立 invited user 並寄邀請信。
- `POST /admin/users/with-password`：直接建立 active user。
- `GET /admin/users`
- `POST /admin/users/{id}/disable`：停用並取消兩軌有效委託、撤銷 session；per-user 模式一併登出其券商 session（綁定列保留）。該使用者的券商綁定正在處理中時回 409 `BROKER_BIND_IN_PROGRESS`。
- `POST /admin/users/{id}/reactivate`：復權；per-user 模式且有綁定時以存放的金鑰重新登入券商並訂閱其有效標的，登入失敗只把 `GET /me/broker-account` 的 `status` 標為 `login_failed`，復權本身仍回 204。
- `POST /admin/users/{id}/resend-invitation`
- `POST /admin/2fa/setup`、`POST /admin/2fa/verify`
- `GET /admin/kill-switch`、`POST /admin/kill-switch`
- `PUT /admin/users/{id}/broker-account`：代使用者綁定券商帳號。body 為 `broker`（目前只有 `fubon`）、`personalId`、`password`、`certPfxBase64`（pfx 解碼後 ≤ 64 KB，以解碼後位元組數計）、`certPassword`；系統先以候選 session 試登入並訂閱該使用者的有效委託標的，成功才加密落庫並切換 session，重綁即整包覆蓋。登入失敗回 422 `BROKER_LOGIN_FAILED`（`details.reason` 為 `login_rejected`／`session_limit`／`provider_unavailable`／`cert_invalid`／`credentials_unreadable`／`unknown`，訊息固定白名單、不含券商原文；`GET /me/broker-account` 的 `lastError` 另可能出現 `session_pool_full` 的訊息：啟動時可還原的綁定數超過 `BROKER_MAX_SESSIONS`，依綁定時間先來先登入，超額者等有人解綁後由重連迴圈自動登入）；候選連線建立時發生非券商回應的例外（缺 SDK wheel、SDK 改版等）回 500 `BROKER_SESSION_SETUP_FAILED`（`details.exceptionType` 為例外類別名，不含訊息），屬環境問題、重試無效；`MFA_ENCRYPTION_KEY` 未設定（不分 `LOCAL_MODE`）回 500 `BROKER_CREDENTIAL_KEY_INVALID`（`details.reason` 為 `missing`）；目標非 active 回 409 `ACCOUNT_NOT_ACTIVE`；本系統 `BROKER_MAX_SESSIONS` 已滿回 409 `BROKER_SESSION_LIMIT_REACHED`；同一使用者綁定進行中回 409 `BROKER_BIND_IN_PROGRESS`。`QUOTE_PROVIDER` 不是 `fubon`（`in_memory`／`shioaji_demo` 的共用帳號模式）時沒有 per-user 登入可驗證，一律回 409 `BROKER_BINDING_NOT_ENABLED`（`details.quoteProvider` 為目前模式），不可重試。回應與 log 不含任何機密。
- `DELETE /admin/users/{id}/broker-account`：解除綁定，冪等回 204；一併把該使用者兩軌（含舊軌 TWAP）所有非終態委託轉為 `cancelled` 並登出其券商 session。

### Broker account（使用者自己看）

- `GET /me/broker-account`：回 `broker`、`brokerAccountNo`（遮罩僅留後四碼）、`status`（`active`／`login_failed`）、`certExpiresAt`、`lastLoginAt`（最後一次成功登入）、`lastError`（白名單訊息；啟動、復權或重連時登入失敗也會寫入）、`updatedAt`；未綁定回 404。綁定與解除只有管理員能做。憑證一年一換，更新即重新綁定一次。

### Symbols 與行情

- `GET /symbols?q=&limit=`、`GET /symbols/{symbol}`
- `GET /quotes/current-price/{symbol}`：測試查價能力，走呼叫者本人的行情 session（per-user 模式未綁定券商回 409 `BROKER_ACCOUNT_NOT_BOUND`，已綁定但沒有 session 回 503 `BROKER_SESSION_UNAVAILABLE`），需該 provider 支援即時查價（`shioaji_demo`、`fubon`），否則回 503 `QUOTE_PROVIDER_UNAVAILABLE`；`source` 由 provider 自報；`quoteTime` 是行情 frame 時間；`lastTradeTime` 是最後成交時間，語意依 provider 而定——富邦回報真實成交時間（尚無成交時為 `null`），永豐的 REST 快照沒有成交時間欄位，填的是快照自身時間、不會是 `null`。不是通用市場資料 API。

### Trade intents

- `POST /trade-intents`：相容用的 discriminated-union create endpoint。
- `POST /trade-intents/buy-price-alert`
- `POST /trade-intents/sell-price-alert`
- `POST /trade-intents/limit-buy-order`
- `POST /trade-intents/limit-sell-order`
- `POST /trade-intents/market-order`
- `POST /trade-intents/trailing-stop-alert`
- `POST /trade-intents/twap/preview`、`POST /trade-intents/twap/confirm`
- `GET /trade-intents`、`GET /trade-intents/{id}`
- `POST /trade-intents/{id}/cancel`

新 client 應使用 per-strategy typed endpoints；legacy `POST /trade-intents` 目前仍可用，但不應成為新整合的預設。

所有建單（含 TWAP confirm）的行情都來自 owner 本人的券商 session：`QUOTE_PROVIDER=fubon` 且尚未綁定券商時回 409 `BROKER_ACCOUNT_NOT_BOUND`；已綁定但目前沒有 session（存放的金鑰登入失敗、或斷線尚未接回，`GET /me/broker-account` 會顯示原因）回 503 `BROKER_SESSION_UNAVAILABLE`，可稍後重試；兩者都不落列，限額檢查先於這兩項。取消時只在本人沒有其他同標的有效委託才退訂本人的 session。

### Notifications

- `GET /notifications?unreadOnly=&cursor=&pageSize=`
- `POST /notifications/{id}/read`

通知送 Telegram 的規則與文字見 [通知訊息](notifications.md)。

### Telegram

- `POST /telegram/webhook`：僅供 Telegram 呼叫，驗證 `X-Telegram-Bot-Api-Secret-Token` 與 chat allowlist。owner（`TELEGRAM_OWNER_EMAIL`）尚未綁定券商時，確認建單回覆「尚未綁定券商帳號，請聯絡管理員綁定後再確認。」；已綁定但 session 不可用時回覆「券商連線目前無法使用，請稍後再試；若持續發生請聯絡管理員重新綁定。」；兩者草稿都維持待確認，同一顆按鈕可再按。

群組文字只支援 `limit_buy_order`、`limit_sell_order`、`market_buy_order`、`market_sell_order`。LLM 產生 draft 後，使用者以 inline button 確認或取消；確認會呼叫相同的 core create command。

### Local-only dev API

`LOCAL_MODE=true` 才會掛載：

- `POST /dev/evaluate-quotes`
- `POST /dev/twap/process-due-slices`
- `POST /dev/twap/process-price-followups`

production 必須設定 `LOCAL_MODE=false`，此時 `/dev/*` 不存在。

## 最小建單流程

1. `POST /auth/login` 取得 access token。
2. `GET /symbols` 選擇可用標的。
3. 對對應策略 endpoint 發送 body、Bearer token 與唯一 `Idempotency-Key`。
4. 以 `GET /trade-intents` 查詢狀態。
5. 以 `GET /notifications` 讀取觸發訊息；需要時呼叫 mark-read。
