# 股票交易意圖與到價通知系統規格草稿

## 1. 版本範圍

### V1：交易意圖管理與到價通知

V1 不串接券商下單、不送出真實委託。系統只負責建立交易意圖、監控近即時 quote、套用除息調整、觸發通知與保存稽核紀錄。Shioaji 僅作為目前行情來源，不使用其下單、帳務或持倉能力。

支援能力：

- 台股現股與台股 ETF。
- UI 單筆建立。
- Telegram 通知與 UI 站內通知。
- 買賣到價提醒。
- 停利 / 停損提醒。
- 停利 + 停損 OCO 群組提醒。
- 限價到價提醒（`limit_buy_order` / `limit_sell_order`，notify-only）。
- 市價委託意圖提醒（`market_order` / `market_buy_order` / `market_sell_order`，notify-only）。
- TWAP 時間加權切片意圖提醒（`twap_order`，notify-only，已交付）。
- 移動出場提醒（code：`trailing_stop_alert`，對使用者不拆移動停利 / 移動停損，已交付）。
- 現金股利除息固定金額調整。
- 使用者層級通知通道設定。
- Admin 管理使用者、corporate actions、系統告警與 kill switch。

不支援能力：

- 券商下單 / 帳務 / 持倉串接。
- 自動下單。
- 證交所自動匯入 symbol master / market calendar（待券商對接 / 正式營運；V1 用 seed / 固定時段）。
- 國定假日 / 颱風臨時休市 / 半日盤 / 補班自動處理（待券商對接 / 正式營運）。
- 漲跌停超出當日限制驗證與 `invalid_for_day` 生命週期（待券商對接 / 正式營運）。
- 後端 CSV 專屬功能：CSV upload API、CSV preview API、batch draft、batch import、CSV row errors、CSV metadata 與 CSV retention。
- 零股、金額模式。
- 空單進場。
- 通訊軟體文字或語音建立交易意圖。
- Line 通知。
- 多帳戶、多投資組合、團隊協作、代理建立。
- 使用者手動暫停 / 恢復交易意圖。
- 編輯已建立交易意圖。
- 使用者匯出資料。

### V1.5：保留給後續 notify-only 擴充

移動出場與 TWAP notify-only 均已在 V1 交付。V1.5 目前不承諾額外後端策略範圍；若未來需要拆分「移動停利啟動門檻」或其他進階 notify-only 策略，需另行定義 capability matrix 與審核規則。

### V2：券商串接與人工確認下單

V2 逐步開放券商串接。使用者必須人工確認才送單。

支援方向：

- `execution_mode = notify_only | manual_confirm_order`
- notify-only 模式永久保留。
- 初期只支援限價單。
- TWAP 人工確認下單能力移至 V2 逐步開放（V1 已交付 TWAP notify-only 切片意圖）。
- 停利、停損、TWAP 等下單能力依 capability matrix 逐步開放，不因 V1 可通知而自動可下單。

人工確認送單流程：

1. 交易意圖到達目標價後跳出提示，顯示有效目標價與觸發當下價格。
2. 使用者選擇下單後，進入下單確認介面。
3. 前端顯示當前 quote，並每 5 秒刷新一次。
4. 使用者按送出時不阻擋。
5. 後端送單前重新取得 submit quote。
6. 後端比較前端最後展示 quote 與 submit quote。
7. 若偏移 `>= 3 ticks`，仍照常送單。
8. Response 帶回 drift warning，前端在送單結果顯示警告。

必須保存：

- 觸發時 quote。
- 確認頁最後展示 quote。
- 後端送單前 submit quote。
- drift ticks。
- drift threshold。
- 實際委託內容。
- 券商回應。

### V3：授權後自動下單

V3 支援授權後到價直接執行。

支援方向：

- `execution_mode = notify_only | manual_confirm_order | auto_order`
- 第三版自動下單不可直接沿用 V2 的「前端最後展示 quote」偏移警告機制。
- 需另定價格保護、允許滑價、限價保護、最大偏移、觸發有效時間與授權範圍。

## 2. 核心概念

### TradeIntent

`TradeIntent` 表示使用者的交易意圖，不等於券商委託單。

V1 中，交易意圖只代表：

- 使用者想監控哪個標的。
- 在什麼價格條件下觸發。
- 觸發後通知哪些通道。
- 觸發後不下單。

V2 後才新增券商相關模型，例如：

- `BrokerOrderDraft`
- `BrokerOrder`
- `ExecutionReport`

### Execution Mode

`TradeIntent.execution_mode` 預留三種模式：

- `notify_only`
- `manual_confirm_order`
- `auto_order`

V1 固定為 `notify_only`。

### Time In Force

資料模型保留：

- `day`
- `gtc_until`

V1 所有外部入口只允許 `day`。`gtc_until` 僅預留，不對 UI、Telegram 開放。

`day` 定義：

- 只覆蓋台股一般交易時段。
- 一般盤收盤後失效。
- 不支援盤後觸發。
- 盤前建立會先進 `scheduled`，開盤後轉 `active`。
- 收盤後或非交易日建立，預設套用下一個台股交易日。

## 3. Strategy Capability Matrix

| Strategy | V1 notify-only | V1.5 notify-only | V2 manual confirm | V3 auto order |
| --- | --- | --- | --- | --- |
| `buy_price_alert` | 支援 | 支援 | 逐步開放限價單 | 需重新審核 |
| `sell_price_alert` | 支援 | 支援 | 逐步開放限價單 | 需重新審核 |
| `take_profit_alert` | 支援 | 支援 | 逐步開放 | 需重新審核 |
| `stop_loss_alert` | 支援 | 支援 | 逐步開放 | 需重新審核 |
| 移動出場（code：`trailing_stop_alert`）| 支援 | 支援 | 逐步開放 | 需重新審核 |
| `limit_buy_order` / `limit_sell_order` | 支援 | 支援 | 逐步開放 | 需重新審核 |
| `TWAP`（code：`twap_order`）| 支援 | 支援 | 逐步開放 | 需重新審核 |
| 市價單（code：`market_order` / `market_buy_order` / `market_sell_order`）| 支援 | 支援 | 初期不支援 | 需重新審核 |

