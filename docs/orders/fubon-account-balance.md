# 富邦帳戶現金餘額查詢

> 狀態：尚未實作。本文件描述待開發需求，不是目前 API 或 schema。
> 前半為白話說明，後半〈工單細節（補充）〉為完整設計與驗收條件。

## 一句話

**這張票 = 後端拿富邦帳號登入券商 API，問一句「帳戶剩多少錢」，把答案包成 JSON 回給前端。**

---

## 現況與痛點

現在整套系統只會做兩件事：盯報價、發通知。使用者在前端完全看不到自己的券商帳戶狀況——連「我還有多少錢可以買」都不知道。

我們手上已經有富邦的 API 套件（`fubon-neo`）跟即將到手的帳號，而富邦剛好有一支現成的餘額查詢。後端只要「登入 → 問一句 → 轉成 JSON」，沒有任何演算法或狀態要維護。

---

## 這張票做的事（白話）

開一支唯讀的網址 `GET /account/balance`，登入過的使用者打它，就會拿到：

| 回傳欄位 | 白話 |
|---|---|
| `currency` | 幣別（TWD） |
| `balance` | 帳戶餘額 |
| `availableBalance` | 可用餘額（能拿去下單的部分） |
| `queriedAt` | 這筆數字是幾點幾分跟券商要的 |

**不回傳券商帳號本身。** 一套系統只服務一個券商帳號，前端不需要辨識是哪個帳號；帳號算個資，少回一欄就少一個外洩面。

---

## 三個要注意的地方（施工的人請看）

1. **富邦文件自己前後不一致**：金額欄位型別標「整數」，但範例卻印成字串 `"666666"`。所以程式一律強制轉一次整數，不要直接相信它給什麼。
2. **這張票不負責登入**（2026-09-09 改）：登入富邦這件事由 [F5](fubon-quote-provider.md)（行情）統一做，這張票直接跟它拿現成的連線。

   為什麼不各登各的？因為**富邦的登入連線數上限只有 10 條**，每登入一次就吃掉一條，超過就直接登不進去；而且程式關掉時沒有正常登出的話，那條殘留連線還會繼續佔著。更根本的是，富邦的行情、帳務、下單本來就掛在**同一個登入**底下，不是三個獨立的東西——各登一次是自己製造問題。

   由行情那張票管，是因為行情是「開機連到關機」的長命連線，而查餘額是使用者點開頁面才查一次。**活得最久的那個負責管，其他人搭便車。**
3. **這支 API 是「會卡住」的類型**：程式要寫成同步的寫法，寫成非同步反而會把整個伺服器卡住。

---

## 出事的時候怎麼辦

| 情況 | 使用者看到 |
|---|---|
| 這套部署還沒接券商帳號（例如 CI、測試機） | 「券商帳務服務未啟用」 |
| 登入失敗、券商回錯誤、連線爆掉 | 「查詢券商餘額失敗」 |

**券商回的原始錯誤訊息只寫進伺服器 log，不給前端看**——那種訊息常常會洩漏券商內部細節。帳號、密碼、憑證路徑則是連 log 都不准出現。

---

## 這張票的邊界

| 不做 | 原因 |
|---|---|
| 下單 | 另一系列的票，這張只是唯讀查詢 |
| 查庫存 / 損益 / 交割金額 | 富邦都有現成 API，但要哪個就另開一張票，不要順手全做 |
| 讓每個使用者綁自己的券商帳號 | 一套部署一個帳號，寫在 `.env`；per-user 綁定是已捨棄的多租戶做法 |
| 做快取 | 使用者是手動開頁面看一次，不是一直輪詢。真的改輪詢再加，那時一行就夠 |
| 先蓋一層「券商介面」好以後換券商 | 現在只有富邦一家、一個實作。第二家出現再抽 |

---

## 最可能卡住的地方（先講在前面）

原本 repo 裡的 `fubon_neo` 安裝檔只有 macOS 版，部署到 Linux 主機會裝不起來。這件事已由 `chore/fubon-sdk-install` 補上 Linux wheel（版本 2.2.9）解決，動工前確認 `uv sync` 在部署環境跑得過即可。

---

## 做完之後

前端可以顯示「帳戶餘額 / 可用餘額」。

（原本這裡寫「後端這時就有一套登入富邦的做法，後面的票直接沿用」——那個責任已經在 2026-09-09 移交給 [F5](fubon-quote-provider.md)，這張票改成沿用它。）

---

## 工單細節（補充）

