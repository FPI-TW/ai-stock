# P4：除息調整（Corporate Action）

## Metadata

- 分層：上線後
- 優先序：P2
- ROM：**L**
- 依賴：L2（audit）；scheduled-jobs worker
- 交付版本：V1
- 併自舊工單：BE-V1-09-corporate-action

## 背景

V0.5 無除息處理。上線初期由 admin 手動避開除息日標的；本票補自動化。對齊 `docs/domain-spec.md` §9（價格 / tick / 除息調整）、§24（資料來源）。**注意**：V1 只做**現金股利固定金額**調整，不做配股/減資/分割（標 unsupported 並暫停）。漲跌停 / invalid_for_day 不在本票（V1 不做）。

## 目標

- **Provider adapter**：`CorporateActionProvider.fetchUpcomingActions(dateRange)`；V1 一個 primary source（TWSE+TPEx 除權除息公告），MOPS 作 authority check。
- **Importer**：抓 raw → normalize → 比對既有 → 自動更新未鎖定 → 產 import report → admin 可覆寫。
- **盤前 snapshot**：產 `trading_day_adjustment_snapshot`；一般盤開始後策略引擎只讀當日 snapshot；盤中修正不重算已啟用意圖，標資料異常影響下一次 snapshot。
- **調整公式**（§9）：`target_price_effective = target_price_original - corporate_action_adjustment_amount`（非除息日為 0）；保存 `target_price_original`/`effective`/`price_adjustment_reason`/`amount`/`adjusted_at`；不合法 tick 採 **round away from trigger**。
- **除息通知三欄**（§12 lines 652–657）：除息調整時通知須顯示**原始目標價／現金股利調整金額／調整後有效目標價**。P4 負責在 trigger payload 提供這三個值（`targetPriceOriginal`/`corporateActionAdjustmentAmount`/`targetPriceEffective`），由通知側（P2 / 既有 trigger payload + template）render；P4 不擁有 template 本身。
- **paused_data_issue**：snapshot disputed → 相關 active intents 轉 `paused_data_issue`、不觸發、通知受影響 user；恢復不回放，從最新有效 quote 起評。
- **unsupported action**：當日有影響價格的 unsupported corporate action → 相關提醒轉 `paused_data_issue`，通知「今日公司行動類型暫不支援」；admin 可覆寫為 cash adjustment（必 audit）。
- 策略引擎一律讀內部 `corporate_actions` / snapshot，不直接讀外部。

## 非目標

- 不做配股/減資/分割等比例調整（標 unsupported）。
- 不做漲跌停 / invalid_for_day（V1 不做）。
- admin 覆寫 UI 屬 P5（本票提供覆寫 command + audit；P5 接 admin 介面）。

## DB / 介面

- `corporate_actions`：`symbol`、`ex_date`、`action_type`、`cash_dividend_amount`、`status(supported/unsupported)`、`locked`、`source`、`imported_at`。
- `trading_day_adjustment_snapshots`：`trading_date`、`symbol`、`adjustment_amount`、`snapshot_version`、`created_at`。
- `trade_intents` 補 `target_price_effective` 相關欄位（若 V0.5 未含）。
- Command：`ApplyCorporateActionSnapshot`、`MarkCorporateActionSnapshotDisputed`。
- scheduled job：盤前 import + snapshot（idempotent，`job_name + trading_date` execution record）。

## 驗收條件

- [ ] importer 抓取→normalize→import report；admin 覆寫鎖定資料不被自動覆蓋。
- [ ] 盤前 snapshot 產生；一般盤只讀 snapshot；盤中修正不重算已啟用。
- [ ] 除息日 effective price 計算正確 + round away from trigger（不讓條件更易觸發）。
- [ ] snapshot disputed → 相關 intents `paused_data_issue` + 通知；恢復不回放。
- [ ] unsupported action → 暫停 + 文案；admin 覆寫為 cash adjustment 寫 audit。
- [ ] snapshot 未在開盤前產生 → admin 告警（P5 接收，本票發出）。

## 測試要求

- Unit：調整公式 + round away from trigger；supported/unsupported 判定。
- Integration：import idempotent；snapshot disputed → pause → resume；unsupported → pause；admin override audit。
- Adapter contract：corporate action importer。

## 工程注意事項

- provider 透過 adapter 抽象，V1 只一個 primary source。
- scheduled job idempotent（advisory lock / unique execution key）；重跑不產重複副作用。