> V1 capability matrix 中標「支援」者均為 notify-only 意圖提醒（不送券商委託）。

## 4. 策略語意與觸發方向

### 一般買賣到價提醒

V1 使用：

- `buy_price_alert`
- `sell_price_alert`

不使用「進場賣出」這種命名，避免與做空進場混淆。

欄位語意：

- `buy` / `sell` 是交易方向或委託方向，用來決定行情參考價。
- `long` / `short` 是持倉方向，只用於停利 / 停損等持倉型策略。
- 進場型買賣提醒不使用 `position_side`，也不以 `long` / `short` 表示買賣。

觸價欄位：

- 買進提醒：優先用 `ask_price <= target_price_effective`。
- 賣出提醒：優先用 `bid_price >= target_price_effective`。
- 缺 ask / bid 時可 fallback 到 `last_price`，但必須標記 `fallback_used = true`。

### 停利 / 停損

停利 / 停損屬於持倉型策略，必填：

- `position_side = long | short`

進場型買賣提醒不需要 `position_side`。

停利 / 停損的 `order_side` 由 `position_side` 推導，不允許使用者手動指定。

推導規則：

- 多單停利：賣出提醒，`bid >= target`
- 多單停損：賣出提醒，`bid <= target`
- 空單停利：買回提醒，`ask <= target`
- 空單停損：買回提醒，`ask >= target`

V1 允許使用者聲明空單持倉，但不驗證實際持倉。第二版接券商後才檢查是否確實有持倉。

V1 不支援做空進場，只支援空單的停利 / 停損回補提醒。

### 移動出場（notify-only）

`trailing_stop_alert` 對使用者呈現為「移動出場」，不拆成移動停利 / 移動停損兩個策略。

- 機制為 baseline + dynamic trigger。
- V1 已交付，僅支援 `day`。
- 當日一般盤內維護 high watermark。
- 價格回落到動態觸發價時出場通知。
- 參考價使用賣出方向：優先用 `bid`，缺則 fallback 到 `last_price` 並標 `fallback_used = true`。
- 收盤後未觸發即過期，不跨日延續。

> 「移動停利」與「移動停損」的差異需要使用者成本或啟動門檻才能精確判定；V1 後端不拆此語意，統一以「移動出場」管理。

### 市價單意圖（notify-only）

`market_order` / `market_buy_order` / `market_sell_order` 為 notify-only 市價意圖提醒，語意與「到價提醒」不同：

- **無價格條件**：不等任何目標價。
- **即時觸發**：意圖轉 active 後，在**下一個有效報價**即觸發（市場休市時等下一盤首個有效報價）。
- **參考價**：買用 `ask`、賣用 `bid`；缺則 fallback 到 `last_price` 並標 `fallback_used = true`。
- **一次性**：每個 intent 僅觸發一次（`trigger_events` 對 `trade_intent_id` unique）。

> 設計意圖：市價單是「將來接券商後『價格到了就下單』」的 notify-only 影子版本——市價委託本就應即時成交，故「有報價就觸發」在 V2 下單語意下正確。**前端不可呈現為「等某價位」**。

## 5. OCO 群組

V1 支援停利 + 停損群組。

底層模型：

- `TradeIntentGroup`
- `group_type = bracket_alert`
- 兩筆子 `TradeIntent`：
  - `take_profit_alert`
  - `stop_loss_alert`

OCO 規則：

- 任一子 intent 觸發後，另一筆自動取消。
- 觸發通知送出失敗時，不回滾 OCO 狀態。
- 使用者取消其中一腳時，取消整組。
- 不允許只取消其中一腳。

建立驗證：

- 多單：停利價必須大於停損價。
- 空單：停利價必須小於停損價。

若同一次 quote 更新中兩腳同時成立，不任選一邊，group 與兩筆子 intent 都標記為 terminal 狀態 `ambiguous_trigger`。系統不送一般到價通知，而是發送異常 system notification，告知條件或行情異常、未觸發任一提醒，並要求使用者重新建立。同時記錄 admin warning，因為可能是 quote 資料異常或驗證漏網。

## 6. 狀態機

### TradeIntent Status

`TradeIntent` 本身沒有 draft 狀態：後端不保存草稿態的 `TradeIntent`，單筆表單於前端填寫，使用者確認後才建立。

主要狀態：

- `scheduled`
- `active`
- `triggered`
- `expired`
- `cancelled`
- `invalid_for_day`
- `paused_data_issue`
- `paused_market_status`
- `ambiguous_trigger`
- `cancelled_by_account_disabled`

Terminal statuses：

- `triggered`
- `expired`
- `cancelled`
- `invalid_for_day`
- `cancelled_by_account_disabled`
- `ambiguous_trigger`

Non-terminal statuses：

- `scheduled`
- `active`
- `paused_data_issue`
- `paused_market_status`

`paused_*` 可由系統或 admin 恢復到 `active`，但使用者不能手動恢復。

狀態規則：

- 盤前、收盤後、非交易日建立的 `day` intent 先進 `scheduled`。
- 到對應交易日一般盤開盤後轉 `active`。
- 條件成立後轉 `triggered`。
- `day` 收盤後未觸發轉 `expired`。
- 使用者取消轉 `cancelled`。
- 當日價格限制或交易日規則導致無法監控轉 `invalid_for_day`（觸發來源——漲跌停驗證、臨時休市改期——待券商對接 / 正式營運；V1 保留此 status 值但不產生）。
- 除息 / snapshot 資料異常轉 `paused_data_issue`（行情資料異常自動暫停隨 licensed quote 一併延後，V1 不由 quote 觸發此狀態）。
- 暫停交易 / 盤中停止交易轉 `paused_market_status`。

Expiry 需有雙保險：expiry job 或 lightweight lifecycle reconciliation 於收盤後將 day intents 轉 `expired`，但 quote evaluator 每輪評估前仍需檢查 market session，一般盤外不得觸發。若收盤後仍存在應過期的 active intents，需產生 admin alert；補跑 expiry job / lifecycle reconciliation 後轉 `expired`，不發到價通知。

