# F1：富邦帳戶現金餘額查詢（後端 → 前端 JSON）

## Metadata

- 分層：上線後（富邦串接第一票；不阻擋既有通知功能）
- 優先序：F1（F = Fubon 券商串接系列，新開前綴；既有 L/P/T 三系列不含券商帳務）
- ROM：**S**
- 依賴：`fubon-neo` 2.2.8 已加入 `pyproject.toml`（本機 wheel，`[tool.uv.sources]`）。無其他功能依賴。
- 交付版本：V1
- 來源：2026-08-03 富邦方向（`PRODUCT_CONTEXT.md` 第 3、4 點）——取得富邦交易帳號後，先做**唯讀帳務**，下單另票。

## 背景

目前系統只有報價與通知，前端沒有任何「這個帳戶還有多少錢」的資訊。富邦 Neo SDK 的
`accounting.bank_remain(account)` 直接回傳交割帳戶餘額，後端只需登入 → 查詢 → 轉成 JSON。

這是**第一支碰富邦 SDK 的程式**，所以本票同時要把「登入 session 怎麼拿、放哪、失敗怎麼辦」
一次定下來，後續下單票直接沿用，不要各寫一套。

## 結論（要做什麼）

新增一支唯讀端點 `GET /account/balance`，後端登入富邦 SDK、呼叫 `accounting.bank_remain`，
把餘額打包成 JSON 回前端。

### 富邦 API 事實（`fubon-api.md` 45393–45450）

```py
from fubon_neo.sdk import FubonSDK
sdk = FubonSDK()
accounts = sdk.login("ID", "password", "cert path", "cert password")
result = sdk.accounting.bank_remain(accounts.data[0])
```

`Result` 欄位：`is_success: bool` / `message: str | None` / `data: BankRemain`。

`BankRemain`：`branch_no`(str)、`account`(str)、`currency`(str)、`balance`(int)、`available_balance`(int)。

⚠️ 文件的回傳範例把 `balance` / `available_balance` 寫成字串（`"666666"`）但型別標 int，
**兩者不一致**。施工時一律 `int(...)` 轉一次再放進 response model，不要直接信任型別。

### 對外介面

`GET /account/balance`，掛 `ActiveUserDep`（登入才可查）。

Response 200（JSON 欄位維持既有 camelCase 慣例）：

```json
{ "data": { "currency": "TWD", "balance": 666666, "availableBalance": 123456, "queriedAt": "2026-08-24T01:23:45Z" } }
```

- **不回傳 `branch_no` / `account`**：單一部署只有一組券商帳號，前端不需要辨識帳號；帳號屬個資，少回一欄少一個外洩面。日後前端真的要顯示，再回遮罩後四碼。
- `queriedAt`：這次向券商取值的 UTC 時間（後端不快取，等同即時）。

錯誤碼：

| 情境 | 狀態碼 | 回應 |
|---|---|---|
| 未設定富邦憑證（CI / 未接帳號的部署） | 503 | 通用訊息「券商帳務服務未啟用」 |
| 登入失敗 / `is_success = false` / SDK 例外 | 502 | 通用訊息「查詢券商餘額失敗」 |

⚠️ **券商回傳的 `message` 只寫 server log，不可回給前端**（避免洩漏券商內部錯誤細節，
對齊 `SECURITY_AUDIT.md` 既有原則）。log 也不得出現帳號、密碼、憑證路徑。

### 程式落點

- `src/app/services/fubon/account_balance.py`（新增，單一模組）：SDK 登入 + `bank_remain` 查詢 + 轉成 dataclass。
- `src/app/schemas/account.py`（新增）：response model。
- `src/app/api/routes/account.py`（新增）+ 在 router 註冊。
- `src/app/core/config.py`：新增下方環境變數。

**不建 `BrokerClient` interface / ABC / factory**——目前只有一家券商、只有一個實作。
第二家券商出現時再抽（`PRODUCT_CONTEXT.md` 第 1 點明文要求）。

### 登入 session 處理

- SDK 登入昂貴且有 session 概念，**module 級延遲初始化 + `threading.Lock` 保護**，不要每次請求重登。
- 查詢失敗且疑似 session 失效時，**重登一次並重試一次**就好；再失敗直接 502。不要做重試佇列 / 指數退避。
- SDK 是**同步阻塞**：端點用 `def`（FastAPI 自動丟 threadpool），**不要寫 `async def`**，否則會卡住 event loop。

