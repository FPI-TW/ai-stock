# BE-V0.5-08：Development Quote Adapter 與 Quote Validation

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：20h
- 依賴：BE-V0.5-04, BE-V0.5-06
- 交付版本：V0.5

## 背景

V0.5 不接正式行情 vendor，但需要可本地推進 quote 來展示與測試 trigger flow。Development quote adapter 只用於本地與測試，不是正式產品功能。

## 目標

- 建立 quote adapter interface。
- 建立 development implementation。
- 提供本地 API 或測試 helper 設定 quote。
- 實作 quote validation。

## 非目標

- 不接 licensed quote vendor。
- 不做 quote unhealthy。
- 不做 provider failover。
- 不保存全部 quote history。
- 不做 websocket/SSE。

## Interface

```python
class QuoteSnapshot:
    symbol: str
    bid_price: Decimal | None
    ask_price: Decimal | None
    last_price: Decimal | None
    quote_time: datetime
    received_at: datetime

class QuoteProvider:
    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        ...
```

Development implementation 可使用 DB table、in-memory store 或測試 fixture。若使用 in-memory，需注意 app reload 後資料消失是可接受的本地限制。

## Dev API

### `POST /dev/quotes`

只在 local mode 啟用。

Request：

```json
{
  "symbol": "2330",
  "bidPrice": "599.00",
  "askPrice": "600.00",
  "lastPrice": "599.50",
  "quoteTime": "2026-05-12T10:00:00+08:00"
}
```

Response：

```json
{
  "data": {
    "symbol": "2330",
    "updated": true
  }
}
```

若 `LOCAL_MODE=false`，endpoint 不應啟用或應回 404。

## Validation Rules

- Symbol 必須存在且 tradable。
- `quote_time` 必須在 regular session。
- `bid_price <= ask_price`，當 bid/ask 都存在時。
- 所有存在的價格都必須 > 0。
- bid/ask/last 至少一個存在。
- 缺 bid 或 ask 可由 evaluation 依策略 fallback last，但 validation 需標記資料不足情境。

V0.5 可不做 10 秒 freshness threshold；V1 licensed provider 再補完整 freshness。

## 驗收條件

- [ ] 本地 API 或測試可設定指定 symbol quote。
- [ ] Normalized quote 包含 symbol、bid、ask、last、quote_time、received_at。
- [ ] Validation 拒絕 out-of-session、crossed、non-positive、insufficient quotes。
- [ ] 缺 bid/ask 時可 fallback 到 last price，並記錄 metadata。
- [ ] Development adapter 不被描述為正式 quote product feature。

## 測試要求

- API test：local mode 可 set quote。
- API test：non-local mode dev endpoint disabled。
- Unit test：crossed quote rejected。
- Unit test：negative / zero price rejected。
- Unit test：out-of-session quote rejected。
- Unit test：last-only quote passes validation but marks fallback path.

## 工程注意事項

- Development adapter 的命名避免 `FakeProductQuoteProvider` 之類容易誤解的名稱。
- Quote validation logic 後續會被 licensed provider 共用，避免寫在 dev endpoint 裡。
- 不要為 V0.5 建 quote history table。

