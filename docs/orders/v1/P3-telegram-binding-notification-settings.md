# P3：Telegram 綁定 + 通知設定

## Metadata

- 分層：上線後
- 優先序：P2
- ROM：**S–M**
- 依賴：L1（user）、P2（telegram delivery 用 binding 狀態）
- 交付版本：V1
- 併自舊工單：BE-V1-11-telegram、BE-V1-12-notification-settings

## 背景

V0.5 telegram 是 best-effort 無綁定流程，無法按真實 user 派送。上線時只有 in_app；本票補 Telegram 綁定與 user 層通知設定。對齊 `docs/domain-spec.md` §7（Telegram 綁定）、§12（通知通道設定）。

## 目標

- **Telegram bot**：`/start`（說明到 Web UI 取 bind code）、`/bind <code>`（完成綁定）；其他輸入回覆「不支援建立提醒，請到 Web UI」。
- **Bind code**：Web UI 產生短效 code（10 分鐘、一次性、每 user 同時僅一個 active、產新失舊）；嘗試錯誤 rate limit。
- **綁定**：`telegram_chat_id` 綁到 user；每 user 最多一個 active binding、每 chat 只能綁一個 user；綁新前先解舊；chat 已綁他人→拒絕並提示聯絡 admin；綁定/解綁寫 audit。
- **不可用 telegram username 作唯一身份**。
- **通知設定**：user 層通道啟用（`in_app` 必開不可關、`telegram` 可選）；派送以**觸發當下**設定為準，不存建立當下 snapshot；停用 telegram 後後續不送 telegram。
- V1 不做靜音標的 / 勿擾時間 / 每日摘要。

## 非目標

- 不做通知交付機制（P2 擁有）；本票只提供 binding 狀態與設定供 P2 查詢。
- 不從 Telegram 建立交易意圖。

## DB / 介面

- `telegram_bindings`：`id`、`user_id`、`telegram_chat_id unique`、`status`、`bound_at`、`unbound_at`、`revoked_reason`。
- `telegram_bind_codes`：`code_hash`、`user_id`、`expires_at`（10min）、`consumed_at`、嘗試 rate limit。
- `notification_settings`：`user_id`、`in_app_enabled`(固定 true)、`telegram_enabled`。
- API：`POST /telegram/bind-code`（產 code）、bot webhook 處理 `/start`、`/bind`、`GET/PATCH /notification-settings`、`DELETE /telegram/binding`（解綁）。

## 驗收條件

- [ ] bind code 10min 一次性、產新失舊、錯誤 rate limit。
- [ ] `/bind` 綁定成功寫 audit；UI 顯示綁定狀態。
- [ ] chat 已綁他人→拒絕提示聯絡 admin；每 user/每 chat 1:1。
- [ ] 解綁寫 audit；解綁後後續只送 in_app。
- [ ] 通知設定停用 telegram 後，後續觸發 P2 不送 telegram（skip reason `telegram_skipped_disabled`）。
- [ ] in_app 不可關閉。

## 測試要求

- Unit：bind code 生命週期；1:1 約束；設定讀寫。
- Integration：bind 全流程；解綁→只送 in_app；設定變更影響派送（搭配 P2）。

## 工程注意事項

- bind code 存 hash 不存明文；telegram webhook 驗證來源。
- 派送讀「觸發當下」設定（不快取建立時通道）。
