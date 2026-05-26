# BE-V0.5-13：Shioaji Demo Quote Provider（取代 BE-V0.5-08）

## Metadata

- 類型：AFK
- 優先序：P0
- 預估：22h
- 依賴：BE-V0.5-04, BE-V0.5-06
- 取代：BE-V0.5-08
- 交付版本：V0.5

## 背景

V0.5 原本規劃以本地 dev quote adapter 推 quote（BE-V0.5-08）。實際 demo 時需要看到真實盤中價格驅動 trigger，因此改以 Shioaji（永豐金證券）官方 Python SDK 的即時行情訂閱作為 V0.5 唯一 quote provider。

Shioaji 訂閱有總量限制，free / demo 等級實測為**同時最多 5 檔**。這對 V0.5 demo 足夠，但需要在系統層級表現出明確的訂閱配額行為，避免後續以為可以無限擴充。

V0.5 使用 Shioaji demo 還受限於另一條約束：**可選標的固定為一組白名單**（見下方「Demo 標的白名單」），不接受任何其他 symbol。此限制**僅適用於 Shioaji demo provider**；當 V1 切換到 licensed vendor 時，所有 symbol master 中的標的皆可選，此白名單失效。

### Demo 與 Production Provider 切分原則

V0.5 的 Shioaji provider **與未來 V1 licensed provider 是兩個獨立 implementation**，不共享程式碼，不靠 feature flag 切換邏輯分支。理由：

- V0.5 此階段為 demo，會有大量 hardcode（白名單、5 檔配額、demo-only error code、Shioaji SDK 特有 callback 結構）。若與正式 provider 寫在同一份程式內，V1 遷移時要逐行剔除 demo 條件，**極易遺漏**。
- 兩個 provider 都實作同一份 `QuoteProvider` interface（見下方 Interface 小節）。上層（evaluator、intent service）只依賴 interface，不認得具體 provider class。
- V1 切換策略：**新增** `app/services/quote/<vendor>.py` 與對應 provider class，**整檔刪除** `shioaji_demo.py`、`SHIOAJI_*` env、demo-only error code；上層程式不動。
- 切換機制：runtime 由 env var `QUOTE_PROVIDER` 決定載入哪個 provider，不在程式內 `if env == "demo"` 判斷。

此 provider 仍屬於「展示用、非正式行情產品」。V1 會以**獨立 work order** 新增 licensed provider，不延續本工單的程式碼。

## 目標

- 沿用 BE-V0.5-08 定義的 `QuoteProvider` interface，提供 Shioaji-backed **demo** 實作（獨立模組，與未來 licensed provider 不共享 class）。
- 透過 Shioaji websocket 訂閱 bid/ask/last，並以 in-memory snapshot 暴露給 evaluator。
- 管理 Shioaji session 生命週期（login、logout、reconnect）。
- 管理最多 5 檔的訂閱配額與白名單守備（demo 限制，限本 provider 內處理）。
- 沿用 BE-V0.5-08 的 quote validation 規則（位於 base 模組，所有 provider 共用）。
- 保留一個「測試用」記憶體 quote provider，供 integration tests 使用，不在 production runtime path 啟用。
- 提供 env 驅動的 provider factory，使切換 provider 不需改任何上層程式。

## 非目標

- 不接其他 licensed quote vendor。
- 不做 quote unhealthy / SLA monitoring。
- 不做 provider failover。
- 不做 quote history 保存。
- 不向前端推 websocket / SSE（前端仍走 polling）。
- 不在 V0.5 做動態提升訂閱配額（5 檔即上限）。
- 不在 V0.5 做 futures / options，僅處理上市櫃股票。

## 架構

### Interface

沿用 BE-V0.5-08：

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

    def subscribe(self, symbol: str) -> None:
        ...

    def unsubscribe(self, symbol: str) -> None:
        ...
```

`subscribe` / `unsubscribe` 為新加方法（BE-V0.5-08 未要求），用以讓上層在 intent 變更時管理訂閱集合。

### 模組與檔案佈局

明確切分 V0.5 throwaway 範圍與 V1 keep 範圍，讓未來 vendor 切換是「新增檔案 + 刪除一個資料夾」的純機械操作。

```
app/services/quote/
├── base.py              # QuoteProvider Protocol、QuoteSnapshot、共用 error class  ← V1 keep
├── validation.py        # QuoteValidator（BE-V0.5-08 沿用，所有 provider 共用）     ← V1 keep
├── in_memory.py         # InMemoryQuoteProvider（測試用）                          ← V1 keep
├── factory.py           # 依 QUOTE_PROVIDER env 決定載入哪個 provider              ← V1 改一行 mapping
└── shioaji_demo/        # Shioaji demo 全部 hardcode 集中於此資料夾                 ← V1 rm -rf
    ├── __init__.py
    ├── provider.py      # ShioajiQuoteProvider（實作 QuoteProvider）
    ├── allowlist.py     # demo 白名單常數 + SymbolNotAvailableInDemo
    ├── quota.py         # 5 檔配額管理 + QuoteSubscriptionLimitExceeded
    └── client.py        # Shioaji SDK login / callback 包裝
