# BE-V0.5-16：移動出場 Alert（Trailing Stop）

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：22h
- 依賴：BE-V0.5-09, BE-V0.5-13
- 交付版本：V0.5

## 背景

V0.5 既有 strategy 都是「靜態 target price」：建立時填一個 target，evaluator 比對 quote 就觸發。產品方需要「移動出場」單，跟著 watermark 動態調整觸發價：

- 設定 5% 移動出場（多單），當日最高價 = 100 → 觸發價 = 100 × (1 − 5%) = 95，價格 < 95 即觸發。
- 也支援固定金額：5 元移動出場（多單），最高價 100 → 觸發價 95，價格 < 95 即觸發。
- 空單對稱：5% 移動出場（空單），當日最低 = 100 → 觸發價 100 × (1 + 5%) = 105，價格 > 105 即觸發。

**「移動出場」涵蓋傳統說法的「移動停利」與「移動停損」**（兩者在這套機制下是同一種單）：

- 兩者都跟著 favorable 方向的 watermark（多單高水位 / 空單低水位）動態調整觸發價，反向達標時觸發。
- 差別只在使用者進場成本：若觸發價已高於進場成本，行為上是「鎖定獲利」（傳統說法的移動停利）；若觸發價仍在進場成本附近或之下，行為上是「限制虧損」（傳統說法的移動停損）。
- V0.5 系統 notify-only 不知道使用者進場成本，無法（也不需要）在 strategy 層分流。
- 產業標準術語統稱 "Trailing Stop"。
- **本工單統一中文稱呼為「移動出場」**，避免使用者糾結於「停利／停損」之分；後端、API、通知文案、handoff 文件一律使用「移動出場」。Code identifier 仍保留 `trailing_stop_alert`（業界標準英文）。

因此本工單只引入單一 strategy：`trailing_stop_alert`。前端 UI 可用「移動出場」單一入口，後端不分流。

domain-spec §1 原把 `trailing_take_profit_alert` / `trailing_stop_loss_alert` 兩個分開規劃在 V1.5（為了預留 take-profit 「啟動門檻」這類僅停利語意的擴充）。本工單前移到 V0.5 + 合併為單一 strategy，是 V0.5 階段的刻意取捨：

- V0.5 / V1 範圍內不做啟動門檻、不做活化條件、不做 OCO bracket；單一 strategy 足以涵蓋。
- 若未來 V1.5+ 需要拆分（例如停利需要先達到 profit 才啟動 trail），再以 schema 加 `activation_threshold` 欄位 + 視情況另開 strategy enum，由 V1.5 工單處理。

## 目標

- 擴 `trade_intents.strategy` enum：加單一 `trailing_stop_alert`。
- 新增 trailing 專屬欄位：`position_side`（必填 long/short）、`trail_mode`、`trail_value`、`watermark_high`、`watermark_low`、`dynamic_trigger_price`、`watermark_updated_at`。
- Evaluator 擴：**每筆 valid quote 都需要更新 watermark + 重算 dynamic_trigger_price**，不只在觸發條件時。
- Trigger 條件：用 dynamic_trigger_price，與既有 ask/bid 比較。
- Session start watermark reset：每個 trading_date 第一筆 valid quote 起重新累積（V0.5 只支援 day intent，故 watermark 不跨日延續）。
- 通知 template：移動出場觸發文案，標題與 body 需明示「移動出場」、position_side、watermark 峰值、實際觸發價。

## 非目標

- 不做 take-profit activation threshold（「達到 X% 獲利後才啟動 trail」這類僅停利語意），所以也不拆 take-profit / stop-loss 兩個 strategy。V1.5 / V2 視需求再評估。
- 不做 OCO bracket（V1-08）。
- 不做跨日 watermark（domain-spec §1：V1.5 規定 trailing 只支援 day）。
- 不模擬部分成交（V2 / 與 BE-V0.5-15 同理）。
- 不做盤中 `position_side` 變更（建立後 immutable）。
- 不做 watermark 在 paused_data_issue 期間的補捉策略（V1-05 quote unhealthy 才完整處理；V0.5 evaluator 對 invalid quote 不更新 watermark）。

