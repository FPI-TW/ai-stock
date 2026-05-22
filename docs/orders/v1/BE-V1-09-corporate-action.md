# BE-V1-09：Corporate Action Importer、Cash Dividend Snapshot、Effective Price Preview

## Metadata

- 類型：HITL
- 優先序：P1
- 預估：45h
- 依賴：BE-V0.5-05, BE-V1-03, BE-V1-04
- 交付版本：V1

## 背景

V1 必須在除息日對 day intent 的 `target_price_effective` 套用現金股利調整（domain-spec §9）。V0.5 沒做這層；BE-V0.5-05 的 tick-size service 是調整後 rounding 的基礎，本工單在其上補：

- 每日盤前 corporate action snapshot。
- 策略引擎只讀 snapshot，不直接讀外部資料。
- Effective price preview API（UI 單筆建立時呼叫）。
- 不支援的 corporate action（配股、分割、合併、減資）→ 相關 active intents 轉 `paused_data_issue`。
- 調整後 tick 不合法 → `round away from trigger`。

Primary source：TWSE + TPEx 除權除息公告。MOPS 作 admin authority check。

## 目標

- 新增 `corporate_actions` table（raw imported events）。
- 新增 `trading_day_adjustment_snapshots` table（盤前 snapshot，策略引擎讀此）。
- 新增 `CorporateActionImporter`：抽 `CorporateActionProvider` adapter + TWSE / TPEx implementation。
- 新增 `ApplyCorporateActionSnapshot` command：盤前 job，pin 當日 effective targets。
- 新增 `MarkCorporateActionSnapshotDisputed` command：admin override（BE-V1-14 endpoint）。
- 新增 `EffectivePricePreview` API：UI 單筆建立時試算。
- 擴 `CreateTradeIntent` command：建立時自動套用當日 snapshot；UI 若沒看過 preview 則拒絕（`STALE_PRICE_CONTEXT`）。
- 不支援 corporate action 處理：相關 intents → `paused_data_issue` + 通知。
- Round away from trigger：tick 不合法時往遠離條件方向 round。

## 非目標

- 不支援配股、分割、合併、減資（標記 unsupported）。
- 不做 admin corporate action UI / endpoint（BE-V1-14）。
- 不做歷史價格回算。
- 不做 ex-dividend simulation tool。

## DB Schema

### `corporate_actions`

- `id uuid primary key`
- `symbol text not null references symbols(symbol)`
- `action_type text not null check (action_type in ('cash_dividend', 'stock_dividend', 'split', 'merger', 'reduction', 'rights_offering', 'other'))`
- `ex_date date not null`
- `cash_dividend_amount numeric(9, 4) null`
- `raw_payload jsonb not null`
- `source text not null check (source in ('twse', 'tpex', 'mops', 'manual'))`
- `source_updated_at timestamptz not null`
- `is_supported boolean not null`（V1 only `cash_dividend` is supported）
- `override_by_admin_id uuid null references users(id)`
- `override_at timestamptz null`
- `note text null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Indexes：`(symbol, ex_date)`、`(ex_date, is_supported)`。

### `trading_day_adjustment_snapshots`

- `id uuid primary key`
- `trading_date date not null`
- `symbol text not null references symbols(symbol)`
- `cash_dividend_amount numeric(9, 4) not null default 0`
- `has_unsupported_action boolean not null default false`
- `unsupported_reason text null`
- `version integer not null`
- `created_at timestamptz not null`
- `disputed boolean not null default false`
- `disputed_reason text null`
- `disputed_at timestamptz null`
- `disputed_by_admin_id uuid null references users(id)`

Unique：`(trading_date, symbol)`。Indexes：`(trading_date)`、`(symbol, trading_date)`。

### `corporate_action_import_reports`

結構同 BE-V1-03 / 04 import report。

### `trade_intents` 變動

新欄位（部分 V0.5 已預留）：

- `corporate_action_adjustment_amount numeric(9, 4) not null default 0`
- `price_adjustment_reason text null check (price_adjustment_reason in ('cash_dividend', null))`
- `price_adjusted_at timestamptz null`
- `snapshot_version integer null`

`target_price_effective` 公式：

```
target_price_effective = target_price_original - corporate_action_adjustment_amount
```

migration backfill：既有 row 設 `corporate_action_adjustment_amount = 0`，effective = original。

## Domain & Services

### `CorporateActionProvider` adapter

```python
@dataclass(frozen=True)
class CorporateActionRaw:
    symbol: str
    action_type: str
    ex_date: date
    cash_dividend_amount: Decimal | None
    raw_payload: dict

