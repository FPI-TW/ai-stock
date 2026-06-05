# BE-V1-19：Production OpenAPI Examples 與 Frontend Contract Fixtures

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：15h
- 依賴：BE-V1-10, BE-V1-12
- 交付版本：V1

## 背景

V0.5 BE-V0.5-11 提供本地 demo 的 API examples handoff。V1 上線前必須交付完整 contract 給前端 repo：

- OpenAPI 3.1 spec 涵蓋全部 V1 endpoints。
- Request / response examples 對 happy path + 主要 error path 完整。
- Contract fixtures（JSON）可被前端直接 import 做 type 與 mock 來源。
- 文件包含 auth flow / idempotency 規則 / cursor pagination / error envelope / rate limit / WebSocket 不採用聲明 / `LOCAL_MODE` 移除聲明。

## 目標

- FastAPI 自動產生 OpenAPI；補 manual annotation 讓 spec 完整。
- 對每個 endpoint：
  - 至少一個 success example。
  - 至少一個 error example（對應主要 error code）。
  - Cursor pagination 用例。
  - Idempotency-Key 用例。
- 提供 `docs/api/v1/openapi.json`（生成 + commit）。
- 提供 `docs/api/v1/fixtures/`：每個 endpoint 一個資料夾，內含 `request.json`、`response.json`、`errors/<code>.json`。
- 提供 `docs/api/v1/README.md`：endpoint 索引 + 流程說明 + auth / idempotency / pagination / rate limit 等規則文件。
- CI gate：FastAPI 自動產生的 spec 與 commit 中的 `openapi.json` 一致；不一致 → CI fail。
- Frontend repo 可透過 release tag 拉 `docs/api/v1` 作為 contract 來源。

## 非目標

- 不寫前端程式。
- 不交付 SDK / client libraries（可後續做）。
- 不交付 production deployment guide（BE-V1-17 範圍）。
- 不交付 Telegram bot 操作說明（內部 ops doc）。

## 文件結構

```
docs/api/v1/
├── README.md                   # 索引 + 規則
├── openapi.json                # 自動產生 + commit
├── auth.md                     # auth flow / CSRF / refresh / MFA
├── idempotency.md              # idempotency key 規則
├── pagination.md               # cursor pagination
├── error-envelope.md           # error envelope + error code matrix
├── rate-limit.md               # rate limit buckets + Retry-After
├── notifications.md            # notification types + metadata schema
└── fixtures/
    ├── auth/
    │   ├── invitation-accept/
    │   │   ├── request.json
    │   │   ├── response.json
    │   │   └── errors/
    │   │       ├── INVITATION_EXPIRED.json
    │   │       └── INVITATION_CONSUMED.json
    │   ├── login/
    │   ├── refresh/
    │   ├── logout/
    │   ├── password-reset-request/
    │   ├── password-reset-confirm/
    │   └── mfa-verify/
    ├── trade-intents/
    │   ├── create/
    │   ├── list/
    │   ├── detail/
    │   ├── cancel/
    │   ├── csv-preview/
    │   └── csv-confirm/
    ├── trade-intent-groups/
    │   ├── create/
    │   ├── cancel/
    │   └── detail/
    ├── symbols/
    │   └── list/
    ├── market-calendar/
    │   └── today/
    ├── quote/
    │   └── effective-price-preview/
    ├── notifications/
    │   ├── list/
    │   ├── unread-count/
    │   └── read/
    ├── me/
    │   ├── telegram/
    │   ├── notification-settings/
    │   └── profile/
    └── admin/
        ├── users/
        ├── symbols/
        ├── market-calendar/
        ├── corporate-actions/
        ├── kill-switches/
        ├── alerts/
        ├── metrics/
        └── audit-events/
```

每個 fixture 資料夾必備：

- `request.json`：完整 headers + body（含 `Idempotency-Key`、`X-CSRF-Token` 等）。
- `response.json`：success response。
- `errors/<code>.json`：對應 error code response。

## OpenAPI 補強

FastAPI 自動產生不足之處需手動補：

- 每個 schema 加 `description`、`example`。
- 每個 error response 加完整 envelope schema reference。
- Cursor pagination 標準化：

```yaml
parameters:
  cursor: string | null
  pageSize: integer (default 50, max 100)
responses:
  200:
    schema:
      data: array
      nextCursor: string | null
```