`invalid_for_day` 需產生 system notification，至少送 `in_app`，Telegram 依觸發當下使用者設定派送。通知文案需說明原因，例如超出漲跌停、今日停止買賣，且不得稱為到價通知。OCO group 任一腳 `invalid_for_day` 時，整組轉 `invalid_for_day` 並通知使用者。

使用者不可手動暫停 / 恢復，只能取消與複製重建。

已建立的 `TradeIntent` 不支援直接編輯。修改價格、張數、策略、通知通道都必須取消並重建。

`triggered` 為 terminal 狀態，使用者不能取消已觸發 intent，只能在 UI 中查看歷史或標記通知已讀。取消 `scheduled`、`active`、`paused_data_issue`、`paused_market_status`、`invalid_for_day` 等未觸發狀態時，尚未送出的 pending notification deliveries 標記 `skipped_cancelled`。Trigger 與 cancel 併發時，DB transaction 需以狀態條件保護，避免同一 intent 同時被取消與觸發。

## 7. 輸入方式

### UI

UI 支援：

- 標的 autocomplete。
- 使用者可輸入代碼或中文名稱搜尋。
- 必須從候選標的選取。
- 後端只接收標準 `symbol`。

CSV 解析屬於前端功能，不屬於後端 domain/API 範圍：

- 前端若提供 CSV 上傳、解析、預覽或欄位對應，均在 client 端完成。
- 後端不提供 CSV upload / preview / confirm / batch draft API。
- 後端不保存 CSV batch、source row、source filename、raw row hash 或 normalized CSV rows。
- 前端確認後只能呼叫既有標準 API，例如單筆 `CreateTradeIntent` 或 TWAP preview / confirm；後端不提供 CSV all-or-nothing 批次語意。
- 後端錯誤回應只針對標準 API payload，不提供 CSV row error envelope。

UI 建立入口分為：

- 買賣到價提醒。
- 持倉停利 / 停損提醒。
- 移動出場提醒。
- TWAP 意圖提醒。

UI 單筆建立的除息互動：

- 使用者選擇特定標的後，前端向後端查詢該標的今日是否有配息調整。
- 若今日有配息，使用者輸入目標價格後，前端再向後端請求權威計算，取得 `target_price_effective`、調整金額、tick rounding 與說明。
- 若今日無配息，前端不需額外請求有效價試算，可直接以原始目標價展示。
- 無論前端是否請求試算，後端建立 TradeIntent 時仍必須重新執行完整驗證與權威計算。
- 若建立送出時後端發現需要配息調整，但使用者尚未看過調整後價格，後端拒絕建立並回傳 `preview_required` 或 `stale_price_context`，前端需顯示最新有效價並要求使用者重新確認。

手機版 Web 為 V1 核心需求，至少支援：

- 登入。
- 查看提醒。
- 建立單筆提醒。
- 取消單筆或依標的取消。
- 查看站內通知。
- Telegram 綁定狀態。

TradeIntent 列表以 trading date + status 作為主軸，而非單純建立時間。建議分組：

- 今日監控中：`active`
- 即將生效：`scheduled`，需顯示 trading date
- 已觸發
- 已過期 / 已取消 / 今日無效
- 暫停中：資料異常、市場狀態

每筆至少顯示 symbol / 名稱、strategy、quantity lots、original target、effective target、trigger reference price type、trading date、last valid quote time、status。

歷史列表規則：

- Active / scheduled / paused 永遠優先顯示。
- History 預設顯示近 30 天。
- 可用日期區間查詢。
- 單次查詢最多 90 天。
- 2 年外資料依保留政策不可查或已清除。

### Telegram

V1 支援 Telegram 通知與綁定，不支援從 Telegram 建立交易意圖。

Telegram bot V1 僅支援：

- `/start`：說明需到 Web UI 取得 bind code。
- `/bind <code>`：完成 Telegram 綁定。

其他文字、語音、檔案或指令都回覆目前不支援建立提醒，請到 Web UI 操作。V1 不解析 Telegram CSV，不保存使用者傳來的非必要內容，最多保留 technical log 90 天或更短。

綁定流程：

1. 使用者在 Web UI 點擊綁定 Telegram。
2. 系統產生短效 code。
3. 使用者到 Telegram bot 輸入 `/bind <code>`。
4. Bot 將 `telegram_chat_id` 綁定到 user。
5. UI 顯示綁定成功。

不可使用 Telegram username 作為唯一身份。

Bind code 規則：

- 有效 10 分鐘。
- 一次性使用，成功綁定後立即失效。
- 每位使用者同時間只能有一個 active bind code。
- 產生新 code 時舊 code 失效。
- Code 嘗試錯誤需 rate limit。
- 綁定成功需寫 audit。
- 若 Telegram chat 已綁到另一個 user，拒絕綁定並提示聯絡 admin。
- 每個 user 最多只能有一個 active Telegram binding。
- 每個 Telegram chat 也只能綁定一個 user。
- 綁定新的 Telegram chat 前，必須先解除舊 binding。
- 解除綁定需寫 audit；解除後後續觸發只送 `in_app`。

## 8. 數量與標的範圍

V1 支援：

- 台股現股。
- 台股 ETF。
- 整股張數。

V1 不支援：

- 零股。
- 金額模式。
- 期貨、選擇權、加密貨幣、美股。

數量模型：

- `quantity_lots`
- 必填。
- 正整數。
- `1` 代表 1 張。

> **V1 staging 限制**：目前報價來源 Shioaji demo 僅允許 4 檔白名單標的（`2330` / `2317` / `0050` / `00878`）、同時最多訂閱 5 檔。「台股全市場現股 / ETF」為產品目標，正式營運（待券商對接、換正式報價源）後解除；V1 staging 期間實際可監控標的受此上限約束。

## 9. 價格、tick 與除息調整

### 價格精度

禁止使用 floating point 儲存價格。

建議使用：

- Decimal。
- 或整數 tick representation。

時間：

- 儲存 UTC timestamp。
- 同時保存市場時區下的 `trading_date`。
- 台股交易日以 Asia/Taipei 計算。

### Tick Size

