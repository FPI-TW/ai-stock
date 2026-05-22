# BE-V1-14：Admin Data Overrides：Symbols、Calendar、Corporate Actions

## Metadata

- 類型：AFK
- 優先序：P2
- 預估：30h
- 依賴：BE-V1-03, BE-V1-04, BE-V1-09
- 交付版本：V1

## 背景

BE-V1-03 / 04 / 09 已建立 importer 與 schema 的 admin override 欄位（`override_tradable_status`、`override_*` on calendar、`disputed` on snapshot）。本工單把 admin 寫操作端 endpoint 補齊，並確保所有 override 寫 audit。

domain-spec §13：admin 不可代建 / 代取消 user intent，但可：

- 維護 symbol master override（tradable status / note）。
- 維護 market calendar override（trading day / session / 半日 / 臨時休市）。
- 維護 corporate action override + mark snapshot disputed。

每筆 override 必須帶 reason 與 audit。

## 目標

- 新增 admin override endpoints for symbols / market calendar / corporate actions / snapshots。
- 對應 commands：`OverrideSymbol` / `OverrideMarketCalendarDay` / `OverrideCorporateAction` / `MarkSnapshotDisputed`。
- 每筆 override 寫 audit stub + structured log。
- Cascade side effects：
  - Symbol 改 `unsupported` → 對應 active intents 轉 `paused_data_issue`。
  - Calendar 改 holiday → scheduled intents 改下日（domain-spec §14）。
  - Snapshot disputed → active intents 轉 `paused_data_issue`。
- 同步通知（透過 BE-V1-06 outbox）。

## 非目標

- 不做 importer 排程細節（BE-V1-15）。
- 不做 audit table（BE-V1-16）。
- 不做 user 等級資料瀏覽（BE-V1-13）。
- 不做 batch override CSV import。

## API

### Symbol override

#### `POST /admin/symbols/{symbol}/override`

Auth: admin（2FA verified）。

Body：

```json
{
  "tradableStatus": "halted",
  "note": "盤後特殊事件",
  "reason": "..."
}
```

Behavior：

- 若 `tradableStatus = unsupported`：cascade pause 該 symbol 所有 active intents（寫 outbox `intent_paused_data_issue`）。
- 若回到 `tradable`：cascade resume（寫 outbox `intent_resumed`），但若該 symbol 同時有 unsupported corporate action 仍維持 paused。
- 更新 `override_tradable_status` / `override_note` / `override_by_admin_id` / `override_at`。
- Audit `admin_override_applied`，metadata 包含 before / after / reason / target / actor。

#### `DELETE /admin/symbols/{symbol}/override`

- Clear override（`override_tradable_status = null`），effective status 改回 source 值。
- Cascade 同上。
- Audit。

### Market calendar override

#### `POST /admin/market-calendar/{tradingDate}/override`

Body：

```json
{
  "isTradingDay": false,
  "reason": "typhoon_2026_05_22"
}
```

或：

```json
{
  "isTradingDay": true,
  "sessionOpen": "09:00",
  "sessionClose": "12:30",
  "isHalfDay": true,
  "reason": "year_end_half_day"
}
```

Cascade：

- 開盤前改成 holiday → scheduled day intents (trading_date = 該日) 自動改下一交易日（用 MarketCalendarService 重 query）。寫 outbox `market_closed_rescheduled` 通知 user。
- 半日盤改變 session_close → expire job 必須讀新值。
- Audit。

#### `DELETE /admin/market-calendar/{tradingDate}/override`

- Clear override，effective 改回 source。
- Cascade 同上（注意若已執行 reschedule，回復為 trading day 不會自動恢復原 trading_date；保留 admin 訊息提示）。

### Corporate action override

#### `POST /admin/corporate-actions`

Body：

```json
{
  "symbol": "2330",
  "actionType": "cash_dividend",
  "exDate": "2026-05-22",
  "cashDividendAmount": "5.0",
  "isSupported": true,
  "reason": "..."
}
```

- 建立新的 corporate_actions row（source = `manual`）。
- 若同 (symbol, ex_date) 已存在 → 視為 override，set `override_by_admin_id / override_at`。
- Cascade：若該日 snapshot 已套用 → mark snapshot disputed（不自動重算，由 admin 觸發 `POST /admin/snapshots/apply`）。
- Audit。

#### `POST /admin/snapshots/mark-disputed`

Body：

```json
{
  "tradingDate": "2026-05-22",
  "symbol": "2330",
  "reason": "..."
}
```

- Set snapshot disputed = true。
- Cascade：對應 active intents 轉 `paused_data_issue`，寫 outbox。
- Audit。

#### `POST /admin/snapshots/apply`（既有，但 BE-V1-09 已建立）

- 強制重跑 snapshot。
- 對相關 active intents 更新 `target_price_effective`。
- Audit `corporate_action_snapshot_applied`。

## 驗收條件

- [ ] Admin override symbol → 該 symbol 對外 effective status 改變。
- [ ] Symbol override 為 unsupported → 相關 active intents 轉 paused_data_issue + 通知。
- [ ] Calendar override 改成 holiday → scheduled intents 改下一交易日 + 通知。
- [ ] Calendar override 半日 → expire job 使用新 session_close。
- [ ] Corporate action 手動建立 → 同 (symbol, ex_date) 已存在的會 override，不重複建立。
- [ ] Snapshot mark disputed → 相關 active intents 轉 paused + 通知。
- [ ] 所有 override 寫 audit，metadata 包含 before / after / reason / target / actor / request_id。
- [ ] Admin 未 2FA verified → 全部 override endpoints 403。

## 測試要求

- Integration：symbol override unsupported → intent paused + 通知 outbox event。
- Integration：symbol override clear → intent resumed（若無其他 paused 原因）。
- Integration：calendar override holiday → scheduled intent 改下日，trading_date 與通知正確。
- Integration：corporate action manual create → snapshot 已套用情境下 disputed。
- Integration：snapshot mark disputed → active intents paused。
- Integration：admin without 2FA / non-admin → 全部 403。
- Unit：cascade 邏輯（mock repo）。
- Unit：override-only 變更 (note) 不觸發 cascade。

## 工程注意事項

- Cascade 影響多 user，可能撞到大量 intent；對單一 transaction 過大時，採用分批 + 多 transaction，但每批仍要保 atomicity。
- Calendar override 改回 trading day 並不會自動 unschedule 已經 reschedule 過去的 intents（避免雙重變動）；admin UI 需顯示「已改期的 intent 不會被自動還原」訊息（前端展示，後端只回 cascade summary）。
- Corporate action override 不重算已套用 snapshot；admin 必須另外觸發 `POST /admin/snapshots/apply`。
- Audit reason 強制 minimum length；不可只填 placeholder（這層在 BE-V1-16 加強）。
- 所有 admin override 不允許繞過 V1-02 owner scope 對 user 資源做 mutation：override 影響的是 system data，不是 user-owned intents 內容。intent status 變更是 cascade 系統行為，不算「代取消」。
- Reverse / undo 透過第二筆 override 完成，不在 endpoint 直接提供 atomic undo。
