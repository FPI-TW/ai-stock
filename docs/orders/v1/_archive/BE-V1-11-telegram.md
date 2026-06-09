# BE-V1-11：Telegram Bind/Unbind 與 Telegram Delivery Worker

## Metadata

- 類型：HITL
- 優先序：P1
- 預估：20h
- 依賴：BE-V1-01, BE-V1-06
- 交付版本：V1

## 背景

V1 支援 Telegram 通知與綁定，不支援從 Telegram 建立交易意圖（domain-spec §7、§12）。Bot 只接 `/start` 與 `/bind <code>`。BE-V1-06 已搭好 outbox + delivery worker 架構，本工單把 Telegram adapter 落地。

Binding 規則：

- Web UI 產生短效 code（10 分鐘）。
- User 在 Telegram bot 輸入 `/bind <code>` 完成綁定。
- 一次性使用，成功立即失效。
- 同 user 同時間只能有一個 active bind code。
- 每 user 最多一個 active Telegram binding；每 Telegram chat 也只能綁一個 user。
- 綁新的之前必須先解除舊。
- 綁定 / 解除寫 audit。

Telegram delivery：

- Retryable error（rate limit、timeout、5xx）→ 最多 3 次 + exponential backoff，不停用 binding。
- Permanent error（bot 被封鎖、chat not found、forbidden）→ binding 標為 `revoked` / `failed_permanent`，後續不再送 Telegram，仍保留 in_app。

## 目標

- 新增 `telegram_bindings`、`telegram_bind_codes` tables。
- 新增 `TelegramAdapter`：呼叫 Bot API 寄送訊息。
- 接 BE-V1-06 outbox worker：實作 telegram channel send。
- 新增 web API：產生 bind code、查綁定狀態、unbind。
- 新增 Bot webhook handler：處理 `/start`、`/bind`、其他指令一律回不支援。
- Bot rate limit / 全域 rate limit（避免 spam）。
- 綁定 audit stub。
- Telegram permanent failure 處理：通知 user `telegram_binding_failed`，保留 in_app delivery（domain-spec §12）。

## 非目標

- 不解析 Telegram CSV、文字、語音建立 intent（domain-spec §7）。
- 不做 user 通知偏好細項（BE-V1-12）。
- 不做 admin 代解 binding（BE-V1-13 / 14 補）。
- 不做 Line 通知（V1 不支援）。

## DB Schema

### `telegram_bindings`

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `telegram_chat_id text not null`
- `telegram_user_id text null`（bot 收到的 from.id）
- `display_name text null`
- `status text not null check (status in ('active', 'revoked', 'failed_permanent'))`
- `bound_at timestamptz not null`
- `revoked_at timestamptz null`
- `revoked_reason text null`（`user_unbind` / `permanent_failure` / `account_disabled` / `replaced`）
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Unique：

- 每 user 最多一個 `status = 'active'`（partial unique index）。
- 每 chat 最多一個 `status = 'active'`（partial unique index）。

### `telegram_bind_codes`

- `id uuid primary key`
- `user_id uuid not null references users(id)`
- `code_hash text not null unique`
- `expires_at timestamptz not null`
- `consumed_at timestamptz null`
- `revoked_at timestamptz null`
- `attempt_count integer not null default 0`
- `created_at timestamptz not null`

每 user 最多一個 `consumed_at is null and revoked_at is null and expires_at > now()`。

### `audit_events`（BE-V1-16 補正式 table，本工單仍以 structured log + stub interface）

事件：

- `telegram_bind_code_created`
- `telegram_bound`
- `telegram_unbound`
- `telegram_binding_failed_permanent`

## TelegramAdapter

`app/adapters/telegram/adapter.py`：

```python
class TelegramAdapter:
    def __init__(self, bot_token: str, http: HTTPClient, rate_limiter: RateLimiter): ...
    def send(self, chat_id: str, text: str, *, markdown: bool = True) -> SendResult: ...
    def is_retryable(self, error: TelegramError) -> bool: ...
```

Send result：

- success → `external_message_id`。
- retryable_error → 進 BE-V1-06 retry queue。
- permanent_error → 本工單在 BE-V1-06 worker 的 telegram adapter integration layer 把 binding revoked + 通知 user。

Rate limit：

- Bot API 限制：每秒每 chat 1 條、每秒全 bot 30 條（粗略）。
- 用簡單 token bucket，per chat + per bot。

Permanent error classifications：

- `chat_not_found`
- `bot_blocked_by_user`
- `forbidden`
- `user_deactivated`

Retryable：

- `rate_limit` (`retry_after` from API)
- `timeout`
- `5xx`
- `network_error`

## Bot Webhook

`POST /telegram/webhook`：

- 由 Telegram 呼叫；secret token 驗證（env `TELEGRAM_WEBHOOK_SECRET`）。
- 解析 update：
  - `/start` → 回 message：「請到 Web UI 取得綁定碼後輸入 `/bind <code>`」。
  - `/bind <code>` → 走 `BindTelegramChat` command（見下）。
  - 其他文字 / 媒體 → 回「目前不支援透過 Telegram 建立提醒，請至 Web UI」。

`BindTelegramChat` command：