## DB Schema

### `trade_intents` 變動

新欄位：

- `position_side text null check (position_side in ('long', 'short'))`
  - 對 `trailing_stop_alert` 必填；對 buy/sell/limit_*_order strategy 必為 null。
  - 與 BE-V1-07 take_profit/stop_loss 規格一致，先在 V0.5 引入；V1-07 接手時不需 schema 再變。
- `trail_mode text null check (trail_mode in ('percentage', 'fixed_amount'))`
  - 對 `trailing_stop_alert` 必填；其他 strategy 必為 null。
- `trail_value numeric(9, 4) null`
  - 對 `trailing_stop_alert` 必填；其他 strategy 必為 null。
  - `trail_mode = percentage` 時，單位是百分比（e.g. `5.0` 代表 5%），值域 (0, 50]。
  - `trail_mode = fixed_amount` 時，單位是 TWD 元，值域 > 0，且 tick-size 合法（沿用 BE-V0.5-05 tick service）。
- `watermark_high numeric(9, 4) null`
  - long trailing：當日 high watermark；short trailing：null。
  - 沒收到任何 valid quote 前 null。
- `watermark_low numeric(9, 4) null`
  - short trailing：當日 low watermark；long trailing：null。
- `dynamic_trigger_price numeric(9, 4) null`
  - Evaluator 每筆 valid quote 重算寫入。
  - long trailing：`watermark_high × (1 − trail_value/100)` 或 `watermark_high − trail_value`，再依「tick round away from trigger」處理。
  - short trailing：`watermark_low × (1 + trail_value/100)` 或 `watermark_low + trail_value`，tick 對齊。
- `watermark_updated_at timestamptz null`
  - Evaluator 更新 watermark 時寫入。

Check constraints：

- `strategy = 'trailing_stop_alert'` → `position_side`, `trail_mode`, `trail_value` 都必須 non-null。
- `strategy != 'trailing_stop_alert'` → 上述三欄都必須 null。
- `position_side = 'long'` → `watermark_low` 必為 null（watermark_high 可為 null 或 value）。
- `position_side = 'short'` → `watermark_high` 必為 null。

### `trigger_events` 變動

新欄位：

- `watermark_at_trigger numeric(9, 4) null`
  - `trailing_stop_alert` trigger 時寫入 watermark_high 或 watermark_low。
  - 其他 strategy 必為 null。
- `dynamic_trigger_price_at_trigger numeric(9, 4) null`
  - `trailing_stop_alert` trigger 時寫入觸發當下的 dynamic price。

### Duplicate check

V0.5-07 partial unique index 不含 trailing 專屬欄位。本工單 duplicate key 擴：

- `(owner_user_id, symbol, strategy, position_side, target_price_effective, trail_mode, trail_value, quantity_lots, trading_date, status)`
- 對 `trailing_stop_alert`，`target_price_effective` 為 null（無原始 target），duplicate 比對以 `trail_mode + trail_value + position_side` 為主。
- 既有非 trailing strategy 不受影響。

Migration 改寫 partial unique index 包含上述欄位（PostgreSQL 17 支援 `unique nulls not distinct`，把 null 視為相等，避免 long/short 同條件 duplicate 因 null 不等被誤放行）。

### `trade_intents.strategy` check constraint 擴充

migration 改 check constraint，新增 `trailing_stop_alert`。

`order_side` derive（沿用 BE-V0.5-15 不 promote schema column 的決定）：

- `trailing_stop_alert` long → sell（多單觸發停損 = 賣出方向）
- `trailing_stop_alert` short → buy（空單觸發停損 = 買回方向）

## Strategy 語意

### Watermark 規則（每筆 valid quote 都跑）

對 long trailing：