```

V1 新增 licensed provider 時：
- 新增 `app/services/quote/<vendor>.py` 或 `app/services/quote/<vendor>/` 子資料夾
- 在 `factory.py` 的 mapping 加一行 `"<vendor>": <Vendor>QuoteProvider`
- 刪除 `shioaji_demo/` 整個資料夾、所有 `SHIOAJI_*` env、demo-only error code
- 上層（evaluator、intent service、API）**完全不動**

如果遷移時發現任何一行上層程式 import 了 `shioaji_demo.*`，視為設計缺陷，必須在 V0.5 修掉。

### Shioaji Demo Provider 實作要點

- 啟動時依 env var `SHIOAJI_API_KEY`、`SHIOAJI_SECRET_KEY` 登入 Shioaji。
- 對訂閱的 contract 同時註冊 `quote_callback`（bid/ask）與 `tick_callback`（last deal）。callback 名稱依實際 SDK 版本決定。
- Callback 內以 thread-safe dict 維護 `symbol -> QuoteSnapshot` 的最新狀態。`received_at` 用系統時間，`quote_time` 用 Shioaji 推送的時間欄位。
- `get_quotes(symbols)` 從 in-memory dict 取最新 snapshot。
- 應用程式 shutdown 時 logout、清空訂閱。
- 重啟 / reconnect：簡單策略，重新 login 後重訂閱目前 active 集合即可，不做 exponential backoff。

### Demo 標的白名單

V0.5 使用 Shioaji demo 期間，可選標的固定為以下 4 檔，**其餘一律拒絕**：

| Symbol | 名稱 | 類別 | tradable_status | 用途 |
|---|---|---|---|---|
| `2330` | 台積電 | stock | tradable | demo 主標的 |
| `2317` | 鴻海 | stock | tradable | demo 主標的 |
| `0050` | 元大台灣50 | etf | tradable | demo ETF |
| `00878` | 國泰永續高股息 | etf | tradable | demo ETF |

`9999` 停牌測試標的改由前端阻擋，不列入 Shioaji demo provider 白名單，也不向 Shioaji 發出查價或訂閱請求。

實作要求：

- Provider 啟動時以該白名單 hardcode（或讀 `SHIOAJI_DEMO_ALLOWED_SYMBOLS` env，預設為上述 4 檔），不從 DB 動態取。
- `subscribe(symbol)` 對白名單外的 symbol 直接拒絕，**不發出 Shioaji 訂閱請求**，回 `SYMBOL_NOT_AVAILABLE_IN_DEMO`。
- `POST /trade-intents` 在 reconcile 觸發 subscribe 失敗時，把該錯誤一併轉成 API error 拒絕建單（與配額超出走同一條 transaction-rollback 路徑）。
- V1 切換到 licensed vendor 時，本節整段隨 demo provider 移除；symbol master 即為唯一 allowlist。
- 此白名單與 BE-V0.5-04 的 symbol seed 內容需保持一致（DB 不該存在白名單外的 tradable symbol），但白名單規則**僅存活於 `shioaji_demo/allowlist.py`**，不下放到 SymbolService、不上拋到 base abstraction。V1 刪 `shioaji_demo/` 即連同移除。

### 訂閱配額管理

- Provider 維持 `max_subscriptions = 5`（可由 env var `SHIOAJI_MAX_SUBSCRIPTIONS` override，但預設 5）。
- `subscribe()` 若會超過上限，丟出 `QuoteSubscriptionLimitExceeded`。
- 訂閱集合來源：當前 `active` 與 `scheduled` 狀態的 trade intent 所涉及的不重複 symbols。
- 啟動時 reconcile 一次（讀 DB → 訂閱）。
- 5 檔配額限制與白名單獨立：白名單先擋（symbol 是否允許），配額後擋（允許標的是否已達訂閱上限）。
- 配額管理程式位於 `shioaji_demo/quota.py`，V1 licensed provider 若有自己的 quota 規則需獨立重新設計，不繼承本 demo 實作。

### Intent CRUD 整合（自 BE-V0.5-07 延後）

BE-V0.5-07 已 ship 但未含此整合，於本工單一併補：

- `POST /trade-intents` 建立成功的同一 transaction commit 前，需 reconcile 訂閱集合；若新 symbol 會讓配額超出，於 commit 前即拒絕，避免建立完才發現沒訂閱。
- 拒絕時對外回傳 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`（HTTP 422 / 409 擇一，與既有 envelope 規範對齊）。symbol 已在訂閱集合內則不會撞到。
- `POST /trade-intents/{id}/cancel` 與 trigger 流程使 intent 進 terminal 狀態後，若該 symbol 無其他 active/scheduled intent，需 unsubscribe 釋放配額。

