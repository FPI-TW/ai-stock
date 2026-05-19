# BE-V0.5-12：Integration Tests：Create -> Quote -> Trigger -> Notification

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：20h
- 依賴：BE-V0.5-07, BE-V0.5-09
- 交付版本：V0.5

## 背景

V0.5 是要提供本地可展示的主流程。Integration tests 必須固定這條路徑，避免後續改動破壞 demo 或正式垂直切片。

## 目標

建立 PostgreSQL-backed integration tests，覆蓋：

- Create intent。
- Quote update。
- Evaluation trigger。
- Trigger metadata。
- Minimal notification。
- Cancel。
- Basic validation failures。

## 非目標

- 不測 auth。
- 不測 CSV。
- 不測 Telegram。
- 不測 OCO。
- 不測 corporate action。
- 不測 outbox worker。

## 測試環境

要求：

- 使用 PostgreSQL，不用 SQLite 取代 domain-critical DB behavior。
- Test DB 可透過 env var 設定。
- Tests 可重跑且互不污染。
- 可用 transaction rollback、schema recreate 或 isolated database。
- Quote provider 一律以 `QUOTE_PROVIDER=in_memory` 注入 `InMemoryQuoteProvider`（見 BE-V0.5-13），整合測試不得呼叫 Shioaji 真實網路或要求 Shioaji credentials。
- 不可在測試中啟動真實 Shioaji websocket session；改以 fixture 直接寫入 in-memory snapshot。

## 必備 Test Cases

### Main Flow

1. Seed symbol `2330`。
2. Create `buy_price_alert` target `600.00`。
3. 經 `InMemoryQuoteProvider` 注入 quote ask `599.00`。
4. 呼叫 evaluator（manual evaluate endpoint 或直接 service call）。
5. Assert intent status is `triggered`。
6. Assert trigger record exists。
7. Assert notification exists。

### Sell Flow

- Create `sell_price_alert` target `600.00`。
- 經 `InMemoryQuoteProvider` 注入 quote bid `601.00`。
- Assert triggered。

### Immediate Trigger

- 先以 `InMemoryQuoteProvider` 注入 quote。
- Create intent whose condition is already true。
- Assert create response returns triggered status or subsequent detail shows triggered.
- Assert only one trigger record exists.

### Cancel

- Create active intent。
- Cancel。
- Evaluate matching quote。
- Assert intent remains cancelled and no trigger record exists.

### Invalid Tick

- Create with illegal target price。
- Assert `INVALID_TICK_SIZE` and nearest prices.

### Unknown Symbol

- Create with unknown symbol。
- Assert `UNKNOWN_SYMBOL`。

### Duplicate

- Create same active/scheduled intent twice。
- Assert second request returns `DUPLICATE_INTENT`。

### Fallback

- Create buy intent。
- Quote has no ask, last <= target（透過 `InMemoryQuoteProvider` 注入）。
- Assert triggered with `fallback_used = true`。

### Quote Subscription Limit

- Create 5 個分屬不同 symbol 的 active intent，第 6 個 symbol 觸發 subscribe。
- Assert 第 6 個 create 回 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`。
- Cancel 其中一個，再 create 同 symbol 之新 intent 應成功（配額釋放）。

## 驗收條件

- [ ] Tests 覆蓋 create -> quote update -> evaluate -> trigger -> notification。
- [ ] Tests 覆蓋 immediate trigger。
- [ ] Tests 覆蓋 cancel active intent。
- [ ] Tests 覆蓋 invalid tick 與 unknown symbol。
- [ ] Tests 覆蓋 quote subscription 5 檔上限與配額釋放。
- [ ] Tests 可在 PostgreSQL 測試環境穩定執行，不依賴 Shioaji 網路或 credentials。

## 工程注意事項

- Tests 應透過公開 API 或 command boundary，避免直接呼叫太深的 private function。
- 對時間敏感的測試需注入 clock。
- Quote/session 測試需固定 Taipei time。
- 不要因測試方便而讓 production code 暴露非 local mode dev endpoints。