```
reference_price = last_price if last_price is not None else mid(bid, ask)
if watermark_high is None or reference_price > watermark_high:
    watermark_high = reference_price
    dynamic_trigger_price = round_to_tick_away_from_trigger(
        watermark_high * (1 - trail_value/100)  if trail_mode == 'percentage'
        else watermark_high - trail_value,
        direction='down'  # 對 long 而言，trigger 在 watermark 下方，要往遠離 trigger 的方向 round = 往下
    )
    watermark_updated_at = now()
```

對 short trailing：

```
reference_price = last_price if last_price is not None else mid(bid, ask)
if watermark_low is None or reference_price < watermark_low:
    watermark_low = reference_price
    dynamic_trigger_price = round_to_tick_away_from_trigger(
        watermark_low * (1 + trail_value/100)  if trail_mode == 'percentage'
        else watermark_low + trail_value,
        direction='up'
    )
    watermark_updated_at = now()
```

注意：

- `reference_price` 採 last 為主、mid(bid, ask) 為輔；缺三者其一不可的情境（bid/ask/last 全缺）沿用 BE-V0.5-09 quote validation，不更新 watermark。
- `round_to_tick_away_from_trigger` 是 BE-V0.5-05 tick service 的 wrapper：對 long 往下、對 short 往上 round，避免 rounding 讓 trigger 更容易發生。
- `dynamic_trigger_price` 必須持久化到 `trade_intents`，否則 evaluator 重啟後遺失。
- Watermark 更新即使在 trigger 不成立的 quote 也要寫；evaluator 每 quote 都跑 update + check。
- Watermark 與 `dynamic_trigger_price` 在 `paused_data_issue`（V1-05）/ `paused_market_status`（V1-04）期間 freeze；恢復後從最新有效 quote 繼續，不回放暫停期間行情。**V0.5 沒有 paused 狀態**，本段為 V1 銜接 placeholder。

### Trigger 條件

對 long trailing：

- 觸發：`bid_price <= dynamic_trigger_price`（賣出方向）。
- Fallback：bid 缺失且 last 存在 → `last_price <= dynamic_trigger_price`，標 `fallback_used = true`、`trigger_reference_price_type = last_fallback`。
- `trigger_reference_price_type` 主要值：`bid`、`last_fallback`（與既有 sell_price_alert 一致）。

對 short trailing：

- 觸發：`ask_price >= dynamic_trigger_price`（買回方向）。
- Fallback：ask 缺失且 last 存在 → `last_price >= dynamic_trigger_price`，fallback 標記。
- `trigger_reference_price_type` 主要值：`ask`、`last_fallback`。

### Trigger 行為

同 BE-V0.5-09 trigger transaction：

1. Lock / status guard `active`。
2. 寫 `trigger_events`，包含：
   - 既有欄位（quote_snapshot / trigger_price / fallback_used / triggered_at）。
   - `watermark_at_trigger` = trigger 當下 watermark_high 或 watermark_low。
   - `dynamic_trigger_price_at_trigger` = trigger 當下 dynamic_trigger_price。
   - `target_price_effective` = `dynamic_trigger_price_at_trigger`（讓既有 evaluator infra 不破，但欄位語意上對 trailing 與其他 strategy 有差，文件需註記）。
3. 更新 intent status `triggered`、`triggered_at`、`filled_quantity_lots = quantity_lots`（沿用 V0.5-15 decision）。
4. 建立 notification，type = `trailing_stop_triggered`。

### 即時觸發

盤中建立時的 immediate trigger 路徑（BE-V0.5-07）需處理 trailing：

- Create transaction 內取 current quote。
- 用 quote.last（或 mid bid/ask）初始化 watermark。
- 算 dynamic_trigger_price。
- 比較 quote.bid（或 ask for short）vs dynamic_trigger_price。
- 若達標：同 transaction 完成 trigger。
- 否則：寫入初始 watermark + dynamic_trigger_price，intent 進 active。