### 測試用 In-Memory Provider

- 提供 `InMemoryQuoteProvider`（命名避免 `Fake*Product*` 之類混淆），實作同一 interface。
- 僅供 integration tests / unit tests 注入；不在 production 容器啟用、不掛 dev API。
- BE-V0.5-08 的 `POST /dev/quotes` endpoint **不實作**。Demo 改以實際盤中行情驅動；測試以 in-memory provider 直接 set。

## Configuration

新增 env var：

- `QUOTE_PROVIDER`：provider 切換器，**唯一的切換機制**。V0.5 接受值：
  - `shioaji_demo`（預設，runtime 使用）
  - `in_memory`（測試 / CI 使用）
  - 任何其他值 → 啟動失敗，明示「未知 provider」
  - V1 新增 licensed vendor 時，於 `factory.py` mapping 加新 value，不需改其他 env。
- `SHIOAJI_API_KEY` / `SHIOAJI_SECRET_KEY`：**僅在 `QUOTE_PROVIDER=shioaji_demo` 時讀取**；缺失則 demo provider 啟動失敗。其他 provider 不讀此 env，啟動不報缺。
- `SHIOAJI_MAX_SUBSCRIPTIONS`：選用，預設 5。同上限定僅 `shioaji_demo` 讀。
- `SHIOAJI_SIMULATION`：選用 boolean，呼叫 Shioaji simulation 模式時為 `true`，預設 `false`。同上限定僅 `shioaji_demo` 讀。
- `SHIOAJI_DEMO_ALLOWED_SYMBOLS`：選用，逗號分隔 symbol list，預設為白名單 4 檔。同上限定僅 `shioaji_demo` 讀。

切換 provider 不需修改 settings.py 之外的程式碼；所有 `SHIOAJI_*` env 與相關 config 都隨 `shioaji_demo/` 一併在 V1 移除。

`LOCAL_MODE` 不再用來開關 dev quote endpoint（該 endpoint 已移除）。

## Validation Rules

沿用 BE-V0.5-08，重申於此：

- Symbol 必須存在且 tradable。
- `quote_time` 必須在 regular session。
- `bid_price <= ask_price`，當 bid/ask 都存在時。
- 所有存在的價格都必須 > 0。
- bid/ask/last 至少一個存在。
- 缺 bid 或 ask 可由 evaluation 依策略 fallback last，validation 需標記資料不足情境。

V0.5 仍不做 freshness threshold；V1 再補。Validation 模組必須獨立於 provider，licensed provider 將來共用同一份。

## Error Codes

**共用（base 模組，V1 keep）**：

- `QUOTE_PROVIDER_UNAVAILABLE`：provider 不可用（如 Shioaji login 失敗、licensed vendor session 斷線）。具體觸發語意各 provider 自定，error envelope 對外一致。
- `QUOTE_UNAVAILABLE`：要求的 symbol 尚未有任何 snapshot（剛訂閱、尚未推送）。

**Demo-only（`shioaji_demo/` 內，V1 隨資料夾刪除）**：

- `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`：訂閱數已達 5 檔。建立 intent 時若新增 symbol 會超出，需以此 error 拒絕。V1 licensed vendor 若有自己的 quota 規則，需新增獨立 error code，不沿用此 code。
- `SYMBOL_NOT_AVAILABLE_IN_DEMO`：symbol 存在於 master，但不在 Shioaji demo 白名單；V1 licensed vendor 上線後此 code 一併移除。

## 驗收條件

- [ ] 以 `QUOTE_PROVIDER=shioaji_demo` 啟動時，可依 active intents 自動完成最多 5 檔訂閱。
- [ ] `QUOTE_PROVIDER` 設為未知值時啟動失敗，error 訊息明示已知值清單。
- [ ] `QUOTE_PROVIDER=in_memory` 啟動時不要求任何 `SHIOAJI_*` env，缺失不報錯。
- [ ] 全 repo grep `shioaji` 應只出現在 `app/services/quote/shioaji_demo/`、`tests/`、本工單文件、env 範例；evaluator / intent service / API 層零命中。
- [ ] Quote callback 進來後，`get_quotes` 可拿到對應 normalized snapshot。
- [ ] 訂閱第 6 檔時拒絕並回 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`。
- [ ] 訂閱白名單外 symbol 時拒絕並回 `SYMBOL_NOT_AVAILABLE_IN_DEMO`，且不向 Shioaji 發出訂閱。
- [ ] `POST /trade-intents` 對白名單外 symbol 於 commit 前回 `SYMBOL_NOT_AVAILABLE_IN_DEMO`，DB 不留下任何 intent。
- [ ] `POST /trade-intents` 建立會超過 5 檔配額的 intent 時，於 transaction commit 前回 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`，DB 不留下未訂閱的 intent。
- [ ] Intent cancel / triggered 後該 symbol 若無其他 active intent，會自動 unsubscribe，釋放配額。
- [ ] App shutdown 會 logout Shioaji 並清空訂閱。
- [ ] Validation 拒絕 out-of-session、crossed、non-positive、insufficient quotes。
- [ ] 缺 bid/ask 時 evaluation 可 fallback last，並記錄 metadata（與 BE-V0.5-09 對齊）。
- [ ] 測試環境以 `QUOTE_PROVIDER=in_memory` 切換為 `InMemoryQuoteProvider`，integration tests 不依賴 Shioaji 網路、不需要任何 `SHIOAJI_*` env。
- [ ] 不存在 `POST /dev/quotes` endpoint（避免回退到 BE-V0.5-08 的設計）。

