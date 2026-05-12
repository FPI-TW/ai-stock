# BE-V0.5-04：最小 Symbol Seed、Validation、Lookup API

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：15h
- 依賴：BE-V0.5-02
- 交付版本：V0.5

## 背景

V0.5 不做正式 symbol importer，但仍需要內部 symbol master 的最小版本，讓 create intent 使用正式的 symbol validation 流程。這份 seed 會被 V1 production importer 取代或擴充。

## 目標

- 建立最小 symbol seed。
- 實作 symbol lookup API。
- 實作 symbol validation domain service。
- Create intent 只接受 canonical symbol。

## 非目標

- 不做 TWSE/TPEx importer。
- 不做 admin override。
- 不做 fuzzy search ranking。
- 不做停復牌同步。
- 不做 CSV symbol validation。

## Seed 建議

至少包含：

- `2330` 台積電，TWSE，stock，tradable。
- `2317` 鴻海，TWSE，stock，tradable。
- `0050` 元大台灣50，TWSE，ETF，tradable。
- `00878` 國泰永續高股息，TWSE，ETF，tradable。
- 可再加入一筆 `halted` 或 `unsupported` 測試資料。

Seed 可以用 migration、啟動 seed command，或測試 fixture。若使用啟動 seed command，需確保 idempotent。

## API

### `GET /symbols`

Query：

- `q` optional，支援 symbol prefix 或 display_name contains。
- `limit` optional，預設 20，最大 50。

Response：

```json
{
  "data": [
    {
      "symbol": "2330",
      "displayName": "台積電",
      "market": "TWSE",
      "instrumentType": "stock",
      "tradableStatus": "tradable"
    }
  ]
}
```

### `GET /symbols/{symbol}`

Unknown symbol 回：

```json
{
  "error": {
    "code": "UNKNOWN_SYMBOL",
    "message": "找不到標的代號",
    "details": {"symbol": "9999"},
    "requestId": "..."
  }
}
```

## Domain Service

建議：

```python
class SymbolService:
    def get_tradable_symbol(self, symbol: str) -> Symbol:
        ...
```

Validation rules：

- Symbol 必須存在。
- `instrument_type in stock | ETF`。
- `tradable_status = tradable`。
- API/command 內部使用 canonical `symbol`。

Error codes：

- `UNKNOWN_SYMBOL`
- `UNSUPPORTED_INSTRUMENT`

## 驗收條件

- [ ] Seed data 包含台股現股與 ETF 範例。
- [ ] V0.5 intents 只接受 seed 中支援的 stock/ETF。
- [ ] API 支援用 symbol 查詢。
- [ ] Create intent 只接收 canonical symbol。
- [ ] Unknown 或 unsupported symbol 回傳正式 error envelope。

## 測試要求

- API test：`GET /symbols?q=2330` 回傳台積電。
- API test：`GET /symbols/{unknown}` 回 `UNKNOWN_SYMBOL`。
- Unit test：unsupported instrument 回 `UNSUPPORTED_INSTRUMENT`。
- Integration test：create intent with unknown symbol fails。

## 工程注意事項

- 不要讓前端傳 display name 建立 intent。
- 不要把 seed 寫死在 validation function 中。
- Symbol string 保持台股代碼原樣，例如 `0050` 不可轉成 int。
