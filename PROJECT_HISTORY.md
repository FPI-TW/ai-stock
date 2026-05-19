# 專案開發歷史

## 2026-05-19
### 任務：IntentRepository 切換至 PostgreSQL
- **修改檔案**：
  - `src/app/repositories/intent_repository.py`（重寫：JSON 檔案 → SQLAlchemy Session）
  - `src/app/api/deps.py`（`get_intent_repository` 改注入 `DatabaseDep`）
  - `src/app/core/config.py`（移除 `intents_file` 欄位）
  - `src/app/domain/trade_intent.py`（移除 `to_dict`/`from_dict`）
- **達成目標**：`IntentRepository` 不再讀寫 JSON 檔案，改用 PostgreSQL。`create`/`find_by_id`/`list_by_owner`/`cancel` 四個方法均以 SQLAlchemy 實作，Keyset cursor pagination 移植為 SQL WHERE 條件，duplicate check 雙層防護（應用層查詢 + DB `IntegrityError`）。所有 tests pass，mypy 及 ruff 全數通過。

## 2026-05-18
### 任務：BE-V0.5-07 Price Alert Create/Cancel/List APIs
- **修改檔案**：
  - `src/app/domain/intent_errors.py`（新增）
  - `src/app/domain/trade_intent.py`（新增：TradeIntentData dataclass）
  - `src/app/repositories/intent_repository.py`（新增：JSON 檔案持久化）
  - `src/app/commands/__init__.py`（新增）
  - `src/app/commands/trade_intent.py`（新增：CreateTradeIntentCommand、CancelTradeIntentCommand）
  - `src/app/schemas/intent.py`（重寫：camelCase request/response schemas）
  - `src/app/api/errors.py`（更新：新增 DUPLICATE_INTENT、FORBIDDEN、UNSUPPORTED_INSTRUMENT 等 error codes 與 handlers）
  - `src/app/api/deps.py`（更新：新增 IntentRepoDep、CreateTradeIntentCommandDep、CancelTradeIntentCommandDep）
  - `src/app/api/routes/intents.py`（重寫：4 個 endpoints）
  - `src/app/main.py`（更新：route prefix /intents → /trade-intents）
  - `src/app/core/config.py`（更新：新增 INTENTS_FILE 設定）
  - `tests/api/conftest.py`（更新：加入 mock_intent_repository fixture）
  - `tests/api/test_intent_symbol_validation.py`（更新：URL 與 payload 格式）
  - `tests/api/test_trade_intents.py`（新增：11 條 API tests）
- **達成目標**：完整實作 POST/GET list/GET detail/POST cancel 四個 trade-intent API endpoints；持久化暫用 JSON 檔案（data/trade_intents.json）；所有驗收條件通過，204 tests all green，mypy + ruff clean。

## 2026-05-13
### 任務：BE-V0.5-06 基本 Trading Session / Day-Intent Rules
- **修改檔案**：
  - `src/app/domain/trading_session.py`（新增）
  - `tests/test_trading_session.py`（新增）
- **達成目標**：實作 `TradingSessionService`（Asia/Taipei 時區、Mon-Fri 交易日、09:00-13:30 regular session）、`OutsideSessionError`、`verify_trading_hours` evaluator guard。25 unit tests 全通過，`make check` 品質門檻 clean。
