# BE-V1-20 TWAP 時間加權平均價格

TWAP 是測試中的策略型下單模式。第一版固定使用 server 端 `notify_only` execution mode，不接受 request 指定 execution mode；未來改為 user setting 後，所有策略同一時間仍只套用同一種 execution mode。

## API

- `POST /trade-intents/twap/preview`
  - 只計算計畫，不寫入 DB。
  - 前端應先呼叫此 API 確認交易是否可執行。
- `POST /trade-intents/twap/confirm`
  - 重新以 confirm 當下時間計算計畫。
  - 建立 `trade_intents` 與所有正數量 `twap_slices`。
  - 回傳 intent summary 與完整 `twapSlices`。

TWAP 不走 `POST /trade-intents` 的通用 strategy discriminator。未來新增策略時，也應使用明確分開的 preview / confirm 入口。

Request:

```json
{
  "symbol": "2330",
  "positionSide": "long",
  "quantityLots": 100,
  "intervalSeconds": 300,
  "endTime": "13:00"
}
```

欄位規則:

- `positionSide`: `long | short`。買賣差異只由多單或空單決定，其餘 TWAP 邏輯一致。
- `quantityLots`: 目標張數，範圍 `2..1000`。
- `intervalSeconds`: 最小單位為秒，範圍 `1..3600`。
- `endTime`: 只接受 `HH:MM`，最小輸入單位為分鐘；允許 `09:00..13:25`。
- TWAP 使用當下市價概念，不接受價格條件。

## 交易階段

收到請求時先判定當天階段，再套用對應邏輯。

- `pre_market`: 今日盤前，`startAt` 為今日 `09:00:00`，`tradingDate` 為今日。
- `regular_session`: 盤中，`startAt` 為 confirm / preview 當下秒數，`tradingDate` 為今日；若 `endTime` 已過，回 `TWAP_END_TIME_ALREADY_PASSED`。
- `post_market`: 盤後或非交易日，`startAt` 為下一個交易日 `09:00:00`，`tradingDate` 為下一個交易日。

第一版下一交易日只跳過週末，不處理國定假日。
實際通知 / 交易排程仍使用秒級時間；`endTime` 的分鐘輸入會以該分鐘的第 0 秒計算。

## Slice 計算

`endTime` 為 inclusive。

```text
availableSliceCount = floor((endAt - startAt) / intervalSeconds) + 1
materializedSliceCount = min(availableSliceCount, quantityLots)
```

限制:

- `materializedSliceCount` 至少 2，否則回 `TWAP_INSUFFICIENT_SLICES`。
- `materializedSliceCount` 最多 200，否則回 `TWAP_TOO_MANY_SLICES`。
- 若可用時間點多於目標張數，只建立前 `quantityLots` 筆正數量 slices，不建立 0 張 slice。

數量分配使用 `ceil(remainingQuantity / remainingSlices)`，較早的 slice 優先取得進位後多出來的張數。

## 通知與候補價格

slice 到期時 worker 會讀取最新行情:

- `long`: 優先 `ask`。
- `short`: 優先 `bid`。
- 若方向價格不存在，使用 `last` 作為 `last_fallback`。
- 若完全無法取得價格，立即送出不含價格的 TWAP 通知。

若主通知無價格，系統會以 10 秒間隔重試 3 次。任一次取得價格後，會送出 `twap_price_followup` 候補價格通知，並標明補發時間。候補價格只影響通知歷史，不改變 intent 或 slice 狀態語意。

Local mode runtime 會啟動 TWAP slice background worker，依 `TWAP_WORKER_INTERVAL_SECONDS` 定期處理到期 slice 與候補價格通知。若要關閉，可設定 `TWAP_WORKER_ENABLED=false`。

Dev worker endpoints 仍保留給手動補跑：

- `POST /dev/twap/process-due-slices`
- `POST /dev/twap/process-price-followups`

## 錯誤碼

- `TWAP_END_TIME_OUTSIDE_SESSION`
- `TWAP_END_TIME_ALREADY_PASSED`
- `TWAP_INSUFFICIENT_SLICES`
- `TWAP_TOO_MANY_SLICES`
- `TWAP_INVALID_INTERVAL`
- `TWAP_INVALID_QUANTITY`
- `TWAP_DUPLICATE_ACTIVE_PLAN`
- 時間格式錯誤仍使用 `VALIDATION_ERROR`
