# Domain 規則

## Trade Intent

`TradeIntent` 表示使用者希望系統監控的條件，不等於券商委託。目前所有 intent 固定：

- `execution_mode = notify_only`
- `time_in_force = day`
- 數量單位為整張，必須大於零。
- 價格使用 `Decimal`／PostgreSQL `numeric(9,4)`，不得用 floating point 比較或儲存。
- owner 從認證 context 取得，request body 不接受 `ownerUserId`。

## 現行策略

| Strategy | 條件與參考價 |
| --- | --- |
| `buy_price_alert` | ask ≤ 目標價；缺 ask 時才用 last |
| `sell_price_alert` | bid ≥ 目標價；缺 bid 時才用 last |
| `limit_buy_order` | 與買進到價相同，只產生「限價買單已觸發」通知 |
| `limit_sell_order` | 與賣出到價相同，只產生「限價賣單已觸發」通知 |
| `market_order` / `market_buy_order` | 有效行情出現即以 ask 觸發；缺 ask 時用 last |
| `market_sell_order` | 有效行情出現即以 bid 觸發；缺 bid 時用 last |
| `trailing_stop_alert` | 追蹤日內最高參考價，以百分比或固定點數向下計算動態觸發價 |
| `twap_order` | 在指定時段按固定間隔建立切片提醒，不代表送單 |

使用 last fallback 時，trigger snapshot 必須記錄 `trigger_reference_price_type=last_fallback` 與 `fallback_used=true`。

## 狀態

| 狀態 | 語意 |
| --- | --- |
| `scheduled` | 已建立，等待所屬交易日一般盤開始 |
| `active` | 正在接收行情並可觸發 |
| `triggered` | 條件已成立並建立觸發紀錄；terminal |
| `expired` | day intent 已超過有效交易日；terminal |
| `cancelled` | 使用者取消；terminal |
| `cancelled_by_account_disabled` | 帳號停用造成取消；terminal |

只有 `scheduled` 與 `active` 可由使用者取消。狀態轉移與觸發／取消競態由 transaction、row lock 與 status guard 保護。

## 交易日與行情

- 所有時間以 timezone-aware datetime 處理，DB 存 UTC；台股市場規則使用 Asia/Taipei。
- day intent 在盤前建立為 `scheduled`，盤中建立為 `active`；盤後或週末建立時由 trading session service 選定下一個平日。目前沒有正式市場日曆，不處理國定假日或臨時休市。
- evaluator 只接受一般交易時段內、相對目前時間不超過 10 秒的行情。
- 任一價格非正數、bid 大於 ask 或 bid／ask／last 全缺時不觸發。
- 建單時已符合條件可在建立 transaction 內立即觸發；取不到行情不阻擋建單，之後由 dispatcher 評估。

## 價格與去重

- symbol 必須存在且 `tradable`；stock／ETF 使用集中式 tick-size validation。
- 同一 owner、symbol、strategy、策略鑑別值、數量與交易日不能同時存在重複的 scheduled／active 非 TWAP intent。
- TWAP 以 owner、symbol、position side 與交易日防止重複有效計畫。
- 已進入 terminal state 後可重新建立相同意圖。

## 目前邊界

系統不驗證真實持倉、不處理券商帳務、不送單、不支援停利／停損、OCO、corporate action 調整或歷史回測。停利／停損屬尚未完成需求，見 [工單](orders/stop-loss-take-profit.md)。