V1 必須實作台股 tick size table，用於：

- 使用者輸入目標價驗證。
- 除息調整後價格修正。
- V2 drift ticks 計算。

使用者原始目標價若不符合 tick size，拒絕建立並提示最近合法價格。

### 除息調整

V1 只支援配息，也就是現金股利固定金額扣除。不支援配股、減資、分割、合併等比例或複合調整。

統一公式：

- `target_price_original`：使用者輸入價。
- `corporate_action_adjustment_amount`：非除息日為 0。
- `target_price_effective = target_price_original - corporate_action_adjustment_amount`。

策略判斷、通知、V2/V3 下單草稿都使用 `target_price_effective`。

原始目標價不可改寫，必須保留：

- `target_price_original`
- `target_price_effective`
- `price_adjustment_reason`
- `price_adjustment_amount`
- `price_adjusted_at`

若除息調整後價格不是合法 tick，採 `round away from trigger`，避免 rounding 讓條件更容易觸發。

除息資料：

- 自動抓取。
- 允許 admin 人工維護。
- 外部資料只負責匯入。
- 策略引擎一律讀內部 `corporate_actions` / snapshot。

Corporate action importer 必須透過 provider adapter 抽象。V1 只實作一個 primary source。

Provider interface 建議：

- `CorporateActionProvider.fetchUpcomingActions(dateRange): CorporateActionRaw[]`

Import 流程：

1. Provider 抓 raw data。
2. Importer normalize 成內部格式。
3. 比對既有資料。
4. 自動更新未鎖定資料。
5. 產生 import report。
6. Admin 可人工覆寫。
7. 盤前產生 snapshot。

V1 只套用 cash dividend。配股與其他 corporate action types 可匯入並標記 `unsupported`，但不套用價格調整。

若某標的當日存在會影響價格基準的 unsupported corporate action，不可只套用部分調整。相關提醒轉 `paused_data_issue`，通知使用者「今日公司行動類型暫不支援，提醒已暫停」。Admin 可人工覆寫為可支援的 cash adjustment snapshot，但必須 audit。

盤前產生 `trading_day_adjustment_snapshot`。一般盤開始後，策略引擎只讀當日 snapshot。盤中修正不自動重算已啟用意圖，而是標記資料異常並影響下一次 snapshot。

若 snapshot disputed：

- 相關標的 active intents 轉 `paused_data_issue`。
- 不觸發。
- 通知受影響使用者。
- 恢復時不回放暫停期間行情，只從最新有效 quote 開始評估。

## 10. 漲跌停與市場狀態

> **本節屬正式營運階段（待券商對接），非 V1。** V1 不做漲跌停超出驗證，也不做盤中停止交易自動偵測（V1 用固定一般盤時段）；以下描述目標行為。

day intent 的有效目標價若超出當日漲跌停：

- 建立時可判定則拒絕。
- scheduled 啟用前才判定則轉 `invalid_for_day` 並通知使用者。

市場狀態：

- 暫停交易 / 停止買賣：阻擋或停用監控。
- 處置股、注意股、全額交割：只提示，不阻擋。
- V2 下單時再強化交易限制檢查。

## 11. Quote 監控

V1 使用近即時 quote，不使用逐筆即時，不使用延遲行情作為交易提醒基礎。

Quote source 必須透過 provider adapter 抽象，不讓 quote evaluator 直接依賴特定資料商。V1 已接上 Shioaji quote provider 作為目前 primary quote source，且只使用行情能力；若 primary provider 故障，該輪不觸發、記錄 error 並告警 admin（§18），不自動混用多資料源。

正式行情 vendor 尚未決定。正式 vendor 決定後，需以新的 provider adapter 串接，並重新確認授權條款、支援市場、bid / ask / last / quote time 品質與 production SLO。

Provider interface 建議：

- `QuoteProvider.getQuotes(symbols): QuoteSnapshot[]`

Normalized quote 至少包含：

- `symbol`
- `bid_price`
- `ask_price`
- `last_price`
- `quote_time`
- `source`
- `source_latency_label`
- `raw_payload_ref` 或 raw hash
- `received_at`

監控方式：

1. 找出所有 active intents 的 symbol set。
2. 以 symbol 為單位每 1-5 秒抓 quote。
3. 對該 symbol 的所有 active intents 批次評估。
4. 觸發時用 transaction 更新狀態、OCO sibling、trigger event 與 outbox。

不保存全部輪詢行情歷史。只持久化：

- 觸發時 quote snapshot。
- 建立時立即觸發 quote snapshot。
- V2 確認頁展示 quote。
- V2 submit quote。

Quote validation：

- `now` 必須在 regular session。
- `quote_time` 必須在 regular session。
- `bid_price <= ask_price`。
- 價格必須大於 0。
- 缺 bid / ask 可 fallback last，但必須標記。
- bid / ask / last 都不足時不評估該 symbol。
- 行情資料異常時不觸發，只記錄並等待下一筆有效 quote。

Quote 失效處理：

- 單次 quote fetch 失敗或 invalid（validation error / 缺價 / 非 session）只記錄、該輪不觸發，等待下一筆有效 quote，不改 intent 狀態。
- V1 不做 per-symbol 健康度累計、`quote_unhealthy` 自動暫停與恢復重評（連同 licensed quote 一併延後至正式營運）；因此 V1 不會因行情問題把 intent 轉 `paused_data_issue`——該狀態 V1 僅由除息 / snapshot 爭議觸發（見 §9）。
- provider 連線異常的 admin 告警見 §18。

UI 顯示：

- 最後有效行情時間。
- 最後評估價格。
- 資料源延遲標籤。

手動刷新 quote 只更新顯示，不由前端直接觸發 `TradeIntent`。

## 12. 通知

V1 notification channels：

- `in_app`
- `telegram`

V1 使用統一 `Notification` / `NotificationDelivery` 模型，不為每種通知建立獨立資料表。通知類型透過 `Notification.type` 區分。

建議 notification types：

- `price_triggered`
- `intent_invalid_for_day`
- `intent_paused_data_issue`
- `intent_paused_market_status`
- `intent_resumed`
- `market_closed_rescheduled`
- `telegram_binding_failed`
- `system_alert_user`

