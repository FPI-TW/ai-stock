# T1：trade_intents 核心表 + 策略衛星參數表分層（V0.5 遺留 schema 債）

## Metadata

- 分層：上線後（schema 重構 / 技術債，非阻擋上線；可獨立排程）
- 優先序：T1（T = 技術債 / 重構，與功能 P 系列、上線前 L 系列分開計號）
- ROM：**S**（舊表為測試資料，免資料回填，省掉本票原本最重的一段）
- 依賴：無功能依賴；需在無「trade_intents 欄位再擴張」的功能票進行中時切入，避免 migration 衝突
- **前提**：目前 `trade_intents` 內全為測試資料 → migration **不需保留 / 回填舊資料**，可直接重建 schema（必要時連同子表一併清空）
- 交付版本：V1
- 來源：V0.5 遺留問題（單表策略專屬欄位爆量），定調為「核心表 + 衛星參數表」垂直分層
- 與「endpoint per-strategy 拆分」是**兩件獨立事**：本票只動 schema/repository，不改 API 路由結構

## 背景

`src/app/db/models/core.py` 的 `TradeIntent`（line 60–209）目前把所有策略的專屬參數都塞在單一 `trade_intents` 表，已累積約 14 個 nullable 策略專屬欄：

- 價格類：`target_price_original`、`target_price_effective`
- trailing：`trail_mode`、`trail_value`、`baseline`、`dynamic_trigger_price`、`baseline_updated_at`
- twap：`position_side`、`twap_interval_seconds`、`twap_end_time`、`twap_start_at`、`twap_end_at`、`twap_available_slice_count`、`twap_materialized_slice_count`

衍生問題：

1. **人眼難辨識**——一筆 row 多數欄位是 null，看不出哪些欄屬於哪個策略。
2. **CHECK 約束臃腫**——`core.py` 已用 `target_price_presence`、`trail_fields_presence`、`twap_fields_presence` 等多條跨欄 CHECK 來模擬「某策略才允許某些欄非空」，每加一個策略就要再補一條巨型 CHECK。
3. **新增交易手段 = 改核心表**——任何新策略都要 `ALTER TABLE trade_intents`，舊策略全體承擔風險。

本票收掉這筆 V0.5 遺留 schema 債。

## 結論（已定調）

**採「核心表 + 策略衛星參數表」垂直分層（class-table inheritance），不拆 6 張獨立表、不用 JSONB。**

### 核心 `trade_intents`（重構後凍結，不再隨策略成長）

只保留所有策略共用的生命週期 / 狀態欄：
`id`、`owner_user_id`、`symbol`、`strategy`、`execution_mode`、`quantity_lots`、`filled_quantity_lots`、`last_fill_at`、`trigger_reference_price_type`、`trading_date`、`time_in_force`、`status`、`transaction_mode`、`notification_mode`、`cancelled_at`、`triggered_at`、時間戳（`created_at` / `updated_at`）。

### 衛星表（1:1，僅需參數的策略才配）

| 衛星表 | 欄位 | 服務的策略 / endpoint |
|---|---|---|
| `trade_intent_price_params` | `target_price_original`、`target_price_effective` | buy/sell_price_alert + limit_buy/sell_order（四個 endpoint **共用同一張**） |
| `trade_intent_trailing_params` | `trail_mode`、`trail_value`、`baseline`、`dynamic_trigger_price`、`baseline_updated_at` | trailing_stop_alert |
| `trade_intent_twap_params` | `position_side`、`twap_interval_seconds`、`twap_end_time`、`twap_start_at`、`twap_end_at`、`twap_available_slice_count`、`twap_materialized_slice_count` | twap_order（`twap_slices` 仍是它的一對多子表，不變） |

- **市價單**（market_order / market_buy_order / market_sell_order）無專屬參數 → 不配任何衛星表。
- 新增交易手段 = 新增一張窄衛星表，**核心表零變動、舊策略零風險**。

### 衛星表主鍵設計

1:1 衛星表用 `trade_intent_id` 同時當 **PK + FK**（不另開獨立 `id`），DB 天然保證一筆委託最多一張該類小卡。一對多才需自己的 `id`（如既有 `twap_slices`）。

### 為何不拆 6 表

dispatcher 的跨策略熱查詢（`system_list_active_*`、`list_by_owner`、`cancel`、生命週期 `activate`/`expire`）**全部只看 `status` 不分 strategy**。拆 6 表會逼出 UNION，並打爛 `twap_slices` / `trigger_events` / `notifications` 對 `trade_intents.id` 的 FK。垂直分層保留單一父表，只在需要參數時 LEFT JOIN。

### 為何不用 JSONB（備選方案 B，未採用）

核心表 + 單一 `strategy_params JSONB` 欄，加策略零 DDL，但失去 DB 層型別 / 約束，與本專案重視 typed / mypy 的調性不合。若未來策略爆量且參數高度多變再重新評估。

## 目標

