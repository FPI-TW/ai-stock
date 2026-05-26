# BE-V1-05：Licensed Quote Provider Adapter、Quote Health、Pause/Resume

## Metadata

- 類型：HITL
- 優先序：P0
- 預估：42.5h
- 依賴：BE-V0.5-09, BE-V0.5-13, BE-V1-06
- 取代：BE-V0.5-13（demo provider 整資料夾移除）
- 交付版本：V1

## 背景

V0.5 使用 Shioaji demo（BE-V0.5-13）作為唯一 runtime quote source，並在工單裡明確說：

> V1 切換到 licensed vendor 時，所有 symbol master 中的標的皆可選，本工單的 4 檔白名單失效。
> V1 新增 licensed provider 時，新增檔案 + 在 `factory.py` mapping 加一行 + **刪除 `shioaji_demo/` 整個資料夾**。

BE-V1-05 就是執行這個切換。同時補齊 V0.5 沒做的：

- Quote freshness（10s threshold，domain-spec §11）。
- Symbol-level `consecutive_invalid_count` + `invalid_since` → `paused_data_issue`。
- Provider 全域故障 → 全域 quote unhealthy。
- 恢復後最新有效 quote 立即評估（`trigger_context = resumed_from_data_issue`）。
- Pause/resume 通知（觸發 BE-V1-06 outbox event）。

正式 vendor 尚未選定（`docs/pm-v1-plan.md` §8 風險）；本工單以「`SomeLicensedQuoteProvider` 為 placeholder 名稱，vendor 確認後改名」的方式進行，所有 contract 與 health logic 不依賴特定 vendor SDK。

## 目標

- 新增 `app/services/quote/<vendor>/` 模組（vendor 名 placeholder），實作 `QuoteProvider` interface。
- 擴 `QuoteProvider` interface：新增 `subscribe_health_listener`、`get_provider_status()`。
- 新增 `QuoteHealthMonitor`：每 symbol 維持 invalid count + since，達門檻轉 `paused_data_issue`。
- 新增 `paused_data_issue` 恢復路徑：拿到 1 筆有效 quote 即 resume，並立即評估（domain-spec §11）。
- 新增 provider 全域 unhealthy 偵測 + admin alert（structured log，BE-V1-15 接收）。
- Factory 把 `shioaji_demo` mapping 移除，加入 `<vendor>` 與 `in_memory`。
- 刪除 `app/services/quote/shioaji_demo/` 整個資料夾、所有 `SHIOAJI_*` env、demo-only error code（`QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`、`SYMBOL_NOT_AVAILABLE_IN_DEMO`）。
- 更新 BE-V0.5-13 文件加 deprecation banner，指向本工單。

## 非目標

- 不做 multi-provider failover（domain-spec §11：「不自動混用多資料源」）。
- 不做 quote history table（V1 不保存 polling history）。
- 不做 push 給前端（前端仍 polling，UI 走 BE-V1-12）。
- 不做 vendor 合約 / 採購流程（HITL 由 PM 負責）。

## Provider Interface 擴充

`app/services/quote/base.py`：

```python
@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    bid_price: Decimal | None
    ask_price: Decimal | None
    last_price: Decimal | None
    quote_time: datetime
    source: str
    source_latency_label: str | None
    raw_payload_ref: str | None
    received_at: datetime

@dataclass(frozen=True)
class ProviderStatus:
    healthy: bool
    last_message_at: datetime | None
    reconnect_count: int
    detail: str | None

class QuoteProvider(Protocol):
    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]: ...
    def subscribe(self, symbol: str) -> None: ...
    def unsubscribe(self, symbol: str) -> None: ...
    def subscribe_quote_listener(self, listener: QuoteListener) -> None: ...
    def get_provider_status(self) -> ProviderStatus: ...
```

新增欄位：

- `source`：寫入 trigger event quote snapshot，例如 `"licensed_vendor_v1"`。
- `source_latency_label`：vendor 提供的延遲級別（`realtime` / `delayed_1s` 等）。
- `raw_payload_ref`：vendor 原始 payload 的 ref / hash，供 debug。
- `received_at` 必須由 provider 寫入（不依賴 server clock 推測）。

## Quote Health Monitor

`app/services/quote/health.py`：

```python
class QuoteHealthMonitor:
    def record_invalid(self, symbol: str, reason: InvalidReason) -> SymbolHealth: ...
    def record_valid(self, symbol: str, snapshot: QuoteSnapshot) -> SymbolHealth: ...
    def get_health(self, symbol: str) -> SymbolHealth: ...
    def get_global_status(self) -> GlobalHealth: ...
```