### Metadata

- 分層：上線後（富邦串接第一票；不阻擋既有通知功能）
- 優先序：F1（F = Fubon 券商串接系列，新開前綴；既有 L/P/T 三系列不含券商帳務）
- ROM：**S**
- 依賴：`fubon-neo` 2.2.9 已加入 `pyproject.toml`（本機 wheel，`[tool.uv.sources]`，macOS 與 Linux 皆備）。
  **[F5](fubon-quote-provider.md)（富邦行情 provider）＝共用登入 session 的擁有者**，本票沿用不自建
  （2026-09-09 變更，理由見〈登入 session 處理〉）。無其他功能依賴。
- 交付版本：V1
- 來源：2026-08-03 富邦方向（`docs/product.md`〈下單能力演進〉與〈設計紅線〉）——取得富邦交易帳號後，先做**唯讀帳務**，下單另票。

### 背景

目前系統只有報價與通知，前端沒有任何「這個帳戶還有多少錢」的資訊。富邦 Neo SDK 的
`accounting.bank_remain(account)` 直接回傳交割帳戶餘額，後端只需登入 → 查詢 → 轉成 JSON。

⚠️ **2026-09-09 變更**：本票原本要「順便把登入 session 一次定下來」，該責任已移交
[F5](fubon-quote-provider.md)。本票改為**沿用 F5 的共用登入**，不自建第二套。
詳見〈登入 session 處理〉。

### 結論（要做什麼）

新增一支唯讀端點 `GET /account/balance`，後端以 F5 的共用 session 呼叫 `accounting.bank_remain`，
把餘額打包成 JSON 回前端。

#### 富邦 API 事實（`fubon-api.md` 45393–45450）

```py
## sdk 與證券帳號皆取自 F5 的共用登入，本票不自己 login
result = sdk.accounting.bank_remain(stock_account)
```

⚠️ 官方文件範例寫的是 `accounts.data[0]`，**不可照抄**。一次登入會回證券帳號 + 期權帳號
**多筆且順序不保證**（`docs/api/fubon-neo-verified-behavior.md` 地雷節，已實測）。
拿到期權帳號去打證券 API 會回「帳號類別錯誤」。取帳號的責任在 F5，本票直接用它給的證券帳號。

`Result` 欄位：`is_success: bool` / `message: str | None` / `data: BankRemain`。

`BankRemain`：`branch_no`(str)、`account`(str)、`currency`(str)、`balance`(int)、`available_balance`(int)。

⚠️ 文件的回傳範例把 `balance` / `available_balance` 寫成字串（`"666666"`）但型別標 int，
**兩者不一致**。施工時一律 `int(...)` 轉一次再放進 response model，不要直接信任型別。

#### 對外介面

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
對齊既有的錯誤回應原則）。log 也不得出現帳號、密碼、憑證路徑。

#### 程式落點

- `src/app/services/fubon/account_balance.py`（新增，單一模組）：`bank_remain` 查詢 + 轉成 dataclass。
  **不含登入**——sdk 實例與證券帳號都向 F5 的共用 session 取。
- `src/app/schemas/account.py`（新增）：response model。
- `src/app/api/routes/account.py`（新增）+ 在 router 註冊。
- `src/app/core/config.py`：新增下方環境變數。

**不建 `BrokerClient` interface / ABC / factory**——目前只有一家券商、只有一個實作。
第二家券商出現時再抽（`docs/product.md`〈設計紅線〉明文要求）。

#### 登入 session 處理

> **2026-09-09 改寫。** 原本這節寫的是「module 級延遲初始化 + `threading.Lock`，第一次查詢才登入」，
> 已作廢——本票改為沿用 [F5](fubon-quote-provider.md) 的共用登入。

**本票不得自行 `sdk.login()`。** 整個 process 只有一個 `FubonSDK` 實例、只登入一次，
由 F5 在 lifespan 建立、關閉時 `logout()`；本票的端點向它取 sdk 與證券帳號即可。

為什麼不各登各的（硬限制，不是偏好）：

- **富邦交易連線數上限 10**，每次 `sdk.login()` 吃掉一條，超過回
  `Login Error, 超過本應用程式連線限制==>[10]`。
- 未正常 `logout()` 的殘留 session 會**繼續佔著額度**——症狀是「平常都好好的，
  反覆重啟幾次之後某天突然登不進去」，極難查。
- SDK 的 `marketdata` / `accounting` / `stock` 本來就掛在**同一個 `sdk` 物件**底下，
  不是三個獨立 client。行情與帳務各登一次是自己製造問題。
