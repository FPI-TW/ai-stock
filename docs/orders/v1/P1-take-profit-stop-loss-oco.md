# P1：停利 / 停損 + OCO 群組

## Metadata

- 分層：上線後
- 優先序：P1
- ROM：**M**
- 依賴：L1（owner scoping）；觸發後通知走既有路徑（P2 後改 outbox）
- 交付版本：V1
- 併自舊工單：BE-V1-07-take-profit-stop-loss、BE-V1-08-oco-bracket

## 背景

V0.5 已交付買賣到價、限價、移動出場、市價、TWAP 策略與 evaluator（`src/app/domain/quote_evaluation.py`）。缺**持倉型停利/停損**與**OCO 群組**。對齊 `docs/domain-spec.md` §4（策略語意）、§5（OCO 群組）。

## 目標

- 策略 `take_profit_alert` / `stop_loss_alert`：必填 `position_side = long|short`；`order_side` 由 `position_side` 推導（不允許手動指定）。
- evaluator 觸發方向（§4）：多單停利 `bid>=target`、多單停損 `bid<=target`、空單停利 `ask<=target`、空單停損 `ask>=target`；缺 bid/ask fallback last 並標 `fallback_used`。
- OCO 群組：`TradeIntentGroup`（`group_type=bracket_alert`）+ 兩子 intent（tp + sl）。
- OCO 規則：任一觸發→另一自動取消（`oco_sibling_cancelled` audit）；通知送出失敗不回滾 OCO；使用者取消一腳→取消整組（不允許只取消一腳）。
- 建立驗證：多單 tp>sl、空單 tp<sl（`OCO_PRICE_RELATION_INVALID` 422）。
- `ambiguous_trigger`：同一 quote 兩腳同時成立→不任選，group 與兩子 intent 標 terminal `ambiguous_trigger`，發異常 system notification（非到價通知）+ admin warning。
- 立即觸發（§15）：建立時已成立→允許建立 + 立即觸發 + quote snapshot；OCO 一腳立即觸發→另一腳依規則取消。

## 非目標

- 不做做空進場（V1 只支援空單停利/停損回補）。
- 不驗證實際持倉（V1 允許聲明，V2 接券商才驗）。
- 不碰既有 8 策略；不做通知交付保證（P2）。

## DB / 介面

- `trade_intent_groups` 表：`id`、`owner_user_id`、`group_type`、`status`、`created_at`；兩子 intent 以 `group_id` fk 關聯。
- `trade_intents` 既有 status 已含 `ambiguous_trigger`（spec §6 terminal）；新增策略值 `take_profit_alert`/`stop_loss_alert`、`position_side` 欄位。
- `CancelTradeIntentGroup` command（接 L2 idempotency 機制）。
- API：`POST /trade-intent-groups`（OCO 建立，帶兩腳）、`POST /trade-intent-groups/{id}/cancel`。

## 依賴與交接

- 依賴 L1 owner scoping、L2 idempotency（`CancelTradeIntentGroup`）。
- 觸發後通知：上線初期走既有直接寫表，P2 完成後改 outbox（屆時本票 trigger 路徑改寫 outbox event，不改業務語意）。
- `daily limit` / 漲跌停：V1 不做（延後），本票不檢查當日限制。

## 驗收條件

- [ ] tp/sl 四種 position_side×方向觸發正確（含 fallback 標記）。
- [ ] OCO 一腳觸發→另一腳自動取消 + audit。
- [ ] 取消一腳→整組取消；不允許單腳取消。
- [ ] 建立驗證 tp/sl 價格關係（多/空）→ `OCO_PRICE_RELATION_INVALID`。
- [ ] 兩腳同 quote 同時成立→`ambiguous_trigger`（兩子+group）+ system notification + admin warning。
- [ ] 建立時已成立→立即觸發 + snapshot；OCO 立即觸發傳播取消另一腳。

## 測試要求

- Unit：四象限觸發方向；OCO 價格關係驗證；ambiguous 判定。
- Integration：OCO 連動取消（transaction）；單腳取消整組；立即觸發傳播；併發 trigger/cancel 狀態保護。
