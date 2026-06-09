# BE-V1-07：Take-Profit / Stop-Loss Strategy Semantics

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：30h
- 依賴：BE-V0.5-07, BE-V0.5-09
- 交付版本：V1

## 背景

V0.5 只支援 `buy_price_alert` / `sell_price_alert`。V1 補上持倉型策略：`take_profit_alert` / `stop_loss_alert`。

`docs/domain-spec.md` §4：

- 停利 / 停損屬於持倉型策略，必填 `position_side = long | short`。
- `order_side` 由 `position_side` 推導，不允許 user 手動指定。
- V1 允許 user 聲明空單持倉，但不驗證實際持倉。

推導規則：

| Strategy            | position_side | 觸發方向（V0.5-09 buy/sell 規則沿用） |
| ------------------- | ------------- | ------------------------------------- |
| `take_profit_alert` | `long`        | `bid >= target`（賣出方向）           |
| `take_profit_alert` | `short`       | `ask <= target`（買回方向）           |
| `stop_loss_alert`   | `long`        | `bid <= target`（賣出方向）           |
| `stop_loss_alert`   | `short`       | `ask >= target`（買回方向）           |

`buy_price_alert` / `sell_price_alert` 仍 V1 保留，不變。

## 目標

- 擴 `trade_intents.strategy` enum：加 `take_profit_alert` / `stop_loss_alert`。
- 新增 `trade_intents.position_side` 欄位。
- 擴 `QuoteEvaluator` 支援新 strategy（沿用 ask/bid 規則 + last fallback）。
- 擴 `CreateTradeIntent` command：strategy 是停利/停損時必填 position_side。
- Notification template 補：`price_triggered` for take_profit/stop_loss（rendered body 區分 position_side 文案）。

## 非目標

- 不做 OCO bracket（BE-V1-08）。
- 不做 trailing（V1.5）。
- 不做 position 持倉驗證（V2）。
- 不做 short entry（domain-spec §4）。

## DB Schema

### `trade_intents`

- 加 `position_side text null check (position_side in ('long', 'short'))`。
- 加 `order_side text not null check (order_side in ('buy', 'sell'))`（derived，由 command 寫入）。
- check constraint：
  - `strategy in ('take_profit_alert', 'stop_loss_alert')` → `position_side is not null`。
  - `strategy in ('buy_price_alert', 'sell_price_alert')` → `position_side is null`。
- `trigger_reference_price_type` enum 加 `'ask_fallback'`（停利空單 / 停損空單 fallback 時用 ask；symmetric to last_fallback）。

### Duplicate check

V0.5 duplicate partial unique index 改加入 `position_side`，避免 long take_profit 與 short take_profit 視為同一筆。

新 duplicate key：

```
(owner_user_id, symbol, strategy, position_side,
 effective_target_price, quantity_lots, trading_date, status)
where status in ('scheduled', 'active', 'paused_data_issue', 'paused_market_status')
```

## Domain & Evaluator

### `app/domain/strategy.py`

```python
def derive_order_side(strategy: Strategy, position_side: PositionSide | None) -> OrderSide:
    match (strategy, position_side):
        case ('buy_price_alert', None): return 'buy'
        case ('sell_price_alert', None): return 'sell'
        case ('take_profit_alert', 'long'): return 'sell'
        case ('take_profit_alert', 'short'): return 'buy'
        case ('stop_loss_alert', 'long'): return 'sell'
        case ('stop_loss_alert', 'short'): return 'buy'
        case _: raise InvalidStrategyCombination(...)
```

`CreateTradeIntent` command 必呼叫上面 helper 寫入 `order_side`，禁止接受 client `order_side`。

### Evaluator

- 比較方向：
  - `take_profit_alert` long → `bid >= target`，fallback last。
  - `take_profit_alert` short → `ask <= target`，fallback last。
  - `stop_loss_alert` long → `bid <= target`，fallback last。
  - `stop_loss_alert` short → `ask >= target`，fallback last。
- `trigger_reference_price_type`：
  - `take_profit_alert` long → `bid`（fallback `last_fallback`）。
  - `take_profit_alert` short → `ask`（fallback `last_fallback`）。
  - `stop_loss_alert` long → `bid`（fallback `last_fallback`）。
  - `stop_loss_alert` short → `ask`（fallback `last_fallback`）。

## API

### `POST /trade-intents` Schema 擴充

新增 fields：

- `positionSide: 'long' | 'short' | null`
- `strategy` 可為 `'buy_price_alert' | 'sell_price_alert' | 'take_profit_alert' | 'stop_loss_alert'`

Validation：

- `strategy` 是停利 / 停損 → `positionSide` 必填。
- `strategy` 是 buy/sell → `positionSide` 必為 null。
- 違反 → `INVALID_STRATEGY_COMBINATION` 422。

Response：

- 回傳 `positionSide`、`orderSide`（derived）。

### `GET /trade-intents` / `/{id}`

- Response 加 `positionSide`、`orderSide`。

## Error Codes

新增：

- `INVALID_STRATEGY_COMBINATION`：422。
- `POSITION_SIDE_REQUIRED`：422（停利 / 停損未填 position_side）。

## 驗收條件

- [ ] `trade_intents` schema migration 可 upgrade / downgrade，既有 row 補 `order_side` 預設值。
- [ ] `CreateTradeIntent` 對 4 種策略 × 2 種 position_side 組合（適用時）正確 derive `order_side`。
- [ ] Evaluator 對 long take_profit / short take_profit / long stop_loss / short stop_loss 各自正確觸發。
- [ ] Duplicate check 對 long / short 不誤判為同筆。
- [ ] Notification body 對 4 種策略各有正確文案（明示停利或停損 + position_side）。
- [ ] BE-V0.5-09 既有 buy/sell tests 全綠。

## 測試要求

- Unit：`derive_order_side` 對所有合法 / 不合法組合的對應 / raise。
- Unit：Evaluator 對 4 種新策略 × ask/bid/last fallback 12 case。
- Integration：`POST /trade-intents` 對 `take_profit_alert` + `long` 成功；對 `take_profit_alert` 無 position_side → 422。
- Integration：long stop_loss bid = target → triggered。
- Integration：short take_profit ask <= target → triggered。
- Integration：duplicate long vs short take_profit 不衝突。

## 工程注意事項

- `order_side` 在 schema 上是 non-null，但既有 V0.5 資料沒這欄位；migration 必須 backfill：
  - `strategy = 'buy_price_alert'` → `order_side = 'buy'`
  - `strategy = 'sell_price_alert'` → `order_side = 'sell'`
- 不要把 `position_side` derive 邏輯散落在 controller；命令處理層集中。
- BE-V1-08 OCO 會建立 take_profit + stop_loss 配對 intent，本工單需確保 single-intent 路徑乾淨，避免 OCO 工單再 refactor。
- Notification template 文案：停利強調 +profit 描述；停損強調 risk 描述；但仍須帶「僅通知、未下單」聲明。
