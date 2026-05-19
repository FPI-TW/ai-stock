# 當前任務：IntentRepository 切換至 PostgreSQL

## 任務拆解
- [x] 步驟 1：重寫 `src/app/repositories/intent_repository.py`（SQLAlchemy Session 取代 JSON 檔案）
- [x] 步驟 2：更新 `src/app/api/deps.py`（`get_intent_repository` 改注入 `DatabaseDep`）
- [x] 步驟 3：移除 `src/app/core/config.py` 的 `intents_file` 欄位
- [x] 步驟 4：移除 `src/app/domain/trade_intent.py` 的 `to_dict`/`from_dict`（JSON 序列化已不需要）
- [x] 步驟 5：`make check` 品質門檻驗證（204 passed, mypy clean, ruff clean）

## 備註
- Keyset cursor pagination 完整移植至 SQL WHERE 條件
- `IntegrityError` 作為 duplicate check 的最後防線（先有應用層查詢，再有 DB 約束）
- 阻塞問題：無
