# _archive — 收斂前舊工單（僅供溯源）

本目錄保存 **2026-06-03 範圍收斂前**的 V1 後端工單與舊索引，**已不再維護**，僅供歷史溯源。

## 為什麼封存

舊 20 張（`BE-V1-01…20`）與舊索引 `v1-backend-work-orders.legacy.md` 成形於 2026-05-22，**早於 06-03 的 V1 範圍收斂**（domain-spec commits `66a230c`/`ced9310`/`46ad028`）。收斂把以下能力改判延後或移除，舊工單已過時：

- 漲跌停驗證、`invalid_for_day` 生命週期 → 待券商對接 / 正式營運
- 開盤前臨時休市改期、symbol master / market calendar 自動匯入 → 待券商對接（V1 用 seed + 固定時段）
- 行情健康層（`quote_unhealthy` / `last_quote_health`）→ 全除
- 後端 CSV 整套 → 移除改純前端
- TWAP → V0.5 已交付

## 現行工單

以 `docs/orders/v1/`（上層）的 **L1–L3（上線前）+ P1–P6（上線後）** 為準，總索引見 `docs/v1-backend-work-orders.md`。

舊→新對應見總索引 §6。
