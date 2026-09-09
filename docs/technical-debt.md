# 已知技術債

本文件只記錄已確認但尚未形成正式執行工單的缺口，不代表排程或承諾。開始處理前應建立 GitHub Issue；需要完整設計時再新增 `docs/orders/` 工單。

## 舊軌交易意圖持久層待退役

非 TWAP 的 create、list、detail 與 cancel 已切到 `trade_intent_core` 新軌，但 TWAP confirm／slice worker 仍讀寫舊軌；舊 dispatcher 也仍在啟動與 quote callback 路徑中運行：

- 舊表：`trade_intents`、`twap_slices`、`trigger_events`。
- 舊模型：`src/app/db/models/core.py` 的 `TradeIntent`、`TwapSlice`、`TriggerEvent`。
- 舊路徑：`commands/trigger_intent.py`、`services/quote_dispatcher.py`、`repositories/intent_repository.py` 及相關 lifecycle／subscription 接線。

風險是 TWAP 與非 TWAP 的對外讀寫來源不一致、維護者容易誤改錯軌、啟動時同時掃描兩套表，且每筆 quote 同時經過兩個 dispatcher。完整退役前必須先完成 TWAP 新軌 slice schema、command、API 與 worker cutover，確認 production 舊表沒有需保留的有效資料，再以 migration 與程式刪除一次移除舊垂直切片；在正式排程前維持 AGENTS.md 的 reviewer freeze。

`notifications`、`symbols`、`users`、auth、quotes 與其他跨軌資源不是舊軌，不能連帶刪除。

## 備份與災難復原未驗證

Repository 有 EC2／RDS 部署流程，但沒有版本化的 RDS backup／PITR policy、restore drill 紀錄與可重複執行的復原 runbook。任何 RPO／RTO 數字目前都不是已驗證承諾。

## Production 驗收資料不足

目前缺少一份與實際環境對齊的 production smoke checklist、還原演練結果與完整 SLO／告警觀測。`GET /health` 只驗證 DB connectivity，不能代表 quote、Telegram、SES 或 background scheduler 全部健康。

## 通知交付為 best-effort

站內通知與 intent trigger 位於同一 DB transaction；Telegram 在 commit 前直接呼叫，且沒有 transactional outbox、delivery 狀態與重試 worker。短暫失敗可能造成外部通知遺失；若外部發送成功但後續 DB transaction 失敗，也可能出現 Telegram 已送出而站內資料未落地的不一致。

小型 bug／enhancement 的即時狀態只在 [GitHub Issues](https://github.com/FPI-TW/ai-stock/issues) 維護，本文件不複製 issue 清單。
