# BE-V0.5-05：Tick-Size 與 Decimal Price Domain Services

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：24h
- 依賴：BE-V0.5-02
- 交付版本：V0.5

## 背景

價格精度是核心交易 domain，不可用 floating point。V0.5 雖然只做買賣到價提醒，也必須先建立正確的 Decimal price 與台股 tick-size validation，避免後續 V1 重做。

## 目標

- 統一 price parsing / serialization。
- 使用 Decimal 儲存與比較價格。
- 實作台股 tick-size table。
- 不合法 tick 回傳最近合法價格。

## 非目標

- 不做除息調整。
- 不做 round away from trigger。
- 不做 V2 drift ticks。
- 不做漲跌停檢查。

## Tick Size Table

請依台股現行 tick size table 實作。若規格尚未凍結，至少封裝成單一 domain service，方便修正表格。

建議 interface：

```python
class PriceService:
    def parse_price(self, raw: str) -> Decimal: ...
    def is_valid_tick(self, price: Decimal) -> bool: ...
    def nearest_valid_prices(self, price: Decimal) -> tuple[Decimal, Decimal]: ...
    def assert_valid_tick(self, price: Decimal) -> None: ...
```

`nearest_valid_prices`：

- 回傳 lower / upper legal prices。
- 若 price 已合法，可回同價或由 caller 不呼叫。
- Response serialization 不應出現 float artifact。

## API Error

Invalid tick：

```json
{
  "error": {
    "code": "INVALID_TICK_SIZE",
    "message": "目標價不符合最小升降單位",
    "details": {
      "nearestPrices": ["98.8", "98.9"]
    },
    "requestId": "..."
  }
}
```

## DB / Serialization

- DB 使用 `numeric(18, 6)` 或更合理 numeric precision。
- Python 使用 `Decimal`。
- Pydantic response 對外輸出建議用 string，避免 JSON number 被前端當 float。
- Internal comparison 全部用 Decimal。

## 驗收條件

- [ ] Persisted price values 不使用 floating point。
- [ ] 不合法 target price 回傳 `INVALID_TICK_SIZE` 與最近合法價格。
- [ ] Unit tests 覆蓋 tick table boundaries。
- [ ] API 與 domain service 使用相同 tick validation。

## 測試要求

- Unit tests：每個 tick range 的 lower boundary、upper boundary。
- Unit tests：合法價格通過。
- Unit tests：非法價格產生 nearest legal prices。
- API integration：create intent with invalid target 回 `INVALID_TICK_SIZE`。
- Regression：`Decimal("0.1") + Decimal("0.2")` 類似情境不得輸出 float artifact。

## 工程注意事項

- 不要用 `float(price)` 做任何比較或 DB 寫入。
- 不要在 API route 各自實作 tick validation。
- Price formatting 應集中處理。