- 由 F5（行情）擁有的理由：行情是**開機連到關機的長命連線**，帳務是請求時查一次；
  生命週期最長的那個負責管理，其他人附掛。

其餘不變：

- 查詢失敗且疑似 session 失效時，**重登一次並重試一次**就好；再失敗直接 502。
  不要做重試佇列 / 指數退避。重登動作一律走 F5 的共用 session（由它負責重建），
  **本票不自己呼叫 `login()`**。
- `FUBON_ENABLED=false`（或共用 session 未建立）→ 503，不嘗試載入 SDK。
- SDK 是**同步阻塞**：端點用 `def`（FastAPI 自動丟 threadpool），**不要寫 `async def`**，否則會卡住 event loop。

#### 環境變數（`.env`，不入 repo）

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

### 非目標

- **不下單**、不查委託、不查成交（下單是另一系列票）。
- 不做庫存（`inventories`）、未實現/已實現損益、交割金額（`query_settlement`）、維持率（`maintenance`）——要哪個另開票。
- 不做多帳號 / 帳號選擇：固定用 F5 給的那個證券帳號（F5 以 `account_type == "stock"` 過濾，
  **不是** `data[0]`；多於一個證券帳號時取第一個並 log warning）。
- **不做快取**。富邦 Web API 有 300/min 速率限制，但前端是使用者手動開頁面看餘額，量級差三個數量級。前端真的改成輪詢再加，屆時一行 TTL cache 即可。
- 不做 `BrokerClient` 抽象層、不做券商 provider factory。
- 不動 shioaji 報價路徑；`make check-shioaji-isolation` 只 grep `shioaji`，本票不受影響。

### 驗收條件

- [ ] `GET /account/balance` 回 200 + `currency` / `balance` / `availableBalance` / `queriedAt`，金額為整數型別。
- [ ] 未登入 401；登入使用者可查。
- [ ] `FUBON_ENABLED=false` → 503，且**不嘗試**載入 SDK / 讀憑證。
- [ ] `is_success = false` 或 SDK 丟例外 → 502，前端拿到通用訊息，券商 `message` 只出現在 server log。
- [ ] log / response 皆不含帳號、密碼、憑證路徑。
- [ ] **本票程式碼中沒有任何 `sdk.login()` 呼叫**；sdk 與證券帳號皆取自 F5 的共用 session。
- [ ] 連續多次請求，`login` 在整個 process 生命週期**只被呼叫一次**（測試以 fake sdk 計數驗證）。
- [ ] 取到的是**證券**帳號（`account_type == "stock"`），不是 `data[0]`。
- [ ] 端點為同步 `def`，不阻塞 event loop。
- [ ] `make check` 全綠。

### 測試要求

- `tests/api/test_account_balance.py`：以 monkeypatch 注入 fake SDK（**不連真富邦 API**，CI 無憑證）。
  - 成功查詢回 200 與正確 JSON。
  - 文件那種字串金額（`"666666"`）也要能正確轉成 int。
  - `is_success = false` → 502 且回應不含券商 message。
  - `FUBON_ENABLED=false` → 503。
  - 連續兩次請求只 `login` 一次（登入由 F5 的共用 session 負責，本票不自己登）。
  - fake 的登入回傳同時含期權與證券帳號、且**期權排在前面**——驗證取到的是證券帳號而非 `data[0]`。
- 真實憑證的連線驗證屬手動 smoke test，寫在 PR 描述，不進 CI。

### 工程注意事項

- 命名遵循 snake_case（模組 / 函式 / 變數），response model class 用 PascalCase，對外 JSON 用 camelCase（`ConfigDict(alias_generator=to_camel)`，比照既有 schema）。
- `fubon_neo` 無型別標註 → `pyproject.toml` 補 `[[tool.mypy.overrides]] module = "fubon_neo.*" ignore_missing_imports = true`（比照既有 shioaji override）。
- SDK wheel 已備 macOS arm64 與 Linux x86_64 兩版（`vendor/fubon_neo-2.2.9-*.whl`，由 `chore/fubon-sdk-install` 引入）；施工時仍先確認部署環境 `uv sync` 跑得過。
- 不要在 `import` 時就登入 SDK（會讓 app 啟動綁死券商可用性、也讓測試變慢）。
  ——這條依然成立且與 F5 不衝突：F5 的登入是在 **lifespan** 做的，不是 import 時，
  而本票根本不登入。
