@docs/product.md

## OVERVIEW

### Git 與 PR

- 禁止在本地 merge `main`，所有變更必須透過 PR。
- 分支命名格式為 `<type>/<summary-kebab-case>`，例如 `feat/platform-command-split`、`fix/auth-refresh-bug`。
- commit 訊息格式為 `<type>: <summary>`，例如 `feat: split package scripts by platform`。
- 建議 type：`feat`、`fix`、`refactor`、`docs`、`test`、`chore`、`build`、`ci`。
- PR 標題、描述、變更摘要與測試說明使用繁體中文。
- Review PR 時，未經允許不得將結果推送到遠端。
- Node 執行版本以 nvm 預設值為準。

### Quality Gate

- `make typecheck` 執行 `uv run mypy src tests`。
- `make check` 執行非 PostgreSQL 品質門檻：lint、format-check、typecheck、Shioaji 隔離檢查與 tests。
- mypy 覆蓋 `src` 與 `tests`，使用 `mypy_path = "src"`，要求 typed function definitions 並檢查 untyped function bodies。
- 不要直接開啟 `strict = true` 或 `disallow_any_*`；Alembic、SQLAlchemy、pytest fixture 的型別硬化需分階段進行。
- `make test-integration` 需要本地 PostgreSQL，與 `make check` 分開執行。

### 文件維護

- 文件入口與權威來源定義見 `docs/index.md`。
- 已實作行為必須依 code、schema、migration 與 OpenAPI 更新主題文件，不得從歷史工單反推現況。
- `docs/orders/` 只放尚未完成且已定案的複雜工單；小型 bug／enhancement 使用 GitHub Issues。
- 工單完成的同一個 PR 必須同步主題文件並刪除工單；取消或被取代時直接刪除，不建立 archive 或完成清單。
- 禁止新增第二份白話版、版本副本或目錄 README；需要導覽時使用 `index.md` 與連結。

### 舊軌委託持久層：凍結，reviewer 免審

背景與待退役範圍見 `docs/technical-debt.md`。新軌已承接非 TWAP 的對外委託流程；TWAP 與舊軌仍作為過渡程式存在。

- 不要 review 舊軌內部、回報舊軌 bug 或提出舊軌重構建議；舊軌退役後不再讀寫。
- 凍結資料表：`trade_intents`、`twap_slices`、`trigger_events`。
- 凍結模型：`src/app/db/models/core.py` 內的 `TradeIntent`、`TwapSlice`、`TriggerEvent`。
- 凍結程式：`src/app/commands/trigger_intent.py`、`src/app/services/quote_dispatcher.py`、`src/app/repositories/intent_repository.py`，以及所有讀寫 `trade_intents` 單表的路徑。
- 新軌 `trade_intent_core*`、衛星表、`trigger_intent_core.py`、`quote_dispatcher_core.py`、`trade_intent_core_repository.py` 需正常嚴審。
- `notifications`、`symbols`、`users`、auth、quotes 等跨軌共用基礎設施需正常嚴審。
- 若新軌或共用碼的變更影響跨軌交界，只審交界行為，不深入舊軌內部。