通知通道只在使用者設定頁管理，不在建立 TradeIntent 時選擇。V1 不支援每筆 TradeIntent notification channel override。

通知派送完全以觸發當下的 active notification settings 為準，不保存建立當下的通道 snapshot。使用者在設定頁停用 Telegram 後，後續觸發不再送 Telegram。

`in_app` 通知為必開通道，使用者不可關閉。Telegram 為可選通道；未綁定或停用 Telegram 時，系統仍透過 `in_app` 通知。

若 Telegram 因未綁定、停用或 binding 失效而未送，需記錄 delivery decision，例如 `telegram_skipped_disabled` 或 `telegram_skipped_unbound`。

Notification templates V1 先由程式碼集中管理，不做 admin 可編輯模板。每種 `Notification.type` 有固定 template，template version 需記錄在 notification metadata。Telegram 和 in-app 可共用核心 message data，但格式可不同。未來若開放後台編輯模板，需另加審核流程。

Notification 必須保存渲染後快照，避免模板改版影響歷史通知：

- `template_key`
- `template_version`
- `message_data`
- `rendered_title`
- `rendered_body`
- `rendered_at`

Telegram delivery 另存 `telegram_message_id` 與 `sent_text_hash` 或 rendered text snapshot。

通知狀態只承諾平台接受送出或站內通知建立，不承諾使用者已讀。

`NotificationDelivery`：

- `channel`
- `status = pending | sent | failed_retryable | failed_permanent | skipped`
- `sent_at`
- `error_code`

站內通知也作為正式 delivery channel。`read_at` 可用於 UI 顯示，但不影響交易意圖狀態。

`in_app` delivery 透過 outbox / notification worker 建立。若 DB 寫入失敗，outbox event 保持 pending/retry。`in_app` 成功只代表站內通知紀錄已建立，不代表使用者已讀。若使用者帳號已 disabled，delivery 標記 `skipped_account_disabled`。

通知失敗規則：

- `TradeIntent` 條件成立後仍轉 `triggered`。
- 通知失敗只針對 `NotificationDelivery` 做有限重試。
- 不因通知失敗讓 intent 回到 active。
- Telegram retryable error，例如 rate limit、timeout、5xx，最多重試 3 次並使用 exponential backoff，不停用 binding。
- Telegram permanent error，例如 bot 被封鎖、chat not found、forbidden，自動將 binding 標記為 `failed_permanent` 或 `revoked`，後續不再送 Telegram。
- Telegram 永久失敗時仍保留 `in_app` notification，並在 UI 顯示 Telegram 綁定異常。

通知文案必須標示：

- 僅通知。
- 未下單。
- 不保證成交。

除息調整時，通知顯示：

- 原始目標價。
- 現金股利調整金額。
- 調整後有效目標價。

暫停原因需分文案：

- 資料異常。
- 市場暫停。
- 休市改期。

恢復通知規則：

- 恢復後未達標：發 `intent_resumed`。
- 恢復後已達標：只發一則 `price_triggered`，metadata 帶 resumed context，文案包含恢復說明。
- 不連續發送「已恢復」與「到價」兩則使用者通知。
- Audit event 可分別記錄 `intent_resumed` 與 `intent_triggered`。

V1 不做複雜通知偏好，例如靜音標的、勿擾時間、每日摘要。只做：

- 使用者層級啟用通道。
- channel 綁定狀態。
- 失敗重試。
- 站內 read/unread。

## 13. 使用者、角色與權限

V1 不開放公開註冊。帳號由 admin 建立。

V1 不支援 team / workspace，但所有使用者資料必須有明確 owner scope：

- TradeIntent、Notification、Telegram binding 都需保存 `owner_user_id`。
- User-facing API 一律從 auth context 取得 user id，不接受 client 傳入 owner id。
- Admin API 如需查使用者資料，需使用明確 endpoint、明確權限並寫 audit。
- DB index 需考慮 `owner_user_id` + status / trading_date。
- 測試需覆蓋 cross-user access forbidden。

Admin dashboard 預設只顯示 aggregate，例如 intent count、failure count、affected symbol count。Admin 若要查看使用者完整 TradeIntent 內容，必須透過明確支援 / 稽核操作、輸入 reason，並寫 audit，包含 admin id、user id、reason、time。

流程：

1. Admin 建立 user，填 email 與角色。
2. 狀態為 `invited`。
3. 系統寄出 invitation link。
4. 使用者首次登入自行設定密碼。
5. 狀態轉 `active`。

Invitation link 規則：

- 有效 24 小時。
- 只有 admin 可重寄。
- 使用者不得自行要求重寄。
- Admin 重寄時，舊 link 立即失效。
- 重寄需寫 audit。

Password reset 規則：

- 只有 `active` 帳號可使用 password reset。
- 使用者可自行發起 password reset。
- Reset request 不透露 email 是否存在。
- Reset token 有效 30 分鐘。
- 成功重設後撤銷既有 sessions。
- Reset request 與成功 / 失敗需寫 audit。
- Reset request 需套用 rate limit。

Session / JWT 規則：

- 採短效 access JWT + DB 持久化 refresh token rotation。
- Access JWT 短效，例如 15 分鐘。
- Refresh token 存 DB，僅保存 hash，不保存明文。
- User refresh token 有效期 30 天，支援持久化登入。
- Admin refresh token 有效期 12 小時。
- 每次 refresh 需 rotation，舊 refresh token 失效。
- 偵測 refresh token reuse 時，撤銷該帳號所有 refresh sessions 並告警。
- Logout、password reset、account disabled、2FA reset 後撤銷既有 refresh tokens。
- API 每次仍需檢查 user status。
- Admin access token 可更短，例如 5-10 分鐘；高風險操作可要求二次驗證。
- Refresh token 使用 HttpOnly、Secure、SameSite=Lax 或 Strict cookie。
- Access token 短效並保存在前端記憶體；頁面刷新後透過 refresh endpoint 換發新的 access token。
- 禁止將 access token 或 refresh token 存入 localStorage。
- 若前後端跨站部署，必須明確處理 CORS 與 SameSite 設定。
- Cookie-based refresh/session 設計需實作 CSRF 防護。
- 所有 state-changing requests 需帶 CSRF token。
- 後端需檢查 `Origin` / `Referer`。
- GET 不得執行狀態變更。

