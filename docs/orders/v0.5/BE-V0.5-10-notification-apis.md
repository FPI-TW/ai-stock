# BE-V0.5-10：Notification List/Read APIs

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：15h
- 依賴：BE-V0.5-09
- 交付版本：V0.5

## 背景

V0.5 只需要展示 trigger 後有站內通知資料可查看。正式 channel settings、delivery attempts、Telegram delivery、read/unread 複雜偏好都留到 V1。

## 目標

- Notification list API。
- Mark read API。
- Unread count 可選，但若前端需要可一起提供。
- Owner scope。

## 非目標

- 不做 NotificationDelivery。
- 不做 Telegram。
- 不做通知偏好。
- 不做 retry。
- 不做 template versioning。

## API

### `GET /notifications`

Query：

- `unreadOnly` optional boolean。
- `cursor` optional。
- `pageSize` optional，預設 50，最大 100。

Response：

```json
{
  "data": [
    {
      "id": "...",
      "type": "price_triggered",
      "tradeIntentId": "...",
      "renderedTitle": "2330 到價提醒已觸發",
      "renderedBody": "僅通知，未下單，不保證成交...",
      "readAt": null,
      "createdAt": "2026-05-12T02:00:00Z"
    }
  ],
  "nextCursor": null
}
```

### `POST /notifications/{id}/read`

Rules：

- 只能標記 local owner 的 notification。
- 已讀重複呼叫應 idempotent。
- 不影響 trade intent status。

Response：

```json
{
  "data": {
    "id": "...",
    "readAt": "2026-05-12T02:01:00Z"
  }
}
```

## 驗收條件

- [ ] Notification list 只回傳 local user context 的通知。
- [ ] Notification 包含 rendered title/body、created_at、read_at。
- [ ] User 可將 notification 標記為 read。
- [ ] Read status 不影響 trade intent status。

## 測試要求

- API test：list returns created notification。
- API test：unreadOnly filters read notifications。
- API test：mark read sets `read_at`。
- API test：mark read twice succeeds。
- API test：mark read does not mutate trade intent。

## 工程注意事項

- Cursor 可先使用 `created_at + id`，不要用 offset 作主要設計。
- V1 會擴充 template/delivery，不要把 V0.5 schema 設計到無法加欄位。
- Read model 保持 owner scope。