### 環境變數（`.env`，不入 repo）

| 變數 | 說明 |
|---|---|
| `FUBON_ENABLED` | bool，預設 `false`。false 時端點回 503，其餘變數不讀。 |
| `FUBON_ID` | 身分證字號 |
| `FUBON_PASSWORD` | 登入密碼（`SecretStr`） |
| `FUBON_CERT_PATH` | 憑證檔路徑 |
| `FUBON_CERT_PASSWORD` | 憑證密碼（`SecretStr`） |

- 密碼欄位一律用 pydantic `SecretStr`，避免被 `repr` / log 帶出。
- `FUBON_ENABLED=true` 但其餘任一未設 → 啟動時就 fail fast（比照 `_enforce_shioaji_credentials` 的做法）。
- 憑證檔放 `.gitignore` 已涵蓋的 `key/` 目錄，**不可 commit**。
- 公版填券商測試環境帳號，客戶版換填客戶自己的；**不做 per-user 綁定券商帳號**。

## 非目標

- **不下單**、不查委託、不查成交（下單是另一系列票）。
- 不做庫存（`inventories`）、未實現/已實現損益、交割金額（`query_settlement`）、維持率（`maintenance`）——要哪個另開票。
- 不做多帳號 / 帳號選擇（`accounts.data[0]` 固定取第一個，多於一個時 log warning）。
- **不做快取**。富邦 Web API 有 300/min 速率限制，但前端是使用者手動開頁面看餘額，量級差三個數量級。前端真的改成輪詢再加，屆時一行 TTL cache 即可。
- 不做 `BrokerClient` 抽象層、不做券商 provider factory。
- 不動 shioaji 報價路徑；`make check-shioaji-isolation` 只 grep `shioaji`，本票不受影響。

## 驗收條件

- [ ] `GET /account/balance` 回 200 + `currency` / `balance` / `availableBalance` / `queriedAt`，金額為整數型別。
- [ ] 未登入 401；登入使用者可查。
- [ ] `FUBON_ENABLED=false` → 503，且**不嘗試**載入 SDK / 讀憑證。
- [ ] `is_success = false` 或 SDK 丟例外 → 502，前端拿到通用訊息，券商 `message` 只出現在 server log。
- [ ] log / response 皆不含帳號、密碼、憑證路徑。
- [ ] SDK 只在第一次查詢時登入，第二次請求重用同一 session（測試以 fake sdk 計算 `login` 呼叫次數驗證）。
- [ ] 端點為同步 `def`，不阻塞 event loop。
- [ ] `make check` 全綠。

## 測試要求

- `tests/api/test_account_balance.py`：以 monkeypatch 注入 fake SDK（**不連真富邦 API**，CI 無憑證）。
  - 成功查詢回 200 與正確 JSON。
  - 文件那種字串金額（`"666666"`）也要能正確轉成 int。
  - `is_success = false` → 502 且回應不含券商 message。
  - `FUBON_ENABLED=false` → 503。
  - 連續兩次請求只 `login` 一次。
- 真實憑證的連線驗證屬手動 smoke test，寫在 PR 描述，不進 CI。

## 工程注意事項

- 命名遵循 snake_case（模組 / 函式 / 變數），response model class 用 PascalCase，對外 JSON 用 camelCase（`ConfigDict(alias_generator=to_camel)`，比照既有 schema）。
- `fubon_neo` 無型別標註 → `pyproject.toml` 補 `[[tool.mypy.overrides]] module = "fubon_neo.*" ignore_missing_imports = true`（比照既有 shioaji override）。
- 目前 wheel 是 macOS arm64 專用（`fubon_neo-2.2.8-cp37-abi3-macosx_11_0_arm64.whl`），**Linux 部署要另外拿對應 wheel**，否則 `uv sync` 在 EC2 會失敗——施工時先確認部署環境裝得起來，這是本票最可能卡住的地方。
- 不要在 `import` 時就登入 SDK（會讓 app 啟動綁死券商可用性、也讓測試變慢）。