1. Lookup `telegram_bind_codes` where hash matches、未 consumed、未 expired、未 revoked、attempt_count < N。
2. 若 code 不存在或過期 → 回 user `綁定碼無效或已過期`，bind_codes 不消費。
3. 若 chat 已綁到他人 → 回 user `此 Telegram 已綁定其他帳號，請聯絡 admin`。
4. 若 user 已有 active binding 但 chat 不同 → 拒絕（domain-spec §7：必須先 unbind）。
5. 成功：
   - `telegram_bindings` insert，status = active。
   - `telegram_bind_codes.consumed_at = now()`。
   - 寫 audit stub。
   - Bot 回 user `綁定成功`。

## Web API

### `POST /me/telegram/bind-code`

- Auth: user。
- Rate limit：1 / 分鐘。
- 若 user 已有 active binding → 拒絕，提示先 unbind。
- 產生 8-12 字長 code，存 `sha256(code)`。
- 同 user 已有未過期 code → revoke 舊 code（domain-spec §7「產生新 code 時舊 code 失效」）。
- Response：明文 code（只回前端一次）+ TTL。

### `GET /me/telegram`

- Auth: user。
- 回 binding status：`unbound` / `active` / `failed_permanent`。

### `POST /me/telegram/unbind`

- Auth: user。
- 找到 active binding → status = revoked、revoked_reason = user_unbind。
- 寫 audit stub。
- Response 204。

## Notification Template 變動

BE-V1-06 已建立 template registry，本工單新增：

- `telegram_binding_failed`：通知 user 「Telegram 綁定異常，後續通知改回站內通知」。

Template 的 telegram body 使用 markdown：

- `price_triggered`：標題 emoji + bullet。
- `oco_sibling_cancelled`、`bracket_ambiguous_trigger`、`intent_paused_*`、`intent_resumed`：對應文案。

## Configuration

新增 env：

- `TELEGRAM_BOT_TOKEN`：缺失則 `TelegramAdapter` 無法啟動；本地測試可用 `LOCAL_MODE` 跳過。
- `TELEGRAM_WEBHOOK_SECRET`：webhook 驗證用 header。
- `TELEGRAM_BIND_CODE_TTL_SECONDS`：預設 600。
- `TELEGRAM_RATE_LIMIT_PER_CHAT`：預設 1/s。
- `TELEGRAM_RATE_LIMIT_GLOBAL`：預設 30/s。

`LOCAL_MODE=true`：

- Bot token 缺失時 TelegramAdapter 以 stub 模式存在，呼叫 `send()` 回 `permanent_error: local_mode_stub` 並 log；不真的 fail 整 outbox。

## Error Codes

新增：

- `TELEGRAM_NOT_BOUND`：404 / 422（unbind / bind-code 對應情境）。
- `TELEGRAM_ALREADY_BOUND`：409。
- `TELEGRAM_BIND_CODE_INVALID`：400（webhook 用，不對外）。

## 驗收條件

- [ ] `telegram_bindings` / `telegram_bind_codes` migration 可 upgrade / downgrade。
- [ ] `POST /me/telegram/bind-code` 產生 code，舊 code 自動失效。
- [ ] Bot `/bind <code>` 成功綁定，二次使用同 code 失敗。
- [ ] 同 chat 已綁他人 → 拒絕。
- [ ] User 已有 active binding 又呼叫 bind-code → 422。
- [ ] User unbind → status = revoked。
- [ ] Outbox telegram delivery 在 retryable error 進 retry；permanent error 將 binding revoked + 發 `telegram_binding_failed`。
- [ ] Rate limit 控制：超過 per chat 速率 → 自動 backoff，不直接 permanent fail。
- [ ] V0.5 / V1-06 既有 tests（in-memory adapter）全綠。

## 測試要求

- Unit：bind code hash / verify、TTL 過期。
- Unit：retryable vs permanent error classification。
- Unit：rate limiter token bucket 行為。
- Integration：bind-code → webhook `/bind` → binding active；db row 正確。
- Integration：webhook 收到非 `/bind` 指令 → 回不支援，不寫 binding。
- Integration：outbox telegram delivery：mock adapter success / retryable / permanent 各路徑。
- Integration：permanent error 後同 user 後續 trigger 不再嘗試 telegram，但 in_app 仍正確。
- Integration：unbind 後再次 trigger 不 send telegram，delivery skip_reason = `telegram_skipped_unbound`。

## 工程注意事項

- Bot token / webhook secret 機密：不可進 log，不可進 audit metadata。
- Webhook handler 必須對 unverified request 直接 403，不洩漏 bot 細節。
- Telegram username 不是穩定 identity（domain-spec §7）；只用 chat id。
- Adapter 透過 HTTPClient interface 注入，測試用 stub；不直接呼叫 `python-telegram-bot` library 在 production code path（避免 lib 升版打破）。
- BE-V1-06 worker 對 telegram channel 的 attempt 上限 = 3；adapter rate limit 退避透過 `available_at` 控制，不在 adapter 內阻塞 thread。
- Permanent failure 將 binding revoked 是 best practice，但 user 可重新走 bind-code 流程綁定（不需 admin 介入）。
- Bot 對 `/bind` 失敗時要回 user 文案，避免 user 不知道為何沒綁上。
- 如果同 chat 已綁到他人帳號，bot 回應只能模糊（不洩漏對方 email）；UI 端可指引聯絡 admin。
