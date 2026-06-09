# BE-V1-12：User Notification Settings 與 Notification Center Hardening

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：20h
- 依賴：BE-V1-06, BE-V1-11
- 交付版本：V1

## 背景

V1 通知通道完全以「觸發當下使用者設定」為準（domain-spec §12），不在建立 trade intent 時 snapshot channel。`in_app` 為必開，`telegram` 為可選。BE-V1-06 worker 已預留 `NotificationSettingsService` Protocol，本工單把它落地。

同時補強 V0.5 的 notification list（BE-V0.5-10）：

- BE-V0.5-10 的 list / read API 沿用，但加上 owner_user_id filter（BE-V1-02 已補）。
- 增加 `unreadCount` endpoint。
- 增加 notification type filter / pagination。
- 加 V1 新的 notification types（`intent_paused_*` / `intent_resumed` / `oco_sibling_cancelled` / `bracket_ambiguous_trigger` / `intent_invalid_for_day` / `telegram_binding_failed` / `market_closed_rescheduled` / `system_alert_user`）。

## 目標

- 新增 `user_notification_settings` table。
- 新增 `NotificationSettingsService` 真正實作（BE-V1-06 worker 注入）。
- 新增 user-facing API：查設定、更新設定。
- 擴 `GET /notifications`：支援 type filter、unreadCount。
- 補 V1 新 notification types 的 list 顯示（不顯示資料殘缺）。
- `in_app` 強制開啟，不可關閉（domain-spec §12）。
- 設定變更不影響歷史 delivery，只影響未來。

## 非目標

- 不做靜音標的、勿擾時間、每日摘要（domain-spec §12 明示 V1 不做）。
- 不做 admin 代調 user 通知設定（BE-V1-13）。
- 不做 push notification（V1 不支援）。
- 不做 unread 即時推送（前端仍 polling）。

## DB Schema

### `user_notification_settings`

- `user_id uuid primary key references users(id)`
- `in_app_enabled boolean not null default true`（domain-spec §12：必開，不可關閉。schema 保留欄位以便未來政策變動，但 API 層強制忽略 client 改 false 的請求）
- `telegram_enabled boolean not null default false`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Migration 預設值：

- 對既有 V1-01 建立的 users，預設 `in_app_enabled = true`、`telegram_enabled = false`。
- 新 user 透過 invitation activation 完成時，自動建立 row。

## NotificationSettingsService

`app/services/notifications/settings.py`：

```python
@dataclass(frozen=True)
class NotificationChannelDecision:
    in_app: bool
    telegram: bool
    telegram_skip_reason: str | None

class NotificationSettingsService:
    def get_channels_for_user(self, user_id: UUID) -> NotificationChannelDecision: ...
```

Decision matrix：

| in_app_enabled | telegram_enabled | telegram_binding              | Decision                                          |
| -------------- | ---------------- | ----------------------------- | ------------------------------------------------- |
| true           | true             | active                        | in_app=true, telegram=true                        |
| true           | true             | revoked / failed_permanent    | in_app=true, telegram=false, skip=telegram_skipped_unbound |
| true           | true             | none                          | in_app=true, telegram=false, skip=telegram_skipped_unbound |
| true           | false            | any                           | in_app=true, telegram=false, skip=telegram_skipped_disabled |

`in_app_enabled` 永遠是 true（schema 預留但 API 強制）。

## API

### `GET /me/notification-settings`

Auth: user。

Response：

```json
{
  "data": {
    "inAppEnabled": true,
    "telegramEnabled": true,
    "telegram": {
      "status": "active",
      "displayName": "..."
    }
  }
}
```

### `PUT /me/notification-settings`

Auth: user。

Body：

```json
{ "telegramEnabled": true }
```

Rules：

- 只接受 `telegramEnabled`。
- `inAppEnabled` 不接受（永遠 true）。
- 若 `telegramEnabled = true` 但 user 沒有 active binding → 仍接受儲存，但 worker 端 decision 會 skip telegram delivery（不報錯，UI 提示尚未綁定）。
- 更新後不重 dispatch 既有 pending outbox events；下次 trigger 才生效。

### `GET /notifications`（既有，擴充）

Query：

- `unreadOnly`: boolean
- `type`: comma-separated list of notification types
- `cursor`、`pageSize`

Response 加：

- `unreadCount`：當前 user 全部未讀數（不分 filter）。
- `data[].metadata`：`triggerContext` 等 V1 新欄位。
- `data[].channel`：恆為 `'in_app'`（V1 只列 in_app delivery）。

### `GET /notifications/unread-count`

- 輕量 endpoint，前端 polling 用。
- Response：`{ "data": { "unreadCount": 12 } }`。

## Notification Type 顯示策略

對前端的 UI hint 由 type 決定：

- `price_triggered` → 主要顯示。
- `intent_paused_data_issue` / `intent_paused_market_status` / `intent_resumed` / `intent_invalid_for_day` / `market_closed_rescheduled` → 系統通知區。
- `oco_sibling_cancelled` / `bracket_ambiguous_trigger` → 與觸發通知同列。
- `telegram_binding_failed` → 帳號通知區。
- `system_alert_user` → 系統通知區。

本工單只保證 API 回正確 type 與 metadata，分區由前端決定。

## 驗收條件

- [ ] `user_notification_settings` migration 可 upgrade / downgrade，既有 users 自動補 row。
- [ ] `PUT /me/notification-settings` 把 `inAppEnabled` 改 false 會被忽略（仍 true）。
- [ ] `NotificationSettingsService` 對 4 個 decision matrix case 正確產出 channel decision。
- [ ] BE-V1-06 worker 接 service 後，telegram disabled / unbound 的 user 觸發後 delivery `skip_reason` 正確。
- [ ] `GET /notifications` 支援 `type` filter，`unreadOnly`，`unreadCount` 一起回。
- [ ] 通知設定變更不影響既有 pending delivery（worker 仍依當下 settings 決策；變更後新 trigger 才生效）。

## 測試要求

- Unit：`NotificationSettingsService` decision matrix 全 case。
- Unit：`PUT /me/notification-settings` 拒絕改 `inAppEnabled`。
- Integration：user 關 telegram → 後續 trigger delivery `skip_reason = telegram_skipped_disabled`。
- Integration：user 開 telegram + binding active → telegram delivery 真的被送。
- Integration：user binding 後變 revoked → delivery skip_reason = telegram_skipped_unbound。
- Integration：`GET /notifications` type filter 正確篩 in_app 通知。
- Integration：`unreadCount` 在標已讀後正確下降。

## 工程注意事項

- 設定不對歷史通知重 dispatch；歷史 delivery 的 channel decision 是「dispatch 當下」的 snapshot（domain-spec §12）。
- `in_app_enabled` 欄位保留是為未來政策；若日後允許關閉，API + worker 須一起放開，不是只動 schema。
- Service 必須容忍 `user_notification_settings` row 不存在（未建立 / 老 user）的情境，預設值同 schema default。
- 新 type 的 template / icon 由前端 i18n / asset 接，後端只回 `type` + `messageData`。
- `unreadCount` 不要每次 list 都全表 count；用 partial index `(owner_user_id, read_at)`。
- BE-V0.5-10 的 list / read API 在 V1 不能被認證關掉；本工單在 V1-02 owner scope 上再加 `type filter` 即可。
- 不要把 V0.5 假設的 `LOCAL_USER_ID` 殘留處理寫入 notification settings；BE-V1-01 已 migrate 為正式 user。
