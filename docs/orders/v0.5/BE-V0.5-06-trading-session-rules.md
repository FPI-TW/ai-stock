# BE-V0.5-06：基本 Trading Session / Day-Intent Rules

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：15h
- 依賴：BE-V0.5-02
- 交付版本：V0.5

## 背景

V0.5 不做正式 market calendar importer 或 scheduled jobs，但仍需要基本 `day` intent rules，確保 evaluator 不會在一般盤外觸發。V1 會把這個 service 擴充成完整 MarketCalendarService。

## 目標

- 實作 Asia/Taipei timezone handling。
- 實作台股一般盤 session 判斷。
- Create intent 可推導 `trading_date`。
- Evaluator 每次評估前檢查 session。

## 非目標

- 不做正式 holiday calendar importer。
- 不做半日交易。
- 不做臨時休市。
- 不做正式 scheduler；activation/expiry 由 API、quote dispatcher、dev tooling、TWAP worker 進入點先執行 lightweight lifecycle reconciliation。
- 不做 market status pause/resume。

## 規則

V0.5 session：

- Timezone：`Asia/Taipei`。
- Regular session：09:00-13:30。
- 週一到週五視為交易日。
- 週末視為非交易日。

Create intent：

- 若 now 在 regular session：`status = active`，`trading_date = today(Taipei)`。
- 若 now 在 regular session 前：`status = scheduled`，`trading_date = today(Taipei)`。
- 若 now 在收盤後或週末：`status = scheduled`，`trading_date = next weekday`。

Lifecycle reconciliation：

- 盤中：`trading_date = today` 的 `scheduled` day intent 轉 `active`。
- 盤後：`trading_date <= today` 且仍為 `scheduled` / `active` 的 day intent 轉 `expired`。
- 盤前：只過期 `trading_date < today` 的殘留 open intent，不過期當日盤前 scheduled intent。
- 週末 / 非交易日：只過期已落在今日以前的殘留 open intent；未來 trading_date 的 scheduled intent 保持 scheduled。

Evaluator：

- 只有 `active` intent 可評估。
- `now` 必須在 regular session。
- `quote_time` 必須在 regular session。
- 評估前先跑 lifecycle reconciliation，避免收盤後殘留 active intent 被觸發。

## 建議 Interface

```python
class TradingSessionService:
    def now_taipei(self) -> datetime: ...
    def is_trading_day(self, date: date) -> bool: ...
    def is_within_regular_session(self, dt: datetime) -> bool: ...
    def get_day_intent_trading_date(self, now: datetime) -> date: ...
    def get_initial_day_intent_status(self, now: datetime) -> Literal["active", "scheduled"]: ...
```

測試需可注入 clock，不要直接在 domain service 中硬呼叫 `datetime.now()`。

## 驗收條件

- [ ] Service 可判斷一般盤 session。
- [ ] Create intent 可推導 `trading_date`。
- [ ] Evaluator 在 session 外不得觸發。
- [ ] V0.5 文件明確標示不做 activation/expiry scheduled jobs。

## 測試要求

- Unit test：週一 08:59 -> scheduled today。
- Unit test：週一 09:00 -> active today。
- Unit test：週一 13:30 後 -> scheduled next weekday。
- Unit test：週六 -> scheduled next Monday。
- Evaluator test：now outside session 不觸發。
- Evaluator test：quote_time outside session 不觸發。

## 工程注意事項

- 所有 DB timestamp 仍存 UTC；`trading_date` 用 Taipei date。
- 不要把 V0.5 weekday rule 包裝成正式 market calendar。
- V1 會以內部 `market_calendar` table 取代週一到週五簡化規則。