V1 密碼規則採 MVP 設定：

- 最少 8 字元。
- 不強制大小寫、數字、符號組合。
- 不做弱密碼或外洩密碼檢查。
- 後續版本再加入 password strength / breached password checks。

登入與帳號啟用需有基本 rate limit：

- 同一 email 連續登入失敗 5 次，鎖定 15 分鐘。
- 同一 IP 套用全域 rate limit。
- 登入錯誤訊息不可透露 email 是否存在。
- Password reset 與 admin 重寄 invitation link 也需 rate limit。

> 「鎖定 15 分鐘」採 token-bucket（`login:email` capacity 5 / refill 5 per 15min）：達上限回 `LOGIN_LOCKED`，之後漸進回補而非固定窗一次解鎖，語意等價於「滾動 15 分鐘內最多約 5 次嘗試」。

角色：

- `user`
- `admin`

V1 先只保留 `user` / `admin` 兩種角色，`system_admin`、`support_admin`、`data_admin`、`compliance_admin` 等細分角色延後。權限檢查需集中實作，避免未來拆分角色時散落在 controller。

User 可做：

- 建立 / 取消自己的 TradeIntent。
- 綁定自己的 Telegram。
- 查看自己的通知與歷史。

Admin 可做：

- 建立 / 停用帳號。
- 維護 symbol master override。
- 維護 corporate action override。
- 維護 market calendar override。
- 查看系統健康與通知失敗摘要。
- 使用 kill switch。

Admin 必須使用 TOTP 2FA。Admin 首次啟用帳號後，必須設定 2FA 才能進入 admin 功能。2FA reset 需要另一個 admin 或離線人工流程，所有 enable / disable / reset 都必須寫 audit。V1 一般 user 不提供 2FA，但資料模型可預留未來 MFA 能力。

Admin 不可做：

- 代替使用者建立 TradeIntent。
- 任意代替使用者取消 TradeIntent，除非系統風險或資料異常流程。

停用帳號：

- 取消該使用者所有 active / scheduled intents。
- 狀態為 `cancelled_by_account_disabled`。
- 停用 Telegram binding。
- 停用 pending notification deliveries。
- 保留歷史與 audit。

重新啟用帳號時，不恢復舊 TradeIntent。

## 14. Symbol Master 與 Market Calendar

> **本節屬正式營運階段（待券商對接），非 V1。** V1 使用 seed symbol master 與固定一般盤時段（Mon–Fri 09:00–13:30 Asia/Taipei，不含假日 / 補班 / 颱風 / 半日盤）；以下描述目標行為。

### Symbol Master

V2 建立內部 symbol master，作為輸入驗證、行情、除息資料對齊共同基準。

至少包含：

- `symbol`
- `display_name`
- `market = TWSE | TPEx`
- `instrument_type = stock | etf`
- `tradable_status`
- `last_updated_at`

資料維護：

- 外部資料自動同步。
- Admin 可 override 狀態或備註。
- 策略引擎只讀內部 symbol master。

### Market Calendar Service

V2 建立獨立 `MarketCalendarService`。

能力：

- `isTradingDay`
- `getNextTradingDay`
- `getRegularSession`
- `isWithinRegularSession`
- `getDayIntentTradingDate`
- `getDayIntentExpiry`

市場行事曆：

- 使用內部 calendar table。
- 外部資料匯入。
- 允許 admin override。
- 支援半日交易與臨時休市。

開盤前臨時休市：

- scheduled day intents 自動改到下一交易日。
- 通知使用者。
- 保存原 trading date、新 trading date 與改期原因。

盤中臨時停止交易：

- active intents 轉 `paused_market_status`。
- 恢復交易後取得最新有效 quote，從恢復後繼續監控。
- 恢復交易時使用最新有效 quote 立即評估；若達標則立即 trigger，notification metadata 標記 `trigger_context = resumed_from_market_status`，文案需說明恢復交易後目前價格已符合條件。
- 當日未恢復則收盤後過期。

## 15. 建立限制與去重

V1 需要建立上限：

- 單一使用者 active / scheduled intents 上限：200。
- 單一使用者單一標的 active / scheduled intents 上限：20。

> **V1 staging 限制**：上述為產品目標上限；V1 報價源（Shioaji demo）僅 4 檔白名單 / 5 訂閱，實際可監控標的遠低於此，正式營運（待券商對接、換正式報價源）後才完整適用。

禁止同一使用者在同一交易日建立完全相同的 active / scheduled `TradeIntent`。

上限需做成 admin 可調 config，不寫死在程式碼。

重複定義至少包含：

- user
- trading date
- symbol
- strategy
- direction / position side
- effective target price
- quantity lots

若建立時條件已成立：

- 允許建立。
- 立即觸發。
- 保存 quote snapshot。
- 發送通知。
- 不進入長期 active 監控。

停利 / 停損相對目前 quote 已成立時，也允許建立並立即觸發。UI 需提示「目前價格已符合條件，建立後會立即觸發並停止監控」。OCO group 若其中一腳立即觸發，另一腳依 OCO 規則自動取消。

交易時段內建立會進入 active 的 TradeIntent 時，`CreateTradeIntent` command 需同步取得 current quote snapshot 並執行 quote validation。若 quote valid 且條件成立，同一個 command transaction 建立 intent、trigger event 與 outbox event。若 quote fetch/validation 失敗，仍建立 active intent，但不立即觸發，UI 顯示已建立並等待有效行情；後續每輪取得有效 quote 即評估（V1 不因連續失效自動轉 `paused_data_issue`）。Scheduled intent 不做立即觸發，等開盤 activation。

## 16. Command、Domain Service 與 Outbox

V1 採明確 command handler / domain service，不讓 controller 直接更新資料。

Runtime process 邊界：

- Web/API process。
- Quote evaluator worker。
- Notification worker。
- Scheduled jobs worker，負責 corporate action import、snapshot generation、expiry job。

