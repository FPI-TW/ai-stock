# BE-V1-03：Production Symbol Importer 與 Admin Override Readiness

## Metadata

- 類型：HITL
- 優先序：P0
- 預估：30h
- 依賴：BE-V0.5-04
- 交付版本：V1

## 背景

BE-V0.5-04 提供了 5 檔 hardcoded symbol seed 與 `SymbolService` lookup。V1 必須以 TWSE ISIN code list 為 primary source 自動匯入台股現股與 ETF，並支援 admin override（stop trading、特殊狀態、備註）。

`docs/domain-spec.md` §14 規定：

- Primary source = TWSE ISIN code list。
- 停復牌、停止買賣、不可交易等狀態可由交易所公告、行情來源 status 欄位或 admin override 補充。
- 策略引擎只讀內部 `symbol_master`。
- Symbol master 同時也是 BE-V0.5-13 demo allowlist 的母集合（demo 白名單 4 檔必須是 master 中 tradable 標的）。`9999` 仍可存在於 V0.5 seed 作為 halted 測試資料，但不屬於 demo allowlist。

本工單只實作 importer 與 admin override schema，正式 admin UI / API 由 BE-V1-14 接手。

## 目標

- 擴充 `symbols` schema：加 `source`、`source_updated_at`、`override_*` 欄位。
- 新增 `symbol_import_reports` table 紀錄每次匯入摘要。
- 實作 `SymbolImporter` adapter：抽象 `SymbolSourceAdapter`，第一個 implementation 為 `TwseIsinSourceAdapter`（抓 TWSE ISIN HTML / OpenAPI）。
- Importer 流程：fetch → normalize → diff → upsert（保留 admin override 不被 source 覆蓋）。
- Scheduled job 介面：每日盤前一次（時間由 BE-V1-04 calendar 與 BE-V1-15 scheduler 提供）。
- Admin override placeholder：欄位與不可被覆寫的 invariant 就位，正式 endpoint 留 BE-V1-14。
- 與 BE-V0.5-13 demo allowlist 對齊：BE-V0.5-13 的 4 檔 demo allowlist 在 V1 import 後必須仍存在於 master（不被 importer 刪掉）。V0.5 seed 內的 `9999` 是 halted 測試資料，與 demo allowlist 分開處理。

## 非目標

- 不做 admin override endpoint（BE-V1-14）。
- 不做 TPEx OTC ETF 額外 source（V1 ISIN list 已涵蓋 TPEx；後續 vendor 可補）。
- 不做 corporate action（BE-V1-09）。
- 不做正式 scheduled worker process（BE-V1-15）；本工單先以 CLI / management command 觸發 import，scheduler 在 R3 接。
- 不做 symbol 異動歷史 audit table（BE-V1-16）。

## DB Schema 變動

### `symbols` 新增欄位

- `source text not null default 'twse_isin'`
- `source_updated_at timestamptz null`
- `override_tradable_status text null`（admin 設定後取代 import 進來的 `tradable_status`）
- `override_note text null`
- `override_by_admin_id uuid null references users(id)`
- `override_at timestamptz null`

`tradable_status` 的 effective 計算：

```
effective_tradable_status = override_tradable_status IF override_tradable_status IS NOT NULL ELSE tradable_status
```

對外 API（`GET /symbols`）只暴露 effective 值。

### `symbol_import_reports`

- `id uuid primary key`
- `source text not null`（`twse_isin`）
- `started_at timestamptz not null`
- `finished_at timestamptz null`
- `status text not null check (status in ('running', 'success', 'failed'))`
- `total_fetched integer null`
- `inserted integer null`
- `updated integer null`
- `unchanged integer null`
- `errors jsonb null`
- `trigger_actor text not null`（`scheduled` | `manual_admin` | `cli`）
- `created_at timestamptz not null`

Indexes：`(source, started_at desc)`。

### Migration

- 既有 5 檔 seed 改用 `source = 'manual_seed'`，`source_updated_at = now()`。
- 若 V1 import 後 source 改回 `twse_isin`，但 admin 可隨後 override。

## 架構

### `SymbolSourceAdapter`

```python
@dataclass(frozen=True)
class SymbolRecord:
    symbol: str
    display_name: str
    market: Literal['TWSE', 'TPEx']
    instrument_type: Literal['stock', 'etf']
    tradable_status: Literal['tradable', 'halted', 'unsupported']
    isin: str | None

class SymbolSourceAdapter(Protocol):
    def fetch(self) -> Iterable[SymbolRecord]: ...
```

- TWSE ISIN list 來源：`https://isin.twse.com.tw/isin/C_public.jsp?strMode=2`（上市）+ `strMode=4`（上櫃）。
- 解析 HTML table；穩定性差，需 retry + fallback。
- Adapter 自包 retry 與 user-agent，network error 改丟 `SymbolSourceUnavailable`。
- 不在 adapter 做 normalization；只回 raw `SymbolRecord`。

