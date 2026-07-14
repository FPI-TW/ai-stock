# T1：trade_intents 核心表 + 策略衛星參數表分層（V0.5 遺留 schema 債）

> **狀態更新（2026-06-18）— 設計已改為「模式丙：雙軌完全獨立」**
>
> 原「原地改 `trade_intents`」設計**已被推翻**。改採平行軌：舊 `trade_intents` + 舊 endpoint + 舊 dispatcher + 舊 repo/command **一律不動、留作可隨時 git 還原的保險**；另建全新 `trade_intent_core` + 3 衛星表 + 新 repo/command/dispatcher（robot #2），自成一條互不引用的垂直切片。新軌驗證跑通後，舊軌才整組退役（屆時整批刪除，新軌零改動）。
>
> 去重方案 A（核心表 `dedup_key`）維持不變。下方「結論 / 目標 / DB / 介面」等章節仍描述**舊的原地設計**，整份改寫待辦；**最新事實以本 banner + 下方〈Epic 階段〉+ 文末〈附錄：模式丙落地〉為準。**

## Epic 階段（PR 地圖）

T1 已從單一 schema 重構膨脹為**多階段 epic**（模式丙的代價：階段多、但每步小且可逆）。**不含舊軌退役**，到「新軌完整上線、不退化既有功能」共 **4 個 PR**：

| PR | 內容 | 性質 |
|---|---|---|
| **PR1** | 資料層：新表 schema（model+migration）+ repo（dedup + CRUD + §15 count + robot 查詢）+ 最小 command | 暗裝，無行為改變 |
| **PR2** | 新軌觸發能力：robot #2（新 dispatcher）+ 新 `trigger_events`/`notifications` 表 + 訂閱接線 | 暗裝（掃空表） |
| **PR3** | cutover（非 TWAP）：6 個 create endpoint + GET 清單切到新表 + inline 觸發回填 + 生命週期(啟用/到期) + 帳號停用連動 | **扳開關**，可單獨 revert |
| **PR4** | TWAP：新 slices 表 + `create_twap` + 到期連動 + TWAP endpoint 切換 | 獨立 |

- **小件（生命週期 activate/expire、帳號停用連動）折進 PR3**——它們是「切過去後不讓既有功能退化」的必要前置，不單獨成 PR，避免零碎。
- 最高風險的「切換」隔離在 **PR3 一個可 revert 的 commit**（這就是模式丙的保險）。
- **舊軌退役（整組刪除舊 endpoint/表/dispatcher/repo）為之後另議**，不在這 4 個 PR 內。
- 各 PR 內「刻意延後的 repo 方法 ↔ 補回時機」見文末〈附錄：模式丙落地〉。

## 排程與前置決策（2026-06-22）

> 與使用者對齊：產品仍在**構思 / demo 階段，無真實使用者**，`trade_intents` 等表內全為測試資料、隨時可清空。原 metadata「上線後」分層已據此修正。

- **唯一硬閘＝開放真實使用者**：T1 cutover（PR3 把 endpoint 接到新軌、並 `TRUNCATE trade_intents` 及其子表）必須在「開放真實使用者」之前完成。在那之前**無存量遷移、無孤兒單問題**，cutover 可隨時做、做壞清庫重來。閘綁的是「真實資料開始累積」，**不是** PR merge、也不是部署上線。
- **部署基建（#46 docker/nginx、#49 CI 自動部署）與 T1 脫鉤**：demo 階段需要穩定環境，兩者可並行先進，不被 T1 卡、也不為 T1 趕。「能部署」≠「開放真人」。
- **#52（per-strategy endpoint 拆分）排在 PR3 前**：兩者都重寫 `intents.py` 的 create 區塊，先讓 #52 落地可省 rebase 衝突（純工程便利，非上線閘）。
- **PR3 必做，否則 PR1/PR2 成死碼**：#55/#57 為暗裝——新表無寫入路徑、robot #2 掃空表。唯有 PR3 cutover 接通 endpoint，已投入的資料層與第二台 dispatcher 才有實際用途。
- **PR4（TWAP）屬 demo 範圍、不可省**：TWAP 是展示的一部分 → 排在 PR3 之後（仍為獨立增量），但要做完、非無限延後。其依賴的新 `twap_slices` 表 + `system_expire_day_intents_through` 的 slice 連動也在此一併完成；PR3 的到期 expire 僅先做非 TWAP 策略，TWAP 到期連動留給 PR4（見附錄）。
- **模式丙的成本/效益再認**：雙軌並行 + 可 revert cutover 的價值前提是「保護生產上的真實使用者不受遷移波及」；無使用者時這份保險價值低。但 PR1/PR2 已用模式丙投入並通過 review → **順順做完 PR3/PR4 接通優於推翻重來**（沉沒成本 + 已驗證可用）。T1「加策略＝加窄衛星表、核心不動」的方向與「demo 期 schema 仍會變」相容，方向不需翻案。

### 排序總覽

```
#46 / #49 基建        → 現在並行進（demo 用，不等 T1）
#52 endpoint 拆分      → PR3 前 merge（省 intents.py 衝突）
T1: #55 ✅ #57 ✅ → PR3 cutover（接通暗裝 + 清空舊表）→ PR4 TWAP（demo 需要，接著做完）
硬閘: 「開放真實使用者」前，T1 cutover 須完成
```

## Metadata

