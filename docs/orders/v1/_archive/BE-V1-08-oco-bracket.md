# BE-V1-08：OCO Bracket Alert Group Behavior

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：35h
- 依賴：BE-V1-06, BE-V1-07
- 交付版本：V1

## 背景

V1 支援停利 + 停損的 OCO bracket alert group（domain-spec §5）。底層模型：

- `TradeIntentGroup` (`group_type = bracket_alert`)
- 兩筆子 `TradeIntent`：`take_profit_alert` + `stop_loss_alert`，同 `position_side`。

OCO 規則：

- 任一子 intent 觸發後，另一筆自動取消（`cancelled_by_oco`）。
- 通知失敗不回滾。
- 使用者取消其中一腳 → 取消整組。
- 不允許只取消一腳。
- 多單：停利價必須大於停損價。
- 空單：停利價必須小於停損價。
- 兩腳同 quote 同時成立 → 都標 `ambiguous_trigger`、發 system notification + admin warning。

## 目標

- 新增 `trade_intent_groups` table。
- `trade_intents` 加 `group_id` + `group_role`。
- 新增 `CreateTradeIntentBracket` command：兩腳同 transaction 建立。
- 新增 `CancelTradeIntentGroup` command。
- 擴 trigger transaction：觸發其中一腳時，同 transaction 取消 sibling 並寫 outbox event。
- 新增 `ambiguous_trigger` status 處理。
- Notification templates：`bracket_sibling_cancelled` / `bracket_ambiguous_trigger`。

## 非目標

- 不做 trailing OCO（V1.5）。
- 不做 multi-leg（>2 腳）group。
- 不做 OCO group history 獨立 list view（沿用 trade intent list filter）。
- 不做 admin 代取消（domain-spec §13）。

## DB Schema

### `trade_intent_groups`

- `id uuid primary key`
- `owner_user_id uuid not null references users(id)`
- `group_type text not null check (group_type in ('bracket_alert'))`
- `symbol text not null references symbols(symbol)`
- `position_side text not null check (position_side in ('long', 'short'))`
- `status text not null check (status in ('active', 'triggered', 'cancelled', 'ambiguous_trigger', 'expired', 'invalid_for_day', 'cancelled_by_account_disabled'))`
- `trading_date date not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`
- `terminal_at timestamptz null`
- `terminal_reason text null`

Indexes：`(owner_user_id, status, trading_date)`。

### `trade_intents` 變動

- 加 `group_id uuid null references trade_intent_groups(id)`。
- 加 `group_role text null check (group_role in ('take_profit', 'stop_loss'))`。
- 加 `cancelled_by_oco_sibling_id uuid null references trade_intents(id)`。
- check constraint：`group_id is not null` ↔ `group_role is not null`。
- check constraint：`group_role = 'take_profit'` ↔ `strategy = 'take_profit_alert'`，`group_role = 'stop_loss'` ↔ `strategy = 'stop_loss_alert'`。

### `trade_intents.status` 加 `ambiguous_trigger`

- migration 改 check constraint。

## Commands

### `CreateTradeIntentBracket`

Inputs：

- `ownerUserId`（context）
- `symbol`
- `positionSide`
- `quantityLots`
- `takeProfitTargetPrice`
- `stopLossTargetPrice`
- `idempotencyKey`（V1 mandatory，BE-V1-10 / 16 統一補完）

Validation：

- 多單：`take_profit_price > stop_loss_price`。
- 空單：`take_profit_price < stop_loss_price`。
- Tick size validation 對兩腳分別。
- 漲跌停 / corporate action（BE-V1-09）對兩腳分別。
- Symbol / instrument / session 同 V0.5。
- Duplicate check：group-level（不允許 user 同日同 symbol 同 position_side 同價格 group 再建一份）。

Transaction：

1. 建立 `trade_intent_groups` row（status = active / scheduled by session）。
2. 建立 2 筆 `trade_intents`（take_profit + stop_loss）：
   - 都帶 `group_id`、`group_role`、`position_side`、`order_side` derived。
   - 兩腳同 trading_date、同 status。
3. 兩腳在盤中 + condition 已成立的情境參考「Immediate trigger」段。

### `CancelTradeIntentGroup`

- 觸發來源：user 取消其中一腳 → 由 controller 轉成 `CancelTradeIntentGroup`。
- 同 transaction：
  - Group `status = cancelled`、`terminal_reason = user_cancelled`。
  - 兩腳 status = `cancelled`，`cancelled_at`。
  - 寫 outbox event `bracket_cancelled`（可省略，user 自主取消不需通知）。

### Trigger transaction 擴充（BE-V0.5-09 + BE-V1-06 上）

當其中一腳達標：