### `SymbolImporter`

`app/services/symbols/importer.py`：

```python
class SymbolImporter:
    def __init__(self, source: SymbolSourceAdapter, repository: SymbolRepository, clock: Clock): ...
    def run(self, *, trigger_actor: str) -> SymbolImportReport: ...
```

流程：

1. 建立 `symbol_import_reports` row（`status = running`）。
2. `source.fetch()` 取 raw records。
3. Normalize：trim、uppercase symbol、過濾不支援 instrument types（warrant、特別股等）。
4. Diff vs `symbols`：
   - 新增 → INSERT。
   - 既有但 source 欄位變更 → UPDATE，但 `override_tradable_status` 等 admin override 欄位**不動**。
   - source 不再存在的 symbol → 標記 `tradable_status = 'unsupported'`（不 hard delete，保留歷史 FK）。
5. 寫入 `symbol_import_reports`（`status = success` / `failed`，加上統計）。
6. 整個 import 用一個 transaction，任一筆失敗整批 rollback。

### Scheduler hook

`app/services/symbols/scheduler.py`：

- 提供 `def run_daily_symbol_import() -> SymbolImportReport`。
- BE-V1-15 接手 cron / APScheduler；本工單只提供 callable 與 CLI：
  - `python -m app.cli.import_symbols` → 手動觸發。

### Admin override invariant

- `override_tradable_status` 只能由 admin endpoint 寫入（BE-V1-14）。
- Importer **永不**寫 `override_*` 欄位。
- 若 admin override 將 symbol 設為 `unsupported`，該 symbol 不應被視為 BE-V0.5-13 demo allowlist 成員；`9999` halted 測試資料也不列入 demo allowlist。

## API

本工單僅補一個 user-facing read API 與一個 admin internal endpoint：

### `GET /symbols`（已存在，調整）

- 改回 effective tradable status。
- Response 加 `effectiveTradableStatus`、`sourceUpdatedAt`、`hasOverride`（boolean）。

### `POST /admin/symbols/import`（內部，admin only）

- 觸發即時 import。
- Response 回 `symbolImportReport` 摘要。
- Audit reason（structured log）：`admin_symbol_import_triggered`。

### `GET /admin/symbols/imports`（list）

- 列最近 30 筆 import report。
- Cursor pagination 同 V0.5 convention。

## 驗收條件

- [ ] `symbols` schema 新增欄位 migration 可 upgrade / downgrade，且既有 5 檔 seed 不丟失。
- [ ] `SymbolImporter.run()` 可從 TWSE ISIN 抓 raw data 並寫入；失敗時 report `status = failed` 且 transaction rollback。
- [ ] Admin override 欄位不會被 importer 覆寫（regression test）。
- [ ] `effective_tradable_status` 對外正確顯示 override 值。
- [ ] BE-V0.5-13 demo allowlist 4 檔在 import 後仍存在且保持可交易；V0.5 seed 的 `9999` 可保留為 halted 測試資料，但不是 demo allowlist 成員。
- [ ] Source 消失的 symbol 被標 `unsupported`，不 hard delete（FK 保留）。
- [ ] `POST /admin/symbols/import` 由 `user` role 呼叫回 403。

## 測試要求

- Unit：HTML 解析（用 fixture HTML，不打網路）。
- Unit：`SymbolImporter.run` 對 insert / update / unchanged / missing 四種 case 計算正確。
- Unit：`override_tradable_status` 不被 importer 覆寫。
- Integration（PostgreSQL）：完整 import flow 寫入 `symbols` + `symbol_import_reports`。
- Integration：BE-V0.5-13 demo allowlist symbol 在 import 後仍可被 `subscribe()`。
- Integration：CLI 觸發 import → report `trigger_actor = cli`。
- Adapter contract：mock TWSE source 回 4xx / 5xx / timeout → 對應 error code。

## 工程注意事項

- TWSE ISIN HTML 不穩，解析時要對 column index 做 schema check，遇到格式變更立即 fail 並寫 error 到 `symbol_import_reports.errors`，不要硬猜。
- Importer 不應在生產用 `python requests` 同步抓網路時阻塞主 web process；提供 callable 但實際排程在 BE-V1-15。
- ETag / If-Modified-Since 對 TWSE ISIN 沒用（HTML 沒給），不需做。
- 對既有 V0.5 hardcoded seed，migration 寫成「若 row 不存在則 insert」的 idempotent 形式，避免 dev / staging 上 import 後資料衝突。
- Source 消失 → mark unsupported 而非 delete：避免 FK constraint 被破壞（trade_intents 可能還在引用）。
- 不要在 importer 內 fan-out 通知 user；symbol 異動的 user 通知留給 BE-V1-12 / 15。
