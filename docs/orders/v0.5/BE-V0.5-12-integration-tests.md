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

## 必備 Test Cases

### Main Flow

1. Seed symbol `2330`。
2. Create `buy_price_alert` target `600.00`。
3. Set quote ask `599.00`。
4. Evaluate quote。
5. Assert intent status is `triggered`。
6. Assert trigger record exists。
7. Assert notification exists。

### Sell Flow

- Create `sell_price_alert` target `600.00`。
- Set quote bid `601.00`。
- Assert triggered。

### Immediate Trigger

- Set quote first。
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
- Quote has no ask, last <= target。
- Assert triggered with `fallback_used = true`。

## 驗收條件

- [ ] Tests 覆蓋 create -> quote update -> evaluate -> trigger -> notification。
- [ ] Tests 覆蓋 immediate trigger。
- [ ] Tests 覆蓋 cancel active intent。
- [ ] Tests 覆蓋 invalid tick 與 unknown symbol。
- [ ] Tests 可在 PostgreSQL 測試環境穩定執行。

## 工程注意事項

- Tests 應透過公開 API 或 command boundary，避免直接呼叫太深的 private function。
- 對時間敏感的測試需注入 clock。
- Quote/session 測試需固定 Taipei time。
- 不要因測試方便而讓 production code 暴露非 local mode dev endpoints。