注意 watermark 初始化邊界 case：若建立當下 quote 已達 limit，dynamic_trigger_price 仍會以該 quote 為初始 watermark，因此建立 → 立即觸發是合理的（user 等同設了一個已成立的 trailing）。

### Session 與 Trading Date

- V0.5 只支援 day intent；trailing 與其他 strategy 一致，收盤後未觸發 → 留到 BE-V1-04 expire job（V0.5 無 expire job，狀態保持 `active` 直到下日；目前 V0.5 也沒處理）。
- Watermark 不跨日延續：每個 trading_date 第一筆 valid quote 重新初始化（V0.5 day intent 都是當日建立、當日有效，本欄位實作為「watermark_high / low 為 null 時用首筆 quote 初始化」，自然滿足）。
- 盤外 evaluator 不執行（同 BE-V0.5-09）。

## Notification

新增 type：`trailing_stop_triggered`。

Template 要求（in_app body 與 title）：

- Title：`2330 移動出場已觸發`（不分多空，body 內細分）。
- Body 必含：
  - strategy：移動出場 + position_side（多單／空單）。
  - `trail_mode` 與 `trail_value`（e.g. `移動幅度 5%` / `移動幅度 NT$5.0`）。
  - watermark 峰值（多單顯示「今日最高價」、空單顯示「今日最低價」）。
  - dynamic_trigger_price（觸發價）。
  - 實際 trigger_price（quote bid / ask / last）。
  - quote time。
  - 「僅通知、未下單、不保證成交」。

範例（多單）：

```
2330 移動出場已觸發

策略：移動出場（多單）
移動幅度：5%（百分比模式）
今日最高價：100.00
觸發價（最高 × 95%）：95.00
實際觸發成交價：94.80（bid）
報價時間：2026-05-25 13:25:12

僅通知、未下單、不保證成交。
```

範例（空單）：

```
2330 移動出場已觸發

策略：移動出場（空單）
移動幅度：NT$5.0（固定金額模式）
今日最低價：100.00
觸發價（最低 + 5）：105.00
實際觸發成交價：105.20（ask）
報價時間：2026-05-25 13:25:12

僅通知、未下單、不保證成交。
```

> 文案統一使用「移動出場」一詞涵蓋傳統說法的停利／停損兩種使用情境；前端若需要在 UI 額外顯示「鎖定獲利」或「限制虧損」等語意標籤，可由 client 端依 dynamic_trigger_price 與使用者輸入的「參考進場價」（V0.5 後端不持有此資訊）自行決定，但 strategy 主名稱仍應呈現為「移動出場」。

## API

### `POST /trade-intents`（既有，沿用 BE-V0.5-15 重構後的 Pydantic discriminated union schema）

本工單在 BE-V0.5-15 已把 `POST /trade-intents` 改為 discriminated union 的基礎上，新增 `trailing_stop_alert` 子 schema。子 schema 設 `extra=forbid`，與其他 strategy 完全不共用欄位。

Body 範例（多單百分比）：

```json
{
  "strategy": "trailing_stop_alert",
  "symbol": "2330",
  "positionSide": "long",
  "quantityLots": 1,
  "trailMode": "percentage",
  "trailValue": "5.0"
}
```

Body 範例（空單固定金額）：

```json
{
  "strategy": "trailing_stop_alert",
  "symbol": "2330",
  "positionSide": "short",
  "quantityLots": 1,
  "trailMode": "fixed_amount",
  "trailValue": "5.0"
}
```

Schema 規則（由 Pydantic 強制，無需 controller 額外 validation）：

- `strategy = 'trailing_stop_alert'` 子 schema：
  - 必填：`symbol`, `quantityLots`, `positionSide: Literal['long', 'short']`, `trailMode: Literal['percentage', 'fixed_amount']`, `trailValue: Decimal`
  - 不含：`targetPrice`、`transactionMode`、`notificationMode`（`extra=forbid` 拒絕）
  - `trailValue` percentage 模式：`Field(gt=0, le=50)`，Pydantic 自動越界檢查
  - `trailValue` fixed_amount 模式：`Field(gt=0)`，tick-size 由 command 層檢查（沿用既有 `INVALID_TICK_SIZE`，envelope `details` 帶 `field: trailValue` 與 `nearestPrices`）
  - `position_side` / `trail_mode` 兩欄之間互不依賴（由欄位本身 Literal 約束即可）