1. 建三張衛星表（migration），把上述策略專屬欄從 `trade_intents` 移除（**舊表為測試資料，不回填**；必要時 migration 直接清空 `trade_intents` 及其子表後重建）。
2. 對應改 SQLAlchemy model：核心 `TradeIntent` 移除策略專屬欄，新增三個 1:1 relationship 到衛星 model。
3. repository（`src/app/repositories/intent_repository.py`）寫入 / 讀取改為核心表 + 對應衛星表（建單一併 insert 衛星 row，讀取 LEFT JOIN）。
4. 跨策略熱查詢路徑（dispatcher / list / cancel / lifecycle）維持只查核心表，不被衛星表拖慢。
5. 把原本散在核心表的跨欄 CHECK 約束，收斂進各自衛星表的「該欄一定存在」約束（衛星 row 存在即代表該策略，欄位可直接 NOT NULL）。

## 非目標

- 不做 endpoint per-strategy 拆分（另票，保留舊 `POST /trade-intents`）。
- 不改 intent 狀態機、業務語意、API 對外 JSON 形狀（DTO 輸出維持原欄位，組裝層負責拼核心 + 衛星）。
- 不動 `twap_slices`、`trigger_events`、`notifications` 對 `trade_intents.id` 的 FK。
- 不新增策略。

## DB / 介面

- **新增三張表**：`trade_intent_price_params`、`trade_intent_trailing_params`、`trade_intent_twap_params`，各以 `trade_intent_id`（PK + FK → `trade_intents.id`）為主鍵。
- **migration 無資料搬遷**：舊表全為測試資料 → 純 schema 變更（建衛星表 → 從核心表 drop 策略專屬欄）。若 drop 欄會留下無參數的孤兒測試 row，migration 可直接 `TRUNCATE trade_intents` 連同 `twap_slices` / `trigger_events` / `notifications` 等子表一併清空後重建，**不需 RETURNING 回填、downgrade 也不需保留資料**。
- **唯一約束重建（與資料搬遷無關，仍須做）**：`core.py` 現有 `uq_trade_intents_active_duplicate`（line 152）與 `uq_trade_intents_active_twap_duplicate`（line 166）引用了 `target_price_effective` / `trail_*` / `position_side` 等即將搬走的欄。即使舊資料不搬，這兩個防重複唯一索引仍須改建在「核心表 JOIN 衛星表」之上——以衛星表上的 partial unique index（含對應策略條件）重建，**確保對未來新單的去重語意不退化**（這是本票風險最高處，需在驗收逐情境測試）。
- 衛星表欄位在「衛星 row 存在 = 該策略」前提下，多數可直接 `NOT NULL` + 單欄範圍 CHECK（取代核心表的跨欄 presence CHECK）。

## 驗收條件

- [ ] 三張衛星表建立，各以 `trade_intent_id` 為 PK + FK，1:1。
- [ ] migration upgrade 後核心表不再有策略專屬欄；衛星表就緒（**無舊資料回填需求**）。
- [ ] migration 可降級（downgrade）回單表結構（測試資料，不要求保留資料）。
- [ ] 建單（含五種策略 + 市價單）→ 核心 row + 正確衛星 row 同 transaction 寫入，失敗一致回滾。
- [ ] 讀取單筆 / 清單 → DTO 輸出欄位與重構前完全一致（對外 JSON 不變）。
- [ ] dispatcher 熱查詢（`system_list_active_*` / `list_by_owner` / `cancel` / `activate` / `expire`）只掃核心表，不因衛星表退化。
- [ ] **防重複語意不退化**：原 `uq_trade_intents_active_duplicate` / `uq_trade_intents_active_twap_duplicate` 覆蓋的重複委託情境，重構後仍被擋下（需逐情境測試）。
- [ ] `make check` 全綠（含 mypy：relationship 型別正確標註）。

## 測試要求

- Unit：各策略建單寫對應衛星表；市價單不寫衛星表；DTO 組裝核心 + 衛星後欄位齊全。
- Integration：
  - 五策略 + 市價單各建單一次，驗核心 / 衛星 row 與 DTO 輸出。
  - 建單 transaction rollback → 核心與衛星皆不留 row。
  - 重複委託（價格類 / twap）被新唯一約束擋下。
  - dispatcher list/cancel/activate/expire 行為與重構前一致。
- Migration：upgrade 後 schema 正確（核心表無策略欄、三張衛星表就緒）；downgrade 可回單表結構。**無需資料回填測試**（舊表為測試資料）。

## 工程注意事項

- **唯一約束是最大地雷（與資料搬遷無關）**：即使舊資料不搬，仍須盤點所有引用策略專屬欄的 index / constraint（`core.py` line 149–173），確認重建後對**新單**的去重語意等價，否則會放行重複委託。
- 衛星 row 與核心 row 同一 transaction 寫入，沿用既有 repository 寫入慣例（不另 commit）。
- 讀取路徑：熱查詢只查核心表；需參數時才 LEFT JOIN 衛星表，避免無謂 JOIN 拖慢 dispatcher。
- relationship 用 1:1（`uselist=False`），mypy 型別需明確（`Mapped[PriceParams | None]`）。
- 命名遵循 snake_case；class 名用 PascalCase（如 `TradeIntentPriceParams`）。
- 與任何「會再加 trade_intents 欄」的功能票錯開時程，避免 migration rebase 衝突。
