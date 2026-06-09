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
