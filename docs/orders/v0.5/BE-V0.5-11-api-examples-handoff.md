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
- Development quote adapter 不是正式行情功能。

### Main Flow

文件需用完整 request/response 範例覆蓋：

1. Health check。
2. Symbol lookup。
3. Create buy alert。
4. Set dev quote。
5. Evaluate quotes。
6. Get trade intent list/detail。
7. Get notifications。
8. Mark notification read。
9. Cancel active alert。

### Error Examples

至少包含：

- `UNKNOWN_SYMBOL`
- `INVALID_TICK_SIZE`
- `DUPLICATE_INTENT`
- `QUOTE_UNAVAILABLE`

## 驗收條件

- [ ] Examples 覆蓋 create、quote update、evaluate、cancel、list、notification list/read。
- [ ] Error envelope examples 包含 V0.5 核心 error codes。
- [ ] 文件清楚標示 V0.5 無 auth、不部署、不公開。
- [ ] 前端可用 examples 跑通本地主流程。

## 測試要求

- 手動或自動驗證 examples 中的 JSON payload 仍可被 API 接受。
- 若 repo 有 docs lint，可通過。
- 若 OpenAPI 已可產生，確認 examples 與 schema 欄位命名一致。

## 工程注意事項

- 欄位命名需與實作一致，例如 `quantityLots`、`targetPrice`。
- Price examples 用 string。
- 不要在 handoff 文件承諾 V1 才有的 CSV/Telegram/OCO。
