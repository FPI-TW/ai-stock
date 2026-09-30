# 已知技術債

本文件只記錄已確認但尚未形成正式執行工單的缺口，不代表排程或承諾。開始處理前應建立 GitHub Issue；需要完整設計時再新增 `docs/orders/` 工單。

## 舊軌交易意圖持久層待退役

非 TWAP 的 create、list、detail 與 cancel 已切到 `trade_intent_core` 新軌，但 TWAP confirm／slice worker 仍讀寫舊軌；舊 dispatcher 也仍在啟動與 quote callback 路徑中運行：

- 舊表：`trade_intents`、`twap_slices`、`trigger_events`。
- 舊模型：`src/app/db/models/core.py` 的 `TradeIntent`、`TwapSlice`、`TriggerEvent`。
- 舊路徑：`commands/trigger_intent.py`、`services/quote_dispatcher.py`、`repositories/intent_repository.py` 及相關 lifecycle／subscription 接線。

風險是 TWAP 與非 TWAP 的對外讀寫來源不一致、維護者容易誤改錯軌、啟動時同時掃描兩套表，且每筆 quote 同時經過兩個 dispatcher。完整退役前必須先完成 TWAP 新軌 slice schema、command、API 與 worker cutover，確認 production 舊表沒有需保留的有效資料，再以 migration 與程式刪除一次移除舊垂直切片；在正式排程前維持 AGENTS.md 的 reviewer freeze。

`notifications`、`symbols`、`users`、auth、quotes 與其他跨軌資源不是舊軌，不能連帶刪除。

### 舊軌 TWAP 在 per-user 行情模式下沒有參考價

`QUOTE_PROVIDER=fubon` 沒有系統券商 session，舊軌 `QuoteEvaluationDispatcher` 不掛，`TwapSliceScheduler` 注入空的 `InMemoryQuoteProvider`：slice 照時間通知、沒有參考價，TWAP confirm 也只在 owner 已綁定時才能訂閱。TWAP cutover 到新軌後改以 `pool.get(owner)` 取價一併解除。

## per-user 行情的已知低效

- 每筆 quote 每條 session 都會先跑一次 `IntentLifecycleCommand` 的 UPDATE，再依 owner 過濾 intent；N 位使用者同時看同一檔就是 N 次生命週期掃描。單機個位數使用者可接受，人數上升時改為只在 dispatcher 內定時跑一次。
- dispatcher 觸發後不退訂該 symbol（只有 cancel 會退訂）；已觸發的單留下的訂閱要等該使用者下次取消同標的、或重啟時 reconcile 才清掉。

## per-user 行情 session 的兩個無法自癒情境

- **靜默死亡的行情連線**：重連迴圈只看 `realtime_connected` 與 `login_alive` 兩個旗標，旗標只由 SDK 的 `disconnect` 與交易端事件 300／301／304 設定。若 SDK 的 WebSocket reader 執行緒因 Python 例外自行結束而沒有發 `disconnect`，旗標維持 True，該 session 看起來活著卻收不到任何 frame，迴圈不會修。工單明確不做行情健康層，所以目前沒有以「多久沒收到 frame」判斷的看門狗；若正式環境觀察到這種情況，最小做法是在 provider 記最後 frame 時間、迴圈在交易時段內對超過門檻者視同斷線重連（冷門股 frame 間隔可達數分鐘，門檻要以 REST 現價或跨標的比對佐證，不能單看時間）。
- **SDK 原生層崩潰拖垮整個 process**：SDK 核心為 Rust，panic 跨 FFI 會 abort、SIGSEGV 同理，Python 層無法攔截；N 條 session 同在一個 process，一人的 SDK 崩潰就是全員斷線，只能靠 Compose `restart: unless-stopped` 拉起並平行重登（vendor 筆記記錄 2.2.8 `bank_remain()` 曾如此）。真正隔離需要每位使用者一個子 process 跑 SDK、主 process 只做 API，屬架構層級改動，等正式環境實際發生再評估。降低機率的通則：任何新接的 `sdk.*` API 先在獨立行程試打。

## 備份與災難復原未驗證

Repository 有 EC2／RDS 部署流程，但沒有版本化的 RDS backup／PITR policy、restore drill 紀錄與可重複執行的復原 runbook。任何 RPO／RTO 數字目前都不是已驗證承諾。

## Production 驗收資料不足

目前缺少一份與實際環境對齊的 production smoke checklist、還原演練結果與完整 SLO／告警觀測。`GET /health` 只驗證 DB connectivity，不能代表 quote、Telegram、SES 或 background scheduler 全部健康。

## 通知交付為 best-effort

站內通知與 intent trigger 位於同一 DB transaction；Telegram 在 commit 前直接呼叫，且沒有 transactional outbox、delivery 狀態與重試 worker。短暫失敗可能造成外部通知遺失；若外部發送成功但後續 DB transaction 失敗，也可能出現 Telegram 已送出而站內資料未落地的不一致。

小型 bug／enhancement 的即時狀態只在 [GitHub Issues](https://github.com/FPI-TW/ai-stock/issues) 維護，本文件不複製 issue 清單。
