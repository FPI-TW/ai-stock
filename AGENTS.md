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


# AI 程式碼風格約束：一律使用 蛇形命名法 (snake_case)
## 🎯 核心規則
1. **絕對蛇形命名 (`snake_case`)：**
   - 所有由多個單字組成的識別碼（Identifiers），一律使用小寫字母，並用單一底線 `_` 分隔。
   - 除非下方有明確豁免，否則絕不允許使用 `camelCase`（駝峰命名）、`PascalCase`（大駝峰命名）或 `kebab-case`（短棒命名）。
2. **強制執行範圍：**
   - **變數與常數：** 所有區域變數、全域變數、模組級常數（例如：`user_id`、`max_retry_count`）。
   - **函式與方法：** 所有函式名稱、類別的私有/公開方法（Methods）、非同步處理函式。
   - **資料庫元件：** 資料表欄位名、資料表名、外鍵（Foreign keys）以及原生 SQL 的別名（Alias）。
   - **檔案與目錄名稱：** 所有新建立的原始碼檔案、測試檔案以及資料夾結構。
3. **明確豁免範圍：**
   - **類別名稱 (Class Names)：** 必須使用 `PascalCase`（例如：`class UserService:`），以符合物件導向程式設計（OOP）的標準規範。
   - **對外 API JSON 欄位：** 對外 HTTP 介面的 JSON 請求體 / 回應欄位 / query 參數**一律 camelCase**（例如 `quantityLots`、`termsVersion`、`requestId`），與 V0.5 既有 API 一致。內部 Python 屬性、command/domain 欄位、DB 欄位仍維持 snake_case，透過 Pydantic `validation_alias` / response alias 轉換。**唯一豁免在「序列化出入口的鍵名」，跨過邊界後立即回到 snake_case。**
   - **第三方套件：** 如果外部套件或框架有強制的命名規範（例如 FastAPI 的 `Depends`），請遵循該套件的要求。但只要可行，請盡量用 `snake_case` 包裝或設定別名。
---
## ❌ 錯誤範例（絕對不要這樣寫）
```python
# 錯誤的變數與函式命名
userId = 123                  # ❌ 駝峰命名 (camelCase)
User_Profile = {}             # ❌ 大小寫混用
def getActiveUsers(): pass    # ❌ 駝峰命名 (camelCase)
# 錯誤的 API JSON 欄位 / 字典鍵值
user_payload = {
    "isCompleted": True,      # ❌ API 回應中出現 camelCase
    "create-time": "2026-06"  # ❌ 欄位出現 kebab-case
}
# 錯誤的檔案名稱
# ❌ userController.py
# 正確的命名方式
user_id = 123
user_profile = {}
def get_active_users(): pass
# 正確的 API JSON 欄位 / 字典鍵值
user_payload = {
    "is_completed": True,     # ✅ 乾淨的 snake_case
    "create_time": "2026-06"  # ✅ 乾淨的 snake_case
}
# 允許的類別名稱豁免
class TodoRepository:         # ✅ 只有 Class 名稱可以使用 PascalCase
    def get_by_id(self): pass # ✅ 類別內部的方法依然維持 snake_case
# 正確的檔案名稱
# ✅ user_controller.py


# AI 開發約束：Util 函式拆分與重複檢查規範
## 🎯 核心規則
1. **先檢查，後動手（Check Before Build）：**
   - 當你認為某段邏輯適合抽離成 `util function` 時，**絕對不允許**直接建立新檔案或直接寫出新函式。
   - 你必須先主動掃描、閱讀本地專案中現有的工具資料夾（例如 `utils/`、`helpers/`、`tools/` 等）。
2. **判定複用標準：**
   - **完全相同：** 如果已有相同功能的函式，直接引用，禁止重寫。
   - **功能相似：** 如果現有函式的邏輯有 70% 相似，你應該優先選擇**重構（Refactor）現有函式**（例如增加選填參數、擴充泛型），而不是建立一個「長得很像」的新功能。
   - **確認無效：** 只有當你百分之百確認本地現有的所有工具函式，都「完全無法滿足需求」且「硬要修改會破壞原有邏輯」時，才允許建立新的工具功能。
3. **落實單一職責與命名：**
   - 新建的 `util function` 必須是純文字處理、數學計算、時間轉換等「無副作用（Pure Function）」的純邏輯。
   - 命名必須精準反映功能。
---
## ❌ 錯誤行為（絕對不要這樣做）
* **錯誤情境：** 專案中已經有一個 `utils/time_helper.py` 裡面包含 `format_timestamp()`。
* **AI 的錯誤做法：**
  ```python
  # ❌ 沒檢查本地，直接在新的業務邏輯裡又順手寫了一個工具函式
  def convert_epoch_to_string(epoch):
      return datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M:%S')
## ✅ 正確行為（請務必這樣執行）
AI 的正確思考與執行步驟：
思考： 「這個簡訊驗證碼隨機產生的邏輯，好像可以抽成工具。」
檢查： 自動下命令搜尋或讀取 utils/ 底下的檔案（如 utils/string_util.py、utils/crypto.py）。
發現： 發現現有檔案中只有 generate_random_uuid()，沒有數字隨機產生器。
回報與建立： 在對話中主動說明：「經檢查本地 utils/ 檔案，目前無隨機數字功能，我將在 utils/string_util.py 中新增 generate_random_digits 函式。」
## 🚨 執行檢查機制
在提出任何新寫的工具函式、或建立新 util 檔案之前，請在內心（以及對話中）回答以下三個問題：
「我剛剛已經用關鍵字搜尋、或讀取過本地的 utils/ 資料夾了嗎？」
「現有的工具函式中，真的沒有任何人能透過改動來實現這個功能嗎？」
「如果代班的同事來看了這個新函式，他會不會覺得這其實是重複的東⻄？」
如果上述問題沒有明確的答案，請先對本地 utils 執行讀取與分析，並向使用者回報你的檢查結果。