`SymbolHealth`：

- `symbol`
- `consecutive_invalid_count`
- `invalid_since: datetime | None`
- `last_valid_at: datetime | None`
- `status: 'healthy' | 'unhealthy'`

Rules（domain-spec §11）：

- 單 symbol `consecutive_invalid_count >= 3` 或 `now - invalid_since >= 15s` → unhealthy。
- 任一有效 quote 到來 reset。
- Provider 全域：5 秒內 0 筆有效 quote 跨所有訂閱 symbol → global unhealthy。

Health monitor 跑在 quote evaluator 同進程；不寫 DB（避免每 tick 寫 DB），只在 status 改變時觸發 event：

- `symbol_unhealthy` → 寫 outbox event `intent_paused_data_issue`（BE-V1-06 dispatch 通知）。
- `symbol_recovered` → 立即 evaluate + 寫 outbox event。
- `provider_unhealthy` / `provider_recovered` → 觸發 admin alert log。

## Trade Intent State Transitions

新增 transition：

| From               | Trigger                          | To                   |
| ------------------ | -------------------------------- | -------------------- |
| `active`           | symbol unhealthy                 | `paused_data_issue`  |
| `paused_data_issue`| 收到 valid quote 但未達標         | `active`             |
| `paused_data_issue`| 收到 valid quote 且達標           | `triggered` (+ resumed ctx) |
| `active`           | 盤中停止交易（vendor status hook）| `paused_market_status` |
| `paused_market_status` | 恢復交易 + 達標               | `triggered` (+ resumed ctx) |
| `paused_market_status` | 恢復交易 + 未達標             | `active`             |

Transition 都透過 command handlers：

- `PauseIntentForDataIssue`
- `ResumeIntentFromDataIssue`
- `PauseIntentForMarketStatus`
- `ResumeIntentFromMarketStatus`

## 通知（dispatch 透過 BE-V1-06 outbox）

Notification types（domain-spec §12）：

- `intent_paused_data_issue`
- `intent_paused_market_status`
- `intent_resumed`
- `price_triggered`（既有；補 `trigger_context` metadata）

`price_triggered` notification metadata 新增：

```json
{
  "triggerContext": "normal" | "resumed_from_data_issue" | "resumed_from_market_status"
}
```

文案差異由 template 處理（BE-V1-12）。

## Quote Validation 擴充

`app/services/quote/validation.py`：

- 既有 V0.5 規則保留（bid<=ask、>0、bid/ask/last 至少一個、session check）。
- 新增 freshness：
  - `received_at - quote_time <= 10s`
  - `now - quote_time <= 10s`
- 違反 freshness → `InvalidReason.STALE`，由 health monitor 計入 invalid。

## Factory & Configuration

### `factory.py`

```python
_PROVIDERS: dict[str, Callable[[], QuoteProvider]] = {
    "licensed_v1": _build_licensed,
    "in_memory": _build_in_memory,
}
```

- `shioaji_demo` 不再列出；若 env 還傳這個值，啟動失敗並提示「已於 BE-V1-05 移除」。
- Lazy import：licensed vendor SDK 僅在 `QUOTE_PROVIDER=licensed_v1` 時 import。

### 新增 env

- `QUOTE_PROVIDER`：預設 `licensed_v1`，CI / test 使用 `in_memory`。
- `LICENSED_QUOTE_API_KEY` / `LICENSED_QUOTE_SECRET`：vendor credentials。
- `LICENSED_QUOTE_ENDPOINT`：vendor base URL。
- `QUOTE_FRESHNESS_THRESHOLD_SECONDS`：預設 10。
- `QUOTE_SYMBOL_UNHEALTHY_INVALID_COUNT`：預設 3。
- `QUOTE_SYMBOL_UNHEALTHY_INVALID_DURATION_SECONDS`：預設 15。
- `QUOTE_GLOBAL_UNHEALTHY_GAP_SECONDS`：預設 5。

### 移除 env

- 所有 `SHIOAJI_*` 從 `settings.py` / `.env.example` / handoff docs 刪掉。

## Demo Provider 移除步驟

機械操作（grep + delete），不需要邏輯改動：

