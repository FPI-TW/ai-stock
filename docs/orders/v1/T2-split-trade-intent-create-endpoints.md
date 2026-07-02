# T2：trade_intents create endpoint 按策略拆分（消除 union getattr）

## Metadata

- 分層：上線後（API 可讀性 / 型別重構，非阻擋上線；可獨立排程）
- 優先序：T2（T = 技術債 / 重構）
- ROM：**S**
- 依賴：無功能依賴。與 [T1](T1-trade-intent-satellite-params.md)（schema 衛星表分層）是**兩件獨立事**——本票只動 API 層、**單表不動**；T1 只動 DB / repository 層。兩票可任意順序。
- 交付版本：V1
- 來源：V0.5 遺留問題（單一 `POST /trade-intents` 用 discriminated union 接所有策略，靠 `getattr` 取策略專屬欄）
- **狀態：已實作於分支 `feat/split-trade-intent-create-endpoints`（commit `c598555`），本工單為事後追蹤文件**

## 背景

V0.5 的建單只有單一入口 `POST /trade-intents`，request body 是一個涵蓋所有策略的大模型，handler 與 command 必須用 `getattr` / 條件分支去取「這個策略才有的欄位」（target_price / trail_* / twap_* …）。問題同 T1 的單表爆量，只是發生在 API 層：

- 單一 request model 大半欄位對任一策略都是 optional，OpenAPI / 前端看不出「這個策略到底該帶哪些欄」。
- handler 取值靠 `getattr`，型別不收斂、容易漏驗。
- 加策略 = 再往大模型塞 optional 欄 + 再加一條分支。

## 結論（已實作）

**每個策略各給一個 typed create endpoint**，body 只對應**單一** request model（無 union、無 `getattr`）。legacy `POST /trade-intents` **保留**直到前端遷移完成。共用的冪等 + 命令 + 回應串接收斂到一個 `_run_create_intent` helper。

### 新增端點（`src/app/api/routes/intents.py`）

| 端點 | request model | handler |
|---|---|---|
| `POST /trade-intents/buy-price-alert` | `BuyPriceAlertCreateRequest` | `create_buy_price_alert` |
| `POST /trade-intents/sell-price-alert` | `SellPriceAlertCreateRequest` | `create_sell_price_alert` |
| `POST /trade-intents/limit-buy-order` | `LimitBuyOrderCreateRequest` | `create_limit_buy_order` |
| `POST /trade-intents/limit-sell-order` | `LimitSellOrderCreateRequest` | `create_limit_sell_order` |
| `POST /trade-intents/market-order` | `MarketOrderCreateRequest` | `create_market_order` |
| `POST /trade-intents/trailing-stop-alert` | `TrailingStopAlertCreateRequest` | `create_trailing_stop_alert` |

- TWAP 維持既有 `POST /trade-intents/twap/preview` + `/twap/confirm` 兩段流程，不變。
- request model 已存在於 `src/app/schemas/intent.py`（`_BaseIntentCreateRequest` → `_TargetPriceIntentCreateRequest` / `_LimitOrderCreateRequest` 繼承樹），本票只接線端點，不新增模型。
- 共用串接 `_run_create_intent(command, idempotency, *, user_id, idempotency_key, endpoint, inp, payload)`：跑 `CreateTradeIntentCommand` → 包 `IntentCreateResponse` → 過 `IdempotencyManager.run`。每端點各帶自己的 `endpoint` 字串（冪等紀錄區分）。
- 每端點沿用既有防護：`ActiveUserDep`、`enforce_mutation_rate_limit`、`Idempotency-Key`。

## 非目標

- **不動 DB schema / 單表結構**（衛星表分層是 T1）。
- 不改 `CreateTradeIntentCommand` 的業務語意（命令仍吃 `CreateTradeIntentInput`，端點只負責把 typed request 組成 input）。
- 不移除 legacy `POST /trade-intents`（待前端遷移完成另票退場）。
- 不動 cancel / list / detail / twap 流程。

## 介面

- **新增 6 個 create 端點**（見上表），各 201 + `IntentCreateResponse`。
- legacy `POST /trade-intents` 保留，行為不變。
- 對外 JSON：各端點 request 只含該策略需要的欄位（camelCase 維持既有慣例）；response 與 legacy 完全一致（共用 `map_to_response_data`）。

## 驗收條件

- [x] 6 個 per-strategy 端點建立，各對應單一 request model，無 union / `getattr`。
- [x] 每端點吃對的策略欄位（market-order 不收 target_price；limit order 收 transaction_mode / notification_mode）。
- [x] 冪等：每端點各自 `endpoint` 字串 + `Idempotency-Key`，重放回原結果不重複建單。
- [x] 限流：每端點掛 `enforce_mutation_rate_limit`，與 legacy 共桶（`mutation:{user_id}`）。
- [x] response 與 legacy `POST /trade-intents` 位元一致（同 `map_to_response_data` + `IntentCreateResponse`）。
- [x] legacy 端點保留可用。
- [x] `make check` 全綠（mypy：typed request 無 `getattr`）。

## 測試要求

- 已新增 `tests/api/test_trade_intents_split_endpoints.py`：六端點各建單成功、策略欄位驗證（缺/多欄位 422）、冪等重放、限流。
- legacy 端點既有測試維持綠。

## 工程注意事項

- 共用串接只抽一個 `_run_create_intent`（跨 6 端點重用，屬合理共用），不另做 strategy registry / factory（避免過度抽象——策略分支已由「各自獨立 typed 端點」表達，不需執行期分派）。
- request model 繼承樹放在 `schemas/intent.py`，貼近使用處；端點只把 typed request 攤平成 `CreateTradeIntentInput`。
- legacy 與新端點共用同一 `CreateTradeIntentCommand` 與同一限流桶，行為一致、無雙寫風險。
- 命名遵循 snake_case（handler / 變數）；request model class 用 PascalCase。