- Headers：`Idempotency-Key`、`X-Request-Id`、`X-CSRF-Token`、`Retry-After`。
- Cookies：`refresh_token`、`csrf_token`（auth flow 用，標示 HttpOnly / Secure / SameSite）。
- Security schemes：Bearer access token + CSRF cookie 組合。

## Auth / 規則文件

### auth.md

- Invitation → accept → set password → 自動 issue access + refresh + CSRF。
- Login flow + lockout。
- Refresh rotation + reuse detection。
- CSRF double-submit cookie。
- MFA gate for admin。
- Cookie attributes table。
- 跨站部署 / CORS 注意事項。

### idempotency.md

- 哪些 mutating endpoints 必須帶 key（domain-spec §16）。
- Key 規則：UUID v4、user-scoped、24h TTL、修改 payload 必換新 key。
- Same key + same payload → cached response。
- Same key + different payload → 409。
- Missing key → 400。

### pagination.md

- Cursor pagination 規則：opaque cursor、預設 50、最大 100。
- 排序語意對 history list / active list 不同。
- No total count。

### error-envelope.md

- Envelope schema。
- Error code 完整 matrix（含 HTTP status）。
- `requestId` 必帶。
- `details` 對某些 error 有結構化資訊（如 `nearestPrices`、`rowNumber`）。

### rate-limit.md

- Bucket 規格。
- 回 429 + `Retry-After`。
- 如何在前端對使用者顯示 retry 倒數。

### notifications.md

- 全 type 清單。
- 每 type 的 metadata schema（`triggerContext`、`originalTargetPrice` 等）。
- 文案規範（「僅通知、未下單、不保證成交」）。
- Unread count semantics。

## CI Gate

GitHub Actions / GitLab CI job：

```yaml
- name: Generate fresh OpenAPI
  run: python -m app.cli.gen_openapi > /tmp/openapi.json
- name: Diff against committed
  run: diff /tmp/openapi.json docs/api/v1/openapi.json
```

差異 → CI fail；提示開發者跑 `make openapi-update`。

`make openapi-update` 重新產生 + commit。

## 驗收條件

- [ ] `docs/api/v1/openapi.json` 涵蓋所有 V1 endpoints。
- [ ] 每個 endpoint 至少一個 success example + 一個主要 error example。
- [ ] Fixtures 內 JSON payload 通過 schema validation（CI step）。
- [ ] `auth.md` / `idempotency.md` / `pagination.md` / `error-envelope.md` / `rate-limit.md` / `notifications.md` 完整。
- [ ] OpenAPI CI gate 對未更新 spec 的 PR 會 fail。
- [ ] V0.5 handoff doc 仍存在（標 deprecated），不再為 V1 引用。
- [ ] 文件不出現已移除的 endpoints（`POST /dev/quotes` / `shioaji_demo` env 等）。

## 測試要求

- CI：OpenAPI gate（生成 vs commit diff）。
- CI：fixture JSON schema validation（每個 fixture 對 schema 驗證）。
- Integration：對 sample fixture 重新 POST 到 test server 仍能正確處理（避免 example 與實作 drift）。
- 手動：前端工程師 review 一輪，確認可用。

## 工程注意事項

- FastAPI 對複合 cookie + header auth 的 OpenAPI 描述需手動補；不要依賴自動推斷。
- Cursor pagination response 必含 `nextCursor`，即使該頁是最後一頁也回 `nextCursor: null`；前端可用一致 logic。
- Example 不要寫真實 credentials / user data；統一用 dummy（`user-001`、`alice@example.com` 等）。
- Telegram bot token、CSRF token、refresh token、access token 在 example 統一用 `<redacted>` placeholder。
- 對 BE-V1-12 新 notification type 的 metadata schema，必須在 `notifications.md` 顯式列出，否則前端難實作。
- OpenAPI gen 可用 FastAPI 內建 `app.openapi()`；但需 monkey-patch 加 examples（FastAPI 對 examples 支援 OK 但 verbose）。
- 不要把 admin endpoint 直接暴露在公開 API doc（前端 repo 看不到 admin 邏輯）；可拆分為 `openapi-user.json` + `openapi-admin.json`，但 V1 為簡化先共用一份。
- 對 V1 後續若新增 endpoint，必須 PR 包含 fixture / OpenAPI 更新，CI gate 自然強制。
