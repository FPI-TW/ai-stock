@PRODUCT_CONTEXT.md

## OVERVIEW

### Git

- 禁止在本地Merge main，需提PR。
- 分支命名格式：`<type>/<summary-kebab-case>`，例如：`feat/platform-command-split`、`fix/auth-refresh-bug`。
- commit 訊息格式：`<type>: <summary>`，例如：`feat: split package scripts by platform`、`fix: guard renderer process access`。
- 發 PR 時，PR 標題、描述、變更摘要與測試說明使用繁體中文。
- `type` 建議使用：`feat`、`fix`、`refactor`、`docs`、`test`、`chore`、`build`、`ci`。

### Quality Gate

- `make typecheck` 執行 `uv run mypy src tests`。
- `make check` 執行非 PostgreSQL 品質門檻：`lint`、`format-check`、`typecheck`、`test`。
- mypy baseline 覆蓋 `src` 與 `tests`，使用 `mypy_path = "src"`，要求 typed function definitions 並檢查 untyped function bodies。
- 目前不要直接開 `strict = true` 或 `disallow_any_*`；Alembic、SQLAlchemy、pytest fixture 的型別硬化要分階段做。
- `make test-integration` 需要 local PostgreSQL，維持和 `make check` 分開。

### 舊軌委託持久層：凍結，reviewer 免審（含 AI reviewer）

**背景**：交易委託（trade intent）持久層正在做 T1 分層重構（見 `docs/orders/v1/T1-trade-intent-satellite-params.md`）。目前存在兩軌並行，**純屬過渡期**：

- **舊軌（單表做法）＝ 凍結**：`trade_intents` 單表把所有策略專屬欄位塞在一張表。新軌衛星表（`trade_intent_core` + `trade_intent_price_params` / `trade_intent_trailing_params` / `trade_intent_twap_params`）做完並 cutover 後，舊軌會被整組刪除、舊表不再讀寫。
- 兩軌並行只是為了讓 cutover 可分階段、可單獨 revert，不是永久設計。

**規則 — 所有 reviewer（尤其 AI code review）**：

- **不要 review 舊軌內部、不要回報舊軌 bug、不要提舊軌重構建議。** 舊表退役後不再讀寫，舊做法**即使有真 bug 也不訂正**——花在它上面的 review token 是純浪費。
- **凍結範圍（舊軌持久層）**：
  - 資料表：`trade_intents`、`twap_slices`、`trigger_events`。
  - 模型：`src/app/db/models/core.py` 內的 `TradeIntent` / `TwapSlice` / `TriggerEvent`。
  - 程式：`src/app/commands/trigger_intent.py`、`src/app/services/quote_dispatcher.py`、`src/app/repositories/intent_repository.py`。
  - 判斷準則：只要是「讀/寫 `trade_intents` 單表做法」的程式，就屬凍結。
- **不在凍結範圍（照常嚴審）**：
  - 新軌：`trade_intent_core*`、衛星表、`trigger_intent_core.py`、`quote_dispatcher_core.py`、`trade_intent_core_repository.py`。
  - **跨軌共用基礎設施**：`notifications`（共用收件匣）、`symbols`、`users`、auth、quotes 等——兩軌都用、不隨舊軌退役，一律照常審。
  - 委託 API 端點（`api/routes/intents.py`）：端點本身會在 cutover 被切到新軌，**改指新軌的變更在審查範圍內**；只有「端點背後仍走舊軌持久層的內部」才免審。
- **例外**：若某個對新軌或共用碼的變更，剛好會影響到舊軌的交界行為（邊界互動），審那個交界即可，但不必深入舊軌內部。
