# 通知訊息

通知 title/body 的程式權威來源是 `src/app/services/notification_template.py`。後端在 trigger／TWAP transaction 內建立站內 `notifications` row；若 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID` 都有值，會在該 transaction commit 前以相同內容 best-effort 發送：

```text
{rendered_title}

{rendered_body}
```

任一 Telegram 設定缺少時只保留站內通知。Telegram 失敗不回滾 intent、trigger 或站內通知，只記錄不含 token 的 warning；目前沒有 outbox、delivery attempt 或 retry worker。

## `price_triggered`

策略：`buy_price_alert`、`sell_price_alert`。

```text
{symbol} 到價提醒已觸發

{買進到價提醒|賣出到價提醒}

標的：{symbol}
目標價：{target_price}
觸發價：{trigger_price}
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

## `limit_order_triggered`

策略：`limit_buy_order`、`limit_sell_order`。

```text
{symbol} {限價買單|限價賣單}已觸發

策略：{限價買單|限價賣單}
標的：{symbol}
目標價：{target_price}
觸發價：{trigger_price}
成交 {filled_quantity_lots} 張 / 委託 {quantity_lots} 張
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

「成交」是目前 notify-only 文案，不代表券商成交回報。

## `market_order_triggered`

策略：`market_order`、`market_buy_order`、`market_sell_order`。

```text
{symbol} {市價單|市價買單|市價賣單}已觸發

策略：{市價單|市價買單|市價賣單}
標的：{symbol}
成交參考價：{trigger_price}
成交 {filled_quantity_lots} 張 / 委託 {quantity_lots} 張
報價時間：{quote_time_taipei}

僅通知、未下單、不保證成交。
```

## `trailing_stop_triggered`

策略：`trailing_stop_alert`。內容包含移動模式／幅度、日內 baseline、動態觸發價、實際觸發價、參考價類型與報價時間，結尾同樣標示「僅通知、未下單、不保證成交」。精確格式以 template function 為準。

## `twap_slice`

TWAP scheduler 為每個到期 slice 建立一筆通知：

```text
{symbol} TWAP 第 {sequence_no}/{total_slices} 筆

{多單建倉|空單建倉}
建議市價{買入|賣出}：{planned_quantity_lots} 張
參考價：{reference_price}（{reference_price_type}）
報價時間：{quote_time_taipei}
```

取不到行情時改顯示「目前行情暫不可用，請自行確認市價。」並要求候補價格。

## `twap_price_followup`

候補取得行情後另建通知：

```text
{symbol} TWAP 候補價格

第 {sequence_no}/{total_slices} 筆參考價：{reference_price}（{reference_price_type}）
補發時間：{sent_at_taipei}
此為稍後補發的價格資訊。
```