class CorporateActionProvider(Protocol):
    def fetch_upcoming(self, date_range: tuple[date, date]) -> Iterable[CorporateActionRaw]: ...
```

Implementations：

- `TwseCorporateActionAdapter`：TWSE 除權除息公告。
- `TpexCorporateActionAdapter`：TPEx 公告。

`MopsAuthorityChecker`（可選）：admin override 時用來查詢 MOPS 對照。

### `CorporateActionImporter`

- 每日盤前抓未來 7 天範圍。
- Normalize：`action_type` 對應內部 enum，`cash_dividend` 設 `is_supported = true`，其他設 false。
- 對 row 比對既有 corporate_actions：若 admin override 過，不被覆寫。
- 寫 `corporate_action_import_reports`。

### `ApplyCorporateActionSnapshot` command

- 由 scheduler 盤前觸發（BE-V1-15）。
- 對當日交易日 + 所有 active / scheduled intent 的 symbol 計算 snapshot。
- 寫 `trading_day_adjustment_snapshots`（同 `(trading_date, symbol)` 唯一）。
- 若 snapshot 已存在（重跑），保留 `version` 並 `++version`。
- 對 symbol 有 unsupported action 的 active intents，轉 `paused_data_issue` 並寫 outbox event（`intent_paused_data_issue`，原因 = unsupported_corporate_action）。
- 既有 active intents 的 `target_price_effective` 同 transaction 更新：
  ```
  effective = round_away_from_trigger(
      target_price_original - cash_dividend_amount,
      tick_size,
      strategy_direction
  )
  ```
- 寫 audit stub `corporate_action_adjustment_applied`。

### `MarkCorporateActionSnapshotDisputed` command

- 由 admin 觸發。
- 將 snapshot `disputed = true`、相關 active intents 轉 `paused_data_issue`。
- 通知 user。

### Round away from trigger

`app/domain/price.py` 擴：

```python
def round_away_from_trigger(price: Decimal, tick_size: Decimal, direction: Literal['up', 'down']) -> Decimal: ...
```

對 buy / take_profit_short / stop_loss_long fallback 等：「條件成立越容易發生 → 越遠離」依各 strategy 計算 direction。

### `EffectivePricePreview` service

- Input：`symbol`、`targetPriceOriginal`、`tradingDate`（optional，預設今日）。
- Output：
  ```json
  {
    "data": {
      "tradingDate": "2026-05-22",
      "targetPriceOriginal": "600.0",
      "corporateActionAdjustmentAmount": "5.0",
      "priceAdjustmentReason": "cash_dividend",
      "targetPriceEffective": "595.0",
      "tickRoundingApplied": false,
      "explanation": "今日除息 5.00，有效目標價為 595.00"
    }
  }
  ```
- 若 symbol 當日無 corporate action → `adjustmentAmount = 0`、`effective = original`。
- 若 symbol 有 unsupported action → 回 `UNSUPPORTED_CORPORATE_ACTION` warning 與「無法建立」訊息。

### `CreateTradeIntent` 擴充

新欄位：

- `effectivePriceContext`（前端 client 帶上 preview 拿到的 `corporateActionAdjustmentAmount` + `targetPriceEffective`）。

Validation：

- 若 symbol 當日有 corporate action 且 client 沒帶 `effectivePriceContext` → 回 `STALE_PRICE_CONTEXT` 409。
- 若 client 帶的 `effectivePriceContext` 與後端 snapshot 計算不一致 → 回 `STALE_PRICE_CONTEXT` 409。
- Snapshot 未產生（importer 還沒跑）→ 回 `CORPORATE_ACTION_SNAPSHOT_NOT_READY` 503。

不需 preview 的情境：

- 當日 symbol 無 corporate action → 不需 client 帶 effectivePriceContext。

## API

### `POST /quote/effective-price-preview`

- `Depends(require_user)`。
- Body：`{ "symbol": "2330", "targetPriceOriginal": "600.0", "tradingDate": "2026-05-22" }`。
- Response 同上。

### `POST /trade-intents`（既有）

- 加 `effectivePriceContext` field。
- Behavior 同 V0.5，validation 新增。

### `POST /admin/corporate-actions/import`

- Admin only。
- 觸發即時 import。

### `POST /admin/snapshots/apply`

- Admin only。
- Body：`{ "tradingDate": "2026-05-22" }`。
- 強制重跑 snapshot。

### `POST /admin/snapshots/mark-disputed`

- Admin only。
- Body：`{ "tradingDate": "2026-05-22", "symbol": "2330", "reason": "..." }`。

## Error Codes

新增：

- `STALE_PRICE_CONTEXT`：409。
- `UNSUPPORTED_CORPORATE_ACTION`：422（preview / create）。
- `CORPORATE_ACTION_SNAPSHOT_NOT_READY`：503。

## 驗收條件

- [ ] `corporate_actions` / `trading_day_adjustment_snapshots` migration 可 upgrade / downgrade。
- [ ] Cash dividend snapshot 套用後，相關 active intents 的 `target_price_effective` 正確調整。
- [ ] Unsupported corporate action → 相關 active intents 轉 `paused_data_issue` + 通知。
- [ ] Disputed snapshot → 相關 active intents 轉 `paused_data_issue` + 通知。
- [ ] `EffectivePricePreview` API 對有 / 無 corporate action 回正確值。
- [ ] `CreateTradeIntent` 對需要 preview 的 symbol 拒絕無 context 的 request。
- [ ] Round away from trigger 對 buy / sell / take_profit / stop_loss × long / short 各方向正確。
- [ ] Notification 顯示「原始 / 調整金額 / 有效」三欄。
- [ ] V0.5 既有 tests 全綠（migration 後 effective = original）。

## 測試要求

- Unit：`round_away_from_trigger` 對 8 種方向（4 strategy × long/short / N/A）的 case。
- Unit：snapshot 套用：cash_dividend 5.0 / 7.5 / 0 對 effective 影響。
- Unit：unsupported action → snapshot.has_unsupported_action true。
- Unit：preview vs create context match / mismatch。
- Integration：盤前 ApplyCorporateActionSnapshot → effective 更新、unsupported symbol intents 轉 paused。
- Integration：admin mark disputed → 對應 active intents 轉 paused。
- Integration：preview API 對 supported / unsupported / no-action 各回應。
- Integration：`CreateTradeIntent` 帶過期 effective context → 409。
- Adapter contract：TWSE / TPEx mock data → CorporateActionRaw 正確 normalize。

## 工程注意事項

- 配股不會立即反映在 cash dividend 邏輯內；但 unsupported flag 必須會讓 active intent 暫停，避免錯誤觸發。
- Round away from trigger 的 direction 推導：
  - `buy_price_alert`：往更低 round（更難觸發）。
  - `sell_price_alert`：往更高 round。
  - `take_profit_alert long`：往更高 round。
  - `take_profit_alert short`：往更低 round。
  - `stop_loss_alert long`：往更低 round。
  - `stop_loss_alert short`：往更高 round。
- Snapshot 一旦套用，盤中不重算 active intents（domain-spec §9）；盤中修正只標 disputed + paused，待下日 snapshot。
- `effectivePriceContext` 簽章：不需要加密簽章，但要包含 `corporateActionAdjustmentAmount` + `targetPriceEffective`；後端再 recompute 比對即可。
- 不要在 evaluator 內動態計算 effective price；evaluator 永遠讀 `trade_intents.target_price_effective`。
- Importer 重跑必須冪等；同 ex_date + symbol + source 不重複插。