## 測試要求

- Unit test：crossed quote rejected。
- Unit test：negative / zero price rejected。
- Unit test：out-of-session quote rejected。
- Unit test：last-only quote passes validation but marks fallback path。
- Unit test：subscribe 第 6 檔丟 `QuoteSubscriptionLimitExceeded`。
- Unit test：subscribe 白名單外 symbol（如 `1101`）丟 `SymbolNotAvailableInDemo`，且 mock Shioaji client 沒被呼叫。
- Unit test：unsubscribe 後配額釋放，可再訂閱新 symbol。
- Unit test：Shioaji callback payload 正確 normalize 成 `QuoteSnapshot`（用 fake callback 觸發，不打真網路）。
- Integration test（in_memory provider）：BE-V0.5-09、BE-V0.5-12 的 quote-driven 流程仍綠燈。
- 手動驗證：以真實 Shioaji 帳號訂閱 1–2 檔活躍標的，確認 evaluator 可在盤中觸發 demo intent。

## 工程注意事項

**遷移摩擦預防（最高優先）**：

- Demo / production 切分是設計核心；review 時若看到任何 demo-specific 邏輯（白名單、5 檔配額、`SHIOAJI_*` env 讀取、Shioaji SDK 呼叫）出現在 `shioaji_demo/` 以外的檔案，視為阻塞性 issue 必須修。
- `evaluator` / `intent service` / API route 只能 import `app.services.quote.base` 與 `app.services.quote.factory`，**不可直接 import `shioaji_demo` 任何 symbol**（包含 error class）。Demo-only error code 由 factory / dispatcher 層轉成 envelope。
- 在 PR template / CI 加 `grep -r "shioaji" src/app --exclude-dir=services/quote/shioaji_demo` 守備（命中即 fail），防止之後 review 時遺漏。
- `factory.py` 採 **lazy import**：`shioaji_demo` 整包僅在 `QUOTE_PROVIDER=shioaji_demo` 時於函式內 `import`，不在 module top-level import。好處：
  - `QUOTE_PROVIDER=in_memory`（CI / 測試）runtime 不載入 Shioaji SDK，CI 環境可不裝 `shioaji` 套件。
  - V1 刪 `shioaji_demo/` 時不會留下「其他模組 top-level import 了將被刪的 package」的副作用，移除是純機械操作。
  - 啟動時若選到 `shioaji_demo` 但 SDK 未安裝，error 訊息明示「請安裝 shioaji 套件」而不是 ImportError 出在無關位置。

**Shioaji SDK 細節**：

- Shioaji SDK 是 callback-driven 且帶自家 thread；存取共用 dict 一律加鎖，或用 `threading.RLock` / `asyncio.Lock` 配合 event loop bridge。注意不要在 callback thread 直接呼叫 SQLAlchemy session。
- Shioaji 推送的時間欄位需明確轉為 Asia/Taipei tz-aware datetime，再交給 session validation。
- Shioaji credentials 屬機密；不可寫進 repo、不可寫進 log，啟動 log 僅印 `shioaji login ok` 之類訊息。

**其他**：

- Quote validation logic 必須放在 `app/services/quote/validation.py`（base 共用），所有 provider 共用同一份。
- 5 檔限制是 demo 取捨；error message 與文件需明寫，避免後續誤以為是 bug。
- Provider 命名避免 `FakeProductQuoteProvider` 之類容易誤解；使用 `ShioajiQuoteProvider` 與 `InMemoryQuoteProvider`。
- 不要為 V0.5 建 quote history table。
- 更新 BE-V0.5-11 handoff 文件：移除 `POST /dev/quotes` 範例，補上 quote provider 切換說明、5 檔限制、白名單。
- 更新 BE-V0.5-12 integration tests 的 provider 注入方式（透過 `QUOTE_PROVIDER=in_memory`）。
