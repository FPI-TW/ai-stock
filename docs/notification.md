# Notification Messages

本文件列出目前後端可建立並可同步送往 Telegram 的通知訊息。

## Telegram 同步規則

- 啟用條件：`TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID` 皆有值。
- 任一設定留空時，只建立站內通知，不送 Telegram。
- Telegram 送出失敗不影響原本交易意圖、觸發事件或站內通知交易；系統只記錄 warning log。
- Telegram 文字沿用站內通知內容：

```text
{rendered_title}

{rendered_body}
```

## 訊息清單

### `price_triggered`

適用策略：`buy_price_alert`、`sell_price_alert`

Title：

```text
{symbol} 到價提醒已觸發
```

Body：

```text
{買進到價提醒|賣出到價提醒}

標的：{symbol}
目標價：{target_price}
觸發價：{trigger_price}
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

### `limit_order_triggered`

適用策略：`limit_buy_order`、`limit_sell_order`

Title：

```text
{symbol} {限價買單|限價賣單}已觸發
```

Body：

```text
策略：{限價買單|限價賣單}
標的：{symbol}
目標價：{target_price}
觸發價：{trigger_price}
成交 {filled_quantity_lots} 張 / 委託 {quantity_lots} 張
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

### `market_order_triggered`

適用策略：`market_order`、`market_buy_order`、`market_sell_order`

Title：

```text
{symbol} {市價單|市價買單|市價賣單}已觸發
```

Body：

```text
策略：{市價單|市價買單|市價賣單}
標的：{symbol}
成交參考價：{trigger_price}
成交 {filled_quantity_lots} 張 / 委託 {quantity_lots} 張
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

### `trailing_stop_triggered`

適用策略：`trailing_stop_alert`

Title：

```text
{symbol} 移動出場已觸發
```

Body：

```text
策略：移動出場
移動幅度：{trail_value}（{百分比模式|固定點數模式}）
今日最高價：{baseline}
觸發價（{trigger_formula}）：{dynamic_trigger_price}
實際觸發成交價：{trigger_price}（{trigger_reference_price_type}）
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

### `twap_slice`

適用策略：`twap_order`

送出時機：TWAP slice background worker 處理每一筆到期 slice 時送出；每個 slice 會各自建立一筆 notification。

Title：

```text
{symbol} TWAP 第 {sequence_no}/{total_slices} 筆
```

有參考價 Body：

```text
{多單建倉|空單建倉}
建議市價{買入|賣出}：{planned_quantity_lots} 張
參考價：{reference_price}（{reference_price_type}）
報價時間：{quote_time_taipei}
```

無參考價 Body：

```text
{多單建倉|空單建倉}
建議市價{買入|賣出}：{planned_quantity_lots} 張
目前行情暫不可用，請自行確認市價。
```

### `twap_price_followup`

適用策略：`twap_order`

Title：

```text
{symbol} TWAP 候補價格
```

Body：

```text
第 {sequence_no}/{total_slices} 筆參考價：{reference_price}（{reference_price_type}）
補發時間：{sent_at_taipei}
此為稍後補發的價格資訊。
```
