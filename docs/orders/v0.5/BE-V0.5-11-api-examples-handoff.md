# BE-V0.5-11：本地主流程 API Examples 與 Frontend Handoff

## Metadata

- 類型：AFK
- 優先序：P1
- 預估：11h
- 依賴：BE-V0.5-07, BE-V0.5-10
- 交付版本：V0.5

## 背景

前端會另開 repo。V0.5 需要提供足夠清楚的 API examples，讓前端工程師能在本地串完整主流程，而不需要讀後端程式碼猜 payload。

## 目標

- 提供本地主流程 API examples。
- 說明 local mode 限制。
- 說明 error envelope。
- 說明 quote update / evaluate 的本地用途。

## 非目標

- 不產正式完整 OpenAPI contract。
- 不寫前端程式。
- 不提供 production deployment guide。
- 不記錄 CSV/Telegram/admin APIs。

## 建議文件位置

可新增：

```text
docs/api/v0.5-local-flow.md
```

或若專案已有 API docs 慣例，依慣例放置。

## 必備內容

### Local Mode Warning

需明確寫：

- V0.5 無 auth。
- 不部署、不公開。
- `/dev/*` endpoints 僅 local mode 可用。
- Quote 來源為 Shioaji 即時行情訂閱（BE-V0.5-13），demo 等級最多同時訂閱 **5 檔**；超出會回 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`。
- `GET /quotes/current-price/{symbol}` 是 Shioaji 測試查價 API，僅允許 `2330` / `2317` / `0050` / `00878`。此 API 依賴 `QUOTE_PROVIDER=shioaji_demo`、Shioaji SDK 與 Shioaji 登入憑證，無法單獨存在；詳見 `docs/api-v0.5-shioaji-current-price.md`。
- Shioaji demo 期間**可選標的固定為白名單**：`2330` / `2317` / `0050` / `00878`，其他 symbol 一律回 `SYMBOL_NOT_AVAILABLE_IN_DEMO`。`9999` 停牌測試標的改由前端阻擋，不列入 Shioaji demo provider 白名單。白名單詳見 BE-V0.5-13；V1 licensed vendor 上線後解除。
- Shioaji 為展示用 quote 來源，非正式行情產品；V1 才會評估換 licensed vendor。
- 需設定 `SHIOAJI_API_KEY` / `SHIOAJI_SECRET_KEY`，本地測試可改用 `QUOTE_PROVIDER=in_memory`。

### Main Flow

文件需用完整 request/response 範例覆蓋：

1. Health check。
2. Symbol lookup。
3. Create buy alert（盤中 quote 由 Shioaji 自動推送，無需手動 set quote step）。
4. Manual evaluate（`POST /dev/evaluate-quotes`，僅 local mode；用於測試或在 in-memory provider 場景驅動 evaluator）。
5. Get trade intent list/detail。
6. Get notifications。
7. Mark notification read。
8. Cancel active alert。

### Error Examples

至少包含：

- `UNKNOWN_SYMBOL`
- `INVALID_TICK_SIZE`
- `DUPLICATE_INTENT`
- `QUOTE_UNAVAILABLE`
- `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`
- `QUOTE_PROVIDER_UNAVAILABLE`
- `SYMBOL_NOT_AVAILABLE_IN_DEMO`

## 驗收條件

- [ ] Examples 覆蓋 create、manual evaluate、cancel、list、notification list/read。
- [ ] Error envelope examples 包含 V0.5 核心 error codes（含 `QUOTE_SUBSCRIPTION_LIMIT_EXCEEDED`、`QUOTE_PROVIDER_UNAVAILABLE`）。
- [ ] 文件清楚標示 V0.5 無 auth、不部署、不公開。
- [ ] 文件說明 Shioaji 5 檔訂閱上限與 `QUOTE_PROVIDER` 切換方式。
- [ ] 文件說明 Shioaji 測試查價 API 依賴 Shioaji provider，且只能查 `2330` / `2317` / `0050` / `00878`。
- [ ] 文件**不**出現 `POST /dev/quotes`（V0.5 不再提供本地 set quote endpoint）。
- [ ] 前端可用 examples 跑通本地主流程。

## 測試要求

- 手動或自動驗證 examples 中的 JSON payload 仍可被 API 接受。
- 若 repo 有 docs lint，可通過。
- 若 OpenAPI 已可產生，確認 examples 與 schema 欄位命名一致。

## 工程注意事項

- 欄位命名需與實作一致，例如 `quantityLots`、`targetPrice`。
- Price examples 用 string。
- 不要在 handoff 文件承諾 V1 才有的 CSV/Telegram/OCO。
- 不要把 Shioaji credentials 寫進文件，僅標記為 env var 名稱。
