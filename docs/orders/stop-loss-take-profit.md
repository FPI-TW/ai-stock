# 停利／停損提醒

> 狀態：尚未實作。本文件描述待開發需求，不是目前 API 或 schema。

## 目標

讓使用者針對既有多單或聲明的空單部位設定單一停利或停損提醒。條件成立時沿用現有 trigger snapshot、站內通知與選配 Telegram 同步；現階段仍是 `notify_only`，不送出券商委託。

新增兩種明確策略：

- `take_profit_alert`
- `stop_loss_alert`

兩者必填 `position_side = long | short`、`target_price` 與正整數 `quantity_lots`。觸發方向由 strategy + position side 決定，API 不接受獨立 `order_side`：

| Position | 停利 | 停損 | 參考價 |
| --- | --- | --- | --- |
| long | bid ≥ target | bid ≤ target | 缺 bid 時才 fallback last |
| short | ask ≤ target | ask ≥ target | 缺 ask 時才 fallback last |

## 介面與資料

- 新增 typed create endpoints：`POST /trade-intents/take-profit-alert`、`POST /trade-intents/stop-loss-alert`。
- request 與現有 per-strategy endpoints 共用 symbol、quantity、owner scope、rate limit、idempotency、trading date、tick-size 與建立上限規則。
- `trade_intent_core.strategy` 加入兩個值；新增窄衛星表保存 `position_side` 與 original/effective target price，不把策略欄位塞回核心表。
- `dedup_key` 必須納入 position side 與 target price，使同 owner／symbol／strategy／position／price／quantity／trading date 的有效 intent 無法重複建立。
- trigger snapshot 保存 target、實際 trigger price、reference type、fallback flag 與完整 quote snapshot。
- 新增對應通知 template，明確顯示停利／停損、部位方向、目標價、觸發價與「僅通知、未下單」。

## 行為

- 建立時先驗證 symbol、價格 tick、數量與交易時段；不驗證券商真實持倉。
- active intent 建立當下若已有 10 秒內有效行情且條件成立，沿用 inline trigger 在同一 transaction 立即觸發。
- 後續行情由 `TradeIntentCoreDispatcher` 評估；缺 side price 時才使用 last 並標記 fallback。
- kill switch、取消、到期、帳號停用、subscription reconcile 與 terminal-state guard 完全沿用現有新軌行為。
- 本工單不建立 OCO group、兩腳連動取消、`ambiguous_trigger` 或 broker order。

## 驗收條件

- 四種 strategy × position 組合的比較方向與 reference side 正確，含 last fallback。
- 兩個 typed endpoints 拒絕 client owner、order side、未知欄位、無效 tick、非正數量與不支援 symbol。
- 核心 row 與衛星 row 同 transaction 寫入，失敗時一致 rollback。
- 建單立即觸發與 dispatcher 觸發都建立一次且僅一次 trigger／notification。
- duplicate、限流、建立上限、冪等、取消、到期與 kill switch 行為和現有策略一致。
- list／detail response 能回傳 position side 與 target price，既有策略 response 不變。
- migration upgrade／downgrade、unit、API 與 PostgreSQL integration tests 完整，`make check` 與 `make test-integration` 通過。