- 其他既有 strategy 子 schema 不含 `positionSide` / `trailMode` / `trailValue`；client 多帶會被 `extra=forbid` 拒絕
- Schema 違規一律 422 `VALIDATION_ERROR`，envelope `details.loc` 指明錯誤欄位
- 跨 strategy 欄位錯放（如 `buy_price_alert` body 帶 `trailValue`、`trailing_stop_alert` body 帶 `targetPrice`）由 discriminated union 自然拒絕，不需 strategy-specific error code

Response 加：

```json
{
  "data": {
    "positionSide": "long",
    "trailMode": "percentage",
    "trailValue": "5.0",
    "watermarkHigh": null,
    "watermarkLow": null,
    "dynamicTriggerPrice": null,
    "watermarkUpdatedAt": null,
    "filledQuantityLots": 0
  }
}
```

### `GET /trade-intents` / `/{id}`

- Response 加上面 6 個欄位 + 既有欄位。
- watermark 與 dynamic 在 evaluator 更新後可被 user list 觀察到。

### `POST /trade-intents/batch`（BE-V0.5-14）

- 支援 `trailing_stop_alert` row：每 row body 與上方相同。
- 與 buy/sell/limit_*_order row 同 batch 內可混合。
- 不放寬 20 row 上限。

## Error Codes

無新增。

Schema 組合錯誤（缺 `positionSide` / `trailMode` / `trailValue`、多帶 `targetPrice`、`trailValue` 越界、`positionSide` 不在 `('long', 'short')`、`trailMode` 不在 `('percentage', 'fixed_amount')`）一律走既有 `VALIDATION_ERROR` 422，envelope `details.loc` / `details.msg` 由 Pydantic 自動填入。

`INVALID_TICK_SIZE` 沿用：fixed_amount 模式下 `trailValue` 不合 tick 仍由 command 層拒絕，envelope `details` 帶 `field: trailValue` 與 `nearestPrices`。

刻意不為 schema policing 新增 `TARGET_PRICE_NOT_ALLOWED_FOR_TRAILING` / `TRAILING_FIELDS_NOT_ALLOWED` / `POSITION_SIDE_REQUIRED` / `TRAIL_VALUE_OUT_OF_RANGE`，避免 error code 隨 strategy 數量線性增長。前端若需要呈現「特定 strategy 缺欄位」的提示，由 `VALIDATION_ERROR.details.loc` 配合 strategy 名稱即可，不依賴後端區分 code。

## Configuration

無新 env。Evaluator hot path 每 quote 都 update watermark + write `trade_intents`；若效能成問題，後續可加：

- `TRAILING_WATERMARK_WRITE_BATCH_INTERVAL_MS`（V1-05 可考慮 batch 更新）。

本工單不引入此配置。

## 驗收條件