1. `git rm -r src/app/services/quote/shioaji_demo/`
2. `git rm tests/unit/quote/test_shioaji_*.py`
3. `factory.py` 移除 `shioaji_demo` mapping。
4. `settings.py` 移除所有 `SHIOAJI_*` field。
5. `.env.example` 移除所有 `SHIOAJI_*`。
6. `errors.py` 移除 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`、`SYMBOL_NOT_AVAILABLE_IN_DEMO`。
7. 任何 import `shioaji_demo` 的程式必須在 grep 階段就 zero（BE-V0.5-13 已加 CI 守備）。
8. `docs/orders/v0.5/BE-V0.5-13-*.md` 加 deprecation banner，註明被 BE-V1-05 取代。

## Error Codes

新增（共用 base）：

- `QUOTE_PROVIDER_UNHEALTHY`：provider 全域 unhealthy 期間，dev endpoint 或 admin status 查詢回此 code。
- `QUOTE_PAUSED_FOR_DATA_ISSUE`：建立 intent 時若 symbol 已 paused，作為 warning 回傳。

移除：

- `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`（demo-only）。
- `SYMBOL_NOT_AVAILABLE_IN_DEMO`（demo-only）。

## API

### `GET /admin/quote-provider/status`

- `Depends(require_admin)`。
- Response：global health + 每 symbol health。
- 供 BE-V1-15 監控面板使用。

### `POST /admin/quote-provider/reconnect`（選用）

- 強制重連 vendor session。
- Audit log stub `quote_provider_reconnected_by_admin`。

## 驗收條件

- [ ] `shioaji_demo/` 整資料夾從 repo 移除；grep 全 repo 命中 0。
- [ ] `factory.py` 不含 `shioaji_demo` mapping；env `QUOTE_PROVIDER=shioaji_demo` 啟動失敗。
- [ ] Licensed provider 接 vendor 後可訂閱 BE-V0.5-13 demo allowlist 之外的 symbol（測試以 mock vendor + symbol master）。
- [ ] Quote validation freshness 10s 規則生效。
- [ ] Symbol 連續 3 次 invalid 或 15s invalid → `paused_data_issue`，觸發通知。
- [ ] Resume 後拿到有效 quote：若達標 → trigger（`trigger_context = resumed_from_data_issue`）；未達標 → 回 active 並發 `intent_resumed`。
- [ ] Provider 全域 unhealthy 時 evaluator 不觸發新 trigger，但 quote feed 仍嘗試恢復。
- [ ] Admin alert structured log 在 provider unhealthy / recovered 時產生。
- [ ] V0.5 既有 integration tests（透過 in-memory provider）全綠。
- [ ] handoff 文件 / `.env.example` 已更新，無 `SHIOAJI_*` 殘留。

## 測試要求

- Unit：`QuoteHealthMonitor` 三種門檻邊界（2 次 invalid → healthy、3 次 → unhealthy；14s → healthy、15s → unhealthy）。
- Unit：freshness 邊界 9.9s pass、10.1s fail。
- Unit：lazy import vendor SDK 只在 `licensed_v1` 時觸發。
- Integration：`paused_data_issue` 完整轉場（active → paused → resumed → triggered）。
- Integration：恢復後立即評估達標，產生 `price_triggered` with `trigger_context = resumed_from_data_issue`。
- Integration：provider 全域 unhealthy 時新 quote 進來不立刻 trigger，但 `provider_recovered` 後可。
- Adapter contract（mock vendor）：login / subscribe / unsubscribe / reconnect 路徑各自 test。
- Migration：刪 `shioaji_demo/` 後跑全套 test suite 仍綠。

## 工程注意事項

- Vendor 尚未選定，本工單合約層必須保持「vendor neutral」。任何 vendor-specific quirk（payload schema、heartbeat 規則）必須包在 `<vendor>/` 子資料夾，evaluator / health monitor 只看 `QuoteSnapshot` + `ProviderStatus`。
- Provider 移除時，BE-V0.5-13 dev console 文件、tests、env 範例都要同步清理（CI grep 守備可協助發現殘留）。
- Quote health monitor 不寫 DB；其他需要持久化健康狀態的需求（如 BE-V1-15 dashboard）由 outbox event + admin metrics endpoint 提供。
- 對於 license 限制（QPS、symbol 上限等），vendor SDK 自身應有錯誤回應；包成 `QuoteProviderError` raise 到 factory 層。
- 不要在 V1-05 重新引入 demo 白名單概念；symbol master 是唯一允許清單，subscribe 不在 master 中的 symbol 應該由 `subscribe()` 即時拒絕（`UNKNOWN_SYMBOL`）。
- 重連策略：簡單 exponential backoff（1s → 2s → 4s → cap 30s），但每次斷線都 log + 計入 `reconnect_count`。
- Health monitor 的 state 是 in-process；多 worker scaling 時需要共享（BE-V1-15 / 後續 sharding）。V1 評估後決定先單 worker。