MVP 可部署在同一台機器或同一服務群組，但需保持 process 邊界，避免 web API 流量、quote 評估與通知派送互相拖垮。

V1 quote evaluator 先採單一 worker process，以 symbol 為單位批次抓價與評估，並靠 DB transaction / unique constraints 防止重複 trigger。水平擴展與 symbol sharding 延後；未來擴展時需確保同一 symbol 同一時間只由一個 worker 評估，可採 advisory lock、distributed lock 或 shard assignment。

Scheduled jobs 必須 idempotent，並使用 DB advisory lock 或 unique execution key 防止重複執行。每個 job 建議保存 `job_name + trading_date` execution record。重跑不得產生重複 side effects；import 類 job 需產生 import report，snapshot job 需產生 snapshot version。

所有 API、command、outbox、notification 與 audit log 需貫穿 request / correlation id：

- 每個 API request 產生或接受 `X-Request-Id`。
- Command execution 保存 `request_id`。
- AuditEvent 保存 `request_id`。
- OutboxEvent 保存 `correlation_id`。
- NotificationDeliveryAttempt 保存 `correlation_id`。
- Technical log 需帶同一個 id 以利排查。

API error envelope 需統一：

```json
{
  "error": {
    "code": "INVALID_TICK_SIZE",
    "message": "目標價不符合最小升降單位",
    "details": {
      "nearestPrices": ["98.8", "98.9"]
    },
    "requestId": "..."
  }
}
```

V1 核心 error code 初始集合：

- `UNKNOWN_SYMBOL`
- `UNSUPPORTED_INSTRUMENT`
- `INVALID_TICK_SIZE`
- `TARGET_OUT_OF_DAILY_LIMIT`
- `UNSUPPORTED_CORPORATE_ACTION`
- `STALE_PRICE_CONTEXT`
- `QUOTE_UNAVAILABLE`
- `DUPLICATE_INTENT`
- `USER_INTENT_LIMIT_EXCEEDED`
- `SYMBOL_INTENT_LIMIT_EXCEEDED`
- `OCO_PRICE_RELATION_INVALID`
- `TELEGRAM_NOT_BOUND`
- `IDEMPOTENCY_KEY_REQUIRED`
- `IDEMPOTENCY_KEY_CONFLICT`
- `FORBIDDEN`
- `RATE_LIMITED`

成功 response 可包含 warnings：

```json
{
  "data": {},
  "warnings": [
    {
      "code": "QUOTE_UNAVAILABLE",
      "message": "已建立，等待有效行情"
    }
  ]
}
```

`QUOTE_UNAVAILABLE` 使用規則：

- `CreateTradeIntent`：不阻擋建立，作為 warning 回傳。
- Preview / validate：可作為 warning 回傳。
- 手動刷新 quote API：作為 error 回傳。
- Trigger evaluation：作為 internal reason 記錄，不回使用者 API error。

HTTP status mapping：

- `INVALID_TICK_SIZE`、`UNKNOWN_SYMBOL`、`UNSUPPORTED_INSTRUMENT`、`TARGET_OUT_OF_DAILY_LIMIT`、`OCO_PRICE_RELATION_INVALID`：422
- `STALE_PRICE_CONTEXT`：409
- `IDEMPOTENCY_KEY_CONFLICT`：409
- `DUPLICATE_INTENT`：409
- `IDEMPOTENCY_KEY_REQUIRED`：400
- `RATE_LIMITED`：429
- `FORBIDDEN`：403

列表 API 採 cursor-based pagination：

- 預設 page size 50。
- 最大 page size 100。
- Response 回傳 `nextCursor`。
- 不以 offset pagination 作為主要 API。
- 排序依列表語意定義，例如 history 預設 `created_at desc`，active list 依 trading date / status。

核心 command：

- `CreateTradeIntent`
- `CancelTradeIntent`
- `CancelTradeIntentGroup`
- `ActivateScheduledIntents`
- `EvaluateQuoteForSymbol`
- `TriggerTradeIntent`
- `DispatchNotification`
- `ApplyCorporateActionSnapshot`
- `MarkCorporateActionSnapshotDisputed`
- `DisableUserAccount`

Create / cancel API 需支援 idempotency key：

- `CreateTradeIntent`
- `CancelTradeIntent`
- `CancelTradeIntentGroup`

規則：

- 前端為每次 mutating submit 產生 UUID v4 idempotency key。
- Retry 同一 request 沿用原 key。
- 使用者修改 payload 後必須換新 key。
- Mutating request 缺少 idempotency key 時，後端拒絕處理。
- 同 user + same key + same payload，回傳同一結果。
- Same key + different payload，回 409。
- Idempotency key 保存 24 小時。
- 重複 cancel 回傳同一結果，不產生新的副作用。

觸發與通知採 transactional outbox：

1. Quote evaluator 判斷觸發。
2. DB transaction 更新 `TradeIntent`、OCO sibling、建立 `TriggerEvent`、寫 `OutboxEvent`。
3. Notification worker 讀 outbox。
4. 建立或更新 `NotificationDelivery`。
5. 呼叫 Telegram / in-app adapter。
6. 更新 delivery 狀態。

Outbox 語意：

- at-least-once。
- handler 必須 idempotent。
- 透過 unique constraint 降低重複副作用。
- Notification worker 可多 worker 並行，但必須透過 outbox claim / lock 控制。
- Outbox row 需有 `status`、`available_at`、`locked_by`、`locked_until`、`attempt_count`。
- Lock timeout 後可重試。
- Telegram dispatch 需有全域或 per-bot rate limit 控制。

必要約束：

- `TriggerEvent(trade_intent_id)` unique，除非未來支援多次觸發。
- `NotificationDelivery(trigger_event_id, channel)` unique。

Telegram 發送可能存在極少數 API 成功但 DB 更新失敗造成重複通知的殘餘風險。V1 接受此風險，但透過 delivery attempt 與 Telegram message id 盡量降低。

## 17. Audit Log

V1 必須建立 audit event log。

最低事件：