- [ ] migration 可 upgrade / downgrade；既有非 trailing row 補預設值 null。
- [ ] migration 改 duplicate partial unique index 包含 `position_side` / `trail_mode` / `trail_value`，既有 buy/sell duplicate 行為不變。
- [ ] `POST /trade-intents` 在 BE-V0.5-15 discriminated union schema 之上加入 `trailing_stop_alert` 子 schema，`extra=forbid`。
- [ ] `POST /trade-intents` 接受 `trailing_stop_alert`；缺 `positionSide` / `trailMode` / `trailValue` 或多帶 `targetPrice` 一律回 422 `VALIDATION_ERROR`，`details.loc` 指明欄位。
- [ ] 其他既有 strategy body 多帶 `trailValue` / `positionSide` 由 `extra=forbid` 拒絕（regression）。
- [ ] 不存在 `TARGET_PRICE_NOT_ALLOWED_FOR_TRAILING` / `TRAILING_FIELDS_NOT_ALLOWED` / `POSITION_SIDE_REQUIRED` / `TRAIL_VALUE_OUT_OF_RANGE` 任何 error code（grep 全 repo 命中 0）。
- [ ] `trailMode = percentage` 且 `trailValue = 60` → Pydantic 422，`details.loc = ['trailValue']`。
- [ ] `trailMode = fixed_amount` 且 `trailValue = 0.07`（不合 tick） → 422 `INVALID_TICK_SIZE`，`details.field = 'trailValue'`。
- [ ] `trail_mode = percentage` + `trail_value = 5.0` 建立 long trailing → 收到第一筆 quote 後 watermark_high 寫入、dynamic_trigger_price = watermark_high × 0.95（tick round 後）。
- [ ] 收到後續更高 quote → watermark_high 上修、dynamic_trigger_price 跟著更新。
- [ ] 收到後續較低 quote → watermark_high 不變、dynamic_trigger_price 不變。
- [ ] 當 bid <= dynamic_trigger_price → trigger，notification type = `trailing_stop_triggered`，body 含 watermark 與動態觸發價。
- [ ] short trailing 對稱：watermark_low、ask >= dynamic_trigger_price → trigger。
- [ ] Fixed_amount mode：dynamic_trigger_price = watermark ± trail_value，tick 對齊。
- [ ] 即時觸發路徑：盤中建立時若 current quote 已達標，create transaction 內完成 trigger。
- [ ] BE-V0.5-12 既有 test suite 全綠（regression）。
- [ ] BE-V0.5-15 limit order 行為不受影響。

## 測試要求

- Unit：`trailValue` percentage 邊界 (0, 50] 由 Pydantic `Field(gt=0, le=50)` 驗證；越界 → `VALIDATION_ERROR` + `details.loc = ['trailValue']`。
- Unit：`trailValue` fixed_amount tick-size 由 command 層拒絕 → `INVALID_TICK_SIZE` + `details.field = 'trailValue'`。
- Unit：`watermark + dynamic_trigger_price` 計算（long / short × percentage / fixed × 5 個 quote 序列）。
- Unit：`round_to_tick_away_from_trigger`（long 往下、short 往上）。
- Unit：`reference_price` 取值優先順序（last → mid(bid, ask)）。
- Unit：discriminated union 解析：`trailing_stop_alert` body 缺 `positionSide` → Pydantic 422，`details.loc = ['positionSide']`。
- Unit：`trailing_stop_alert` body 帶 `targetPrice` → Pydantic 422（extra forbidden），`details.loc = ['targetPrice']`。
- Unit：`buy_price_alert` body 帶 `trailValue` → Pydantic 422（extra forbidden），`details.loc = ['trailValue']`。
- Unit：strategy → order_side derive（long → sell、short → buy）。
- Integration：long trailing percentage 5% → quote 序列 90 / 100 / 95 / 94 →
  - 90：watermark = 90，dynamic = 85.5（tick 對齊後）。
  - 100：watermark = 100，dynamic = 95.0。
  - 95：watermark 不變 100，dynamic 不變 95.0。
  - 94：bid 94 < 95 → trigger，trigger_events 寫入 watermark_at_trigger = 100、dynamic_at_trigger = 95.0。
- Integration：short trailing fixed_amount 5 → quote 序列 110 / 100 / 105 / 106 →
  - 110：watermark_low = 110，dynamic = 115。
  - 100：watermark_low = 100，dynamic = 105。
  - 105：watermark_low 不變，dynamic 不變。
  - 106：ask 106 > 105 → trigger。
- Integration：bid 缺失、last fallback 路徑各方向。
- Integration：盤中建立已成立 immediate trigger。
- Integration：duplicate trailing intent 撞 `DUPLICATE_INTENT`。
- Integration：trailing 與 buy_price_alert 同條件並存不互撞。
- Integration：long trailing 與 short trailing 同 symbol / 同 trail_value 同時存在不互撞（duplicate key 由 position_side 區分）。
- Integration：BE-V0.5-09 + V0.5-15 既有 trigger 行為不受 schema migration 影響。