- 分層：**上線前完成（趁無真實使用者、DB 可清空）**。原標「上線後」已於 2026-06-22 修正——cutover 會清空 `trade_intents`，必須在「開放真實使用者」之前做完（見〈排程與前置決策〉）。
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
  - ⚠️ 精確界定：「零變動」指**不再長欄位**（相對舊單表 14 個 nullable 策略欄爆量），不是「連 metadata 都不碰」。核心表仍有一條 `ck_trade_intent_core_strategy` CHECK 枚舉合法策略字串（model 與 migration 各一份、手工同步，承襲舊軌慣例）；加第 N 種策略需一次 ALTER 重建這條 CHECK。此屬 metadata-level、非欄位增長、非舊策略風險，成本低故刻意保留 DB 層驗證（比 app 層更強、擋任何 writer 的非法值），不改參照表（9 個約年動一次的枚舉不值得多一張表 + FK）。

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

---

## 附錄：模式丙落地 — 已完成 / 刻意延後的 repository 方法（JIT）

> 新軌 repo `src/app/repositories/trade_intent_core_repository.py` 採 **JIT（just-in-time）**：
> 只實作「已有 / 即將出生 caller」的方法，不把舊 `IntentRepository` 的全部表面預先搬過來。
>
> **為什麼要延後、而不是一次補齊**（資深判斷，與使用者確認）：
> 1. 這是取代型遷移，新軌終究要 parity，所以這些方法**不是臆測**——每個在舊軌都有真實 caller。
> 2. 但「caller 還沒出生就先建好」會落地**未被任何路徑驗證**的程式，且 parity 只能在 **consumer 接縫 + 測試**上證明，不是在方法定義上宣稱。
> 3. 更糟的是有些「移植」現在是**半成品**：例如 `system_expire_day_intents_through` 的「到期連動取消 TWAP slices」必須等新 slices 表存在才寫得對，現在 port 過來只會是個假完成的坑。
> 4. 有些方法藏著**還沒拍板的跨軌設計決策**（訂閱共用 infra、帳號停用要不要連動新表），預先 port 等於默默替這些決策定案。

### 已實作（有即將出生的 caller）

- `create` / `find_by_id` / `cancel` / `commit` / `begin_nested`、`build_dedup_key`、`_to_domain`（非 TWAP 寫/讀/取消，已測）
- `count_active_or_scheduled_for_user` / `count_active_or_scheduled_for_user_symbol`（→ 新 command §15 限額）
- `system_list_active_symbols` / `system_list_active_by_symbols` / `system_update_trailing_baseline`（→ robot #2）

### 刻意延後 ↔ 補回增量（每個都附舊軌真實 caller，佐證非死碼）

| 舊 repo 方法 | 補回時機（增量） | 為什麼延到那時 | 舊軌真實 caller |
|---|---|---|---|
| `active_or_scheduled_symbols` | 新表訂閱接線（早，與 command/robot 同期） | robot #2 要收到報價，symbol 必須先被訂閱；屬尚未拍板的「新軌是否驅動共用訂閱」 | `main.py:63`（啟動 seed） |
| `count_active_or_scheduled_for_symbol` | 新表訂閱接線 | 取消時 reconcile 判斷是否退訂；同上跨軌訂閱決策 | `services/quote/intent_reconciler.py:62` |
| `list_by_owner` | 讀取 endpoint / GET 清單 cutover | cursor 分頁邏輯只在 GET 清單接上時才驗得了等價 | `api/routes/intents.py:118` |
| `system_activate_scheduled_day_intents` | 新生命週期排程 | 新軌還沒有排程 caller | `commands/intent_lifecycle.py:29` |
| `system_expire_day_intents_through` | 新生命週期排程（**接在 TWAP 增量後**） | 到期要連動取消 TWAP slices，須等新 slices 表，否則是半成品 | `commands/intent_lifecycle.py:25` |
| `cancel_active_for_owner` | 帳號停用連動新表 | caller＝改過的 account command，屬跨軌整合、尚未決定 | `commands/account.py:258` |

### TWAP 增量（獨立）

- `create_twap` + 新 slices 表（FK→`trade_intent_core.id`）+ slice 連動取消；完成後才接 `system_expire_day_intents_through`。
- ✅ **已定案（2026-07-14，組長確認）：TWAP「long+short 同 qty 同日」放行。** 做多/做空是方向相反的兩個不同意圖，不算重複；notify-only demo 下同標的多空對沖為正常需求。
  - 背景：Legacy 實際擋兩種重複：(a) 同 `position_side`（`uq_trade_intents_active_twap_duplicate`，任意 qty）；(b) **同 qty、任意 side**——因通用索引 `uq_trade_intents_active_duplicate`（core.py:152）**未排除 twap_order** 且 `nulls_not_distinct=True`，TWAP 的 `(owner, symbol, 'twap_order', NULL, NULL, NULL, qty, date)` 會撞鍵。(b) 判定為 legacy 意外副作用（通用索引沒打算管 TWAP）。
  - 落實：新軌 TWAP 索引 `uq_trade_intent_core_active_twap_duplicate` 只看 `position_side`（無 `quantity_lots`）→ **現況即為「放行」，index 不需改**。仍保留 (a) 同 side 去重。
  - PR4 待辦：`create_twap` 沿用現況 index；逐情境測試須新增一條「long+short 同 qty 同日可並存（不撞去重）」以鎖定此決策，並保留「同 side 重複被擋」。
