# V0.5 Shioaji 測試查價 API

此 API 是測試功能，只用來透過 Shioaji 查詢少數指定標的的當前價格。它依賴既有的 Shioaji quote provider、Shioaji SDK 與 `SHIOAJI_API_KEY` / `SHIOAJI_SECRET_KEY` 登入狀態，無法單獨存在，也不是正式行情產品 API。

## Endpoint

```http
GET /quotes/current-price/{symbol}
```

允許查詢的 `symbol` 僅限：

- `2330`
- `2317`
- `0050`
- `00878`

其他標的會回 `CURRENT_PRICE_SYMBOL_NOT_ALLOWED`。

## Runtime 依賴

必須以 Shioaji provider 啟動：

```env
QUOTE_PROVIDER=shioaji_demo
SHIOAJI_API_KEY=...
SHIOAJI_SECRET_KEY=...
```

若使用 `QUOTE_PROVIDER=in_memory` 或其他非 Shioaji provider，此 API 會回 `QUOTE_PROVIDER_UNAVAILABLE`。這是刻意設計：此端點只是 Shioaji 測試查價入口，不可被視為可抽離的獨立服務。

## Response Example

```json
{
  "data": {
    "symbol": "2330",
    "currentPrice": "590.50",
    "bidPrice": "590.00",
    "askPrice": "591.00",
    "quoteTime": "2026-05-26T10:30:00+08:00",
    "receivedAt": "2026-05-26T02:30:01Z",
    "source": "shioaji",
    "testFeature": true
  }
}
```

`currentPrice` 來自 Shioaji snapshot 的 `close`，`bidPrice` / `askPrice` 分別來自 top buy / sell price。