## 工程注意事項

- **單一 strategy + 統一中文名稱「移動出場」的取捨**：機制相同（watermark + dynamic trigger），只在使用者進場成本下才有「停利 vs 停損」的語意差，V0.5 後端不知道也不需要知道進場成本。中文統一以「移動出場」對使用者呈現，避免「停利」「停損」兩個詞造成的理解負擔；前端 UI 若需在 detail page 額外呈現「鎖定獲利」或「限制虧損」副標，由 client 端用使用者輸入的參考進場價自行判斷，但主標題與 strategy 名稱一律「移動出場」。V1.5 / V2 若拆 take-profit `activation_threshold`（「達到 X% 獲利才啟動 trail」）再分流為新 strategy；屆時可能需要重新引入區分性中文名稱。
- **Evaluator 寫入頻率**：每筆 valid quote 都 `UPDATE trade_intents SET watermark_*, dynamic_trigger_price, watermark_updated_at` 一次。BE-V0.5-13 Shioaji demo 5 檔配額下不會成為 bottleneck；V1-05 licensed provider 接上後若每秒多筆 quote × 多筆 intent 形成壓力，再考慮（a）watermark in-memory + 定期 flush，或（b）改寫獨立 `trailing_watermarks` table。本工單不預先優化。
- Watermark column 寫入 race condition：同 symbol 多筆 trailing intent 共用 quote callback，建議在 evaluator 內以 `for intent in active_trailing_intents: update + check trigger` 序列處理（V0.5 單 process），不要 fan-out。
- `dynamic_trigger_price` 必須持久化：evaluator 重啟（process crash）後不能因為 in-memory watermark 遺失就讓 trigger 條件 reset。
- `round_to_tick_away_from_trigger` 與 V1-09 round-away-from-trigger 是同一概念但本工單先在 V0.5 落地一份簡化版（不含 corporate action adjustment）；V1-09 接手時把兩處統一為 BE-V0.5-05 tick service 的 method。
- `target_price_effective` 欄位對 trailing strategy 在 `trigger_events` 寫入時設為 `dynamic_trigger_price_at_trigger`，避免擴充 evaluator infra；但這個語意 overload 必須在 BE-V0.5-11 handoff 文件明寫，否則 reader 容易誤會。
- Watermark reset 跨日邏輯：V0.5 day intent 一次完整生命週期都在當日，watermark 從 null 開始即正確；不需另寫 daily reset job。V1-04 加上 scheduled activation 後，跨日恢復 scheduled → active 時 watermark 仍為 null，evaluator 自然從首筆 quote 初始化。
- `position_side` 欄位本工單與 BE-V1-07 take-profit/stop-loss 共用 schema；V1-07 接手時不需 migration，只擴 strategy enum 與 derive `order_side`。
- duplicate partial unique index 用 `unique nulls not distinct`（PostgreSQL 15+）；專案使用 17，可直接用，避免 long/short 同價條件 duplicate 因 null 不等被誤放行。
- 對 BE-V0.5-13 subscription quota：trailing intent 與其他 strategy 一樣占一個 symbol 訂閱配額，behavior 完全沿用。
- 對 BE-V0.5-14 batch：trailing row 與其他 row 同 batch 內可建，validation 由 `CreateTradeIntent` 共用 callable 涵蓋。
- 不要為了「未來 V1.5 補活化門檻」而在本工單預先加 `activation_threshold` 欄位；V1.5 / V2 工單接手時自然處理。
- 「依當天最高 / 最低」的 quote 取值：用 last_price 為主、mid(bid, ask) 為輔，與真實券商「以成交價判定」對齊；不採用 mid 為主以避免 bid-ask spread 太大造成 watermark 偏移。
