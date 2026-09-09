# API 指南

API 預設 base URL 為 `http://127.0.0.1:8100`。完整 request／response schema、欄位限制與錯誤碼以執行期 `/openapi.json` 或 `/docs` 為準；本文件只整理共通契約與主要使用流程。

## 共通契約

- 除登入、邀請接受、密碼重設、health、symbol／current-price 查詢與 Telegram webhook 外，業務 API 需帶 `Authorization: Bearer <accessToken>`。
- mutating intent API 需帶 `Idempotency-Key`；相同 key + 相同 payload 重放原結果，相同 key + 不同 payload 回衝突。
- response 使用 camelCase；列表採 cursor pagination，常用欄位為 `cursor`、`pageSize` 與 `nextCursor`。
- 每個 response 都會回傳設定指定的 request ID header，預設 `X-Request-Id`。
- 錯誤 envelope 為 `{ "error": { "code", "message", "details", "requestId" } }`；前端邏輯應依 `code`，不要解析 message。
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
- `POST /admin/users/{id}/disable`
- `POST /admin/users/{id}/reactivate`
- `POST /admin/users/{id}/resend-invitation`
- `POST /admin/2fa/setup`、`POST /admin/2fa/verify`
- `GET /admin/kill-switch`、`POST /admin/kill-switch`

### Symbols 與行情

- `GET /symbols?q=&limit=`、`GET /symbols/{symbol}`
- `GET /quotes/current-price/{symbol}`：目前綁定 demo provider 的測試查價能力，不是通用市場資料 API。

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

### Notifications

- `GET /notifications?unreadOnly=&cursor=&pageSize=`
- `POST /notifications/{id}/read`

通知送 Telegram 的規則與文字見 [通知訊息](notifications.md)。

### Telegram

- `POST /telegram/webhook`：僅供 Telegram 呼叫，驗證 `X-Telegram-Bot-Api-Secret-Token` 與 chat allowlist。

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