1. Lock 整 group + 兩腳（用 `SELECT ... FOR UPDATE`，鎖定順序固定避免 deadlock：先 group 再兩腳 by id asc）。
2. 確認 group `status = active`。
3. 寫 `trigger_events` for 觸發腳。
4. 觸發腳 `status = triggered`。
5. Sibling `status = cancelled`、`cancelled_by_oco_sibling_id = triggered_leg_id`、`cancelled_at = now()`。
6. Group `status = triggered`、`terminal_at = now()`、`terminal_reason = oco_triggered`。
7. 寫 outbox event：
   - `trigger_intent_triggered`（觸發腳）。
   - `oco_sibling_cancelled`（sibling）— 通知文案說明「OCO 自動取消」（domain-spec §17 audit）。

### Ambiguous trigger（兩腳同 quote 同時成立）

當 evaluator 對同一 quote 對 group 評估時，發現兩腳都成立：

1. Group `status = ambiguous_trigger`、`terminal_at`、`terminal_reason = ambiguous_trigger`。
2. 兩腳 `status = ambiguous_trigger`。
3. 不寫 `trigger_events`（兩腳都未產生 trigger）。
4. 寫 outbox event：
   - `bracket_ambiguous_trigger`：通知 user「條件或行情異常，未觸發任一提醒，請重新建立」。
   - admin alert log（structured）：`bracket_ambiguous_trigger_detected`。

## 通知 Template（新增）

- `oco_sibling_cancelled`
  - in_app + telegram。
  - 內容：哪腳被 OCO 取消，sibling 觸發摘要。
- `bracket_ambiguous_trigger`
  - 內容：行情或條件異常導致兩腳同時成立，未觸發任一提醒，請重新建立。

## API

### `POST /trade-intent-groups`

Body：

```json
{
  "symbol": "2330",
  "positionSide": "long",
  "quantityLots": 1,
  "takeProfitTargetPrice": "650.0",
  "stopLossTargetPrice": "580.0",
  "idempotencyKey": "..."
}
```

Response：group + 兩腳 detail。

### `POST /trade-intent-groups/{id}/cancel`

- 取消整組。

### 既有 `POST /trade-intents/{id}/cancel`

- 若 intent.group_id 存在 → 轉成 `CancelTradeIntentGroup` 處理整組。
- Response 提示 sibling 也被取消。

### `GET /trade-intent-groups/{id}`

- 回 group 詳細 + 兩腳。

## Error Codes

新增：

- `OCO_PRICE_RELATION_INVALID`：422（多單 tp <= sl 或空單 tp >= sl）。
- `OCO_LEG_CANCEL_NOT_ALLOWED`：409（嘗試只取消一腳）。

## 驗收條件

- [ ] `trade_intent_groups` migration 可 upgrade / downgrade。
- [ ] `POST /trade-intent-groups` 建立 group + 兩腳同 transaction。
- [ ] 多單 tp <= sl / 空單 tp >= sl → 422。
- [ ] 觸發一腳後 sibling 自動 cancelled，整 group `status = triggered`。
- [ ] User 取消其中一腳 → 整組 cancelled。
- [ ] 兩腳同 quote 同時成立 → ambiguous_trigger，無 `trigger_events`，發 user 通知 + admin alert。
- [ ] Outbox events 正確寫入（觸發 + sibling cancel）。
- [ ] Duplicate group：同條件重複建立回 `DUPLICATE_INTENT`。

## 測試要求

- Unit：price relation validation。
- Unit：derive order_side for 4 combinations。
- Integration：long bracket → tp triggered → sl cancelled；group triggered。
- Integration：long bracket → sl triggered → tp cancelled。
- Integration：short bracket → tp triggered → sl cancelled。
- Integration：cancel one leg via `POST /trade-intents/{id}/cancel` → group + sibling cancelled。
- Integration：ambiguous trigger（quote 跨過兩個 target）→ group ambiguous_trigger，admin alert log 存在。
- Integration：duplicate bracket → `DUPLICATE_INTENT`。

## 工程注意事項

- Lock 順序固定：先 group 再 legs by id asc，避免 deadlock。
- Ambiguous trigger 是 corner case，通常代表 quote gap；vendor 推送如果有 batch updates，evaluator 需要在 batch 邊界判斷，不要連續單筆 quote 各別觸發一腳。
- OCO sibling cancel 必須在同 transaction 完成，避免 race condition 讓 sibling 仍在 active 又被另一 quote 觸發。
- Group status `ambiguous_trigger` 是 terminal；user 不能 cancel / recover，只能重建。
- Group 與 leg 的 status 不需要同步更新所有可能組合；只需保證 terminal state 一致（V1 不允許 leg-level 不同 terminal）。
- 對 BE-V1-10 CSV：bracket CSV row 同時帶 tp_price + sl_price → 經 `CreateTradeIntentBracket`。若 row 只填一邊 → 走單腳 take_profit / stop_loss。