- `intent_created`
- `intent_activated`
- `intent_triggered`
- `intent_expired`
- `intent_cancelled`
- `notification_sent`
- `notification_failed`
- `corporate_action_adjustment_applied`
- `oco_sibling_cancelled`
- `account_invited`
- `account_activated`
- `account_disabled`
- `admin_override_applied`
- `kill_switch_enabled`
- `kill_switch_disabled`

欄位：

- `actor_type = user | system | admin`
- `actor_id`
- `occurred_at`
- `metadata`
- `request_id` 或 correlation id

核心交易資料避免使用通用 `deleted_at` 表達語意，改用明確狀態與 audit event。

## 18. Admin Monitoring 與 Kill Switch

V1 需要 admin-facing 監控與告警。

最低告警：

- quote source stale 或連線失敗。
- 某標的 quote validation 連續失敗。
- Telegram delivery failure rate 過高。
- corporate action import job 失敗。
- snapshot 未在開盤前產生。
- notification worker backlog 過高。
- trigger worker backlog 過高。

Kill switch 層級：

- 全系統停止觸發。
- 特定 symbol 停止觸發。
- 停止 Telegram 發送。
- 停止所有外部通知但保留站內通知。
- 停止 corporate action adjustment 套用。

Kill switch 必須分層控制。

停止觸發時，quote 仍可繼續抓取，用於 UI 顯示與健康監控，但不產生 `TriggerEvent`。

所有 kill switch 操作寫 audit log。

## 19. 資料保留與隱私

初始資料保留規則：

- TradeIntent history：2 年。
- NotificationDelivery：2 年。
- AuditEvent：3 年。
- Technical logs：90 天。
- Debug raw payload 若含敏感資料，保存 7-30 天或只存 hash / reference。

帳號刪除 / 匿名化：

- 帳號可停用。
- 個資欄位可匿名化，例如 email、Telegram chat id。
- TradeIntent、NotificationDelivery、AuditEvent 保留。
- user reference 改為匿名 user id 或不可逆識別。

## 20. 法務與風險揭露

V1 不做 KYC，因為不接券商、不下單、不收付款。

公司將以紙本的形式和使用者簽合約。

但系統仍必須向使用者揭露：

- 交易提醒風險揭露。
- 通知不構成投資建議。
- 通知不代表已下單。
- 通知不保證成交。
- 通知不保證即時。

## 21. UI 更新策略

V1 UI 狀態更新先用短輪詢，不上 WebSocket / SSE。

建議：

- 每 5-10 秒刷新 active intents 與通知中心。
- V2 接券商回報後再評估 SSE / WebSocket。

## 22. SLO

V1 內部 SLO：

- Quote 更新頻率：每 1-5 秒每 active symbol 評估一次。
- 觸發延遲：有效 quote 到達後 3 秒內產生 `TriggerEvent`。
- 通知延遲：`TriggerEvent` 後 10 秒內送出站內 / Telegram，排除平台故障。
- 交易時段核心監控可用性：99.5%。
- 除息 snapshot：開盤前完成，失敗需 admin 告警。

備份與災難恢復 MVP 目標：

- DB 自動備份每日一次。
- 若基礎設施支援，開啟 point-in-time recovery。
- RPO：24 小時。
- RTO：4 小時。
- 上線前至少執行一次備份還原演練。
- Audit / product data 不可只存在 log 系統。

## 23. 測試策略

V1 至少包含三層測試。

Domain unit tests：

- tick size。
- 除息調整。
- round away from trigger。
- 策略觸發方向。
- OCO 規則。
- 狀態轉移。

Command / integration tests：

- `CreateTradeIntent`。
- quote trigger transaction。
- notification outbox。
- account disable cancellation。
- corporate action snapshot disputed。
- market calendar override。

Adapter contract tests：

- quote source adapter。
- Telegram adapter。
- corporate action importer。
- symbol master importer。

少量 E2E UI：

- 建立提醒。
- 觸發後通知中心更新。
- Telegram 綁定。

## 24. 資料來源決策

V1 資料來源採以下決策。

近即時 quote：

- V1 目前使用 Shioaji quote provider（demo tier），已接上並提供 bid / ask / last / quote time；受 demo 限制：僅 4 檔白名單標的、同時最多訂閱 5 檔。
- 正式 vendor 尚未決定；決定後再以 provider adapter 串接，需評估支援 TWSE + TPEx、授權允許用於到價通知服務、資料品質、SLO 與 fallback/切換策略，並解除 demo 的標的 / 訂閱上限。
- 正式交易提醒不得依賴未授權資料來源或臨時爬蟲。

Corporate action：

- Operational primary source 採 TWSE + TPEx 除權除息公告，匯入每日除權息事件。
- MOPS 作為 authority check，用於 admin 覆核或資料衝突確認。
- 策略引擎不直接讀外部來源，只讀內部 `corporate_actions` 與盤前 snapshot。
- V1 只套用配息，配股與其他公司行動標記為 unsupported 並暫停相關提醒。

Symbol master：（自動匯入待券商對接 / 正式營運；V1 用 seed master）

- Primary source 採 TWSE ISIN code list，匯入 TWSE / TPEx 股票與 ETF。
- 停復牌、停止買賣、不可交易等狀態可由交易所公告、行情來源狀態欄位或 admin override 補充。
- 系統內部以 `symbol_master` 作為 UI autocomplete、quote 對齊與 corporate action 對齊的共同基準。

Market calendar：（production service 待券商對接 / 正式營運；V1 用固定一般盤時段，不含假日 / 補班 / 颱風 / 半日盤）

- Primary source 採 TWSE market holiday schedule。
- Regular session rule 依 TWSE trading mechanism，一般盤為 9:00-13:30。
- 使用內部 `market_calendar` table，支援 admin override 處理臨時休市、半日交易與特殊交易時段。

## 25. 待補決策

以下項目尚需後續決策：

- V1 正式營運報價源：取代 Shioaji demo（4 檔 / 5 訂閱上限），選定可支援台股全市場、授權允許到價通知的正式行情來源（預期與券商對接同期）。
- V2 券商 API 與帳戶授權模型。
- V3 自動下單的價格保護與授權模型。
