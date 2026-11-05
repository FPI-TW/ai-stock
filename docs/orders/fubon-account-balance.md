# 富邦帳戶現金餘額查詢

> 狀態：尚未實作。本文件描述待開發需求，不是目前 API 或 schema。
> 前半為白話說明，後半〈工單細節（補充）〉為完整設計與驗收條件。

## 一句話

**這張票 = 拿使用者本人的富邦連線問一句「帳戶剩多少錢」，把答案包成 JSON 回給前端。**

---

## 現況與痛點

現在整套系統只會做兩件事：盯報價、發通知。使用者在前端完全看不到自己的券商帳戶狀況——連「我還有多少錢可以買」都不知道。

[per-user 券商 session 工單](per-user-broker-sessions.md) 做完之後，每個綁定過的使用者在伺服器上都有一條自己的富邦連線，行情、帳務、下單都掛在這條連線上。富邦剛好有一支現成的餘額查詢，後端只要「拿他的連線 → 問一句 → 轉成 JSON」，沒有任何演算法或狀態要維護。

---

## 這張票做的事（白話）

開一支唯讀的網址 `GET /account/balance`，綁定過券商帳號的使用者打它，就會拿到：

| 回傳欄位 | 白話 |
|---|---|
| `currency` | 幣別（TWD） |
| `balance` | 帳戶餘額 |
| `availableBalance` | 可用餘額（能拿去下單的部分） |
| `queriedAt` | 這筆數字是幾點幾分跟券商要的 |

**不回傳券商帳號本身。** 使用者要看自己綁的是哪個帳號，`GET /me/broker-account` 已經回遮罩後四碼；餘額這支不重複回，少回一欄就少一個外洩面。

---

## 三個要注意的地方（施工的人請看）

1. **富邦文件自己前後不一致**：金額欄位型別標「整數」，但範例卻印成字串 `"666666"`。所以程式一律強制轉一次整數，不要直接相信它給什麼。
2. **這張票不負責登入、不碰連線。** 登入、重連、解綁全部由 [per-user 券商 session 工單](per-user-broker-sessions.md) 的 `BrokerSessionPool` 管；本票只是跟 pool 要「這個使用者的連線」，拿到就問餘額。沒綁定的人 pool 會直接拒絕，本票不用自己判斷。
3. **這支 API 是「會卡住」的類型**：程式要寫成同步的寫法，寫成非同步反而會把整個伺服器卡住。

---

## 出事的時候怎麼辦

| 情況 | 使用者看到 |
|---|---|
| 這套部署不是富邦模式（CI、永豐 demo） | 「券商帳務服務未啟用」 |
| 使用者還沒綁定券商帳號，或綁了但連線目前不在（登入失敗、重連中） | 「尚未綁定券商帳號」，到券商帳號頁看狀態 |
| 券商回錯誤、連線爆掉 | 「查詢券商餘額失敗」 |

**券商回的原始錯誤訊息只寫進伺服器 log，不給前端看**——那種訊息常常會洩漏券商內部細節。身分證字號、密碼、憑證則是連 log 都不准出現。

---

## 這張票的邊界

| 不做 | 原因 |
|---|---|
| 下單 | 另一系列的票，這張只是唯讀查詢 |
| 查庫存 / 損益 / 交割金額 | 富邦都有現成 API，但要哪個就另開一張票，不要順手全做 |
| 綁定、解綁、重連券商帳號 | per-user 券商 session 工單負責，本票只取用 |
| 做快取 | 使用者是手動開頁面看一次，不是一直輪詢。真的改輪詢再加，那時一行就夠 |
| 先蓋一層「券商介面」好以後換券商 | 現在只有富邦一家、一個實作。第二家出現再抽 |

---

## 做完之後

前端可以顯示「帳戶餘額 / 可用餘額」，數字來自使用者本人的富邦帳戶。

---

## 工單細節（補充）

### Metadata

- 分層：富邦串接系列第一張帳務票；不阻擋既有通知功能
- ROM：**S**
- 依賴：[per-user 券商 session 工單](per-user-broker-sessions.md) 的 PR1（`FubonClient`）與 PR2（`BrokerSessionPool`、綁定 API）合併後才能施工；不需等 PR3 行情接線。
- 被誰依賴：無。後續委託／持倉／交割查詢票比照本票的取 session 方式。
- 來源：2026-08-03 富邦方向；2026-09-16 定案 per-user 綁定後（`docs/product.md`〈下單能力演進〉）本票改為用本人 session，2026-09-18 依此改寫。

### 富邦 API 事實

出處：`docs/vendor/fubon/fubon-llms-full.txt`、`docs/vendor/fubon/fubon-neo-verified-behavior.md`。

- `sdk.accounting.bank_remain(stock_account)` 回 `Result`：`is_success: bool`、`message: str | None`、`data: BankRemain`。
- `BankRemain` 欄位：`branch_no`(str)、`account`(str)、`currency`(str)、`balance`(int)、`available_balance`(int)。
- ⚠️ 文件回傳範例把 `balance` / `available_balance` 寫成字串（`"666666"`）但型別標 int，**兩者不一致**。施工時一律 `int(...)` 轉一次再放進 response model。
- ⚠️ 證券帳號必須以 `account_type == "stock"` 過濾，不可 `data[0]`；這件事 `FubonClient.login()` 已做，本票直接用 client 持有的證券帳號。
- ⚠️ 測試環境的 `bank_remain` 回「交割銀行不在支援範圍」（verified-behavior 第 6 節），兩組測試帳號都一樣。本票的真連線 smoke test 要等正式帳號，CI 一律 fake。
- SDK 為同步阻塞，端點用 `def`。

### 對外介面

`GET /account/balance`，掛 `ActiveUserDep`。

Response 200（JSON 欄位維持既有 camelCase 慣例）：

```json
{ "data": { "currency": "TWD", "balance": 666666, "availableBalance": 123456, "queriedAt": "2026-08-24T01:23:45Z" } }
```

- 不回傳 `branch_no` / `account`；遮罩後四碼已由 `GET /me/broker-account` 提供。
- `queriedAt`：這次向券商取值的 UTC 時間（後端不快取，等同即時）。

錯誤碼：

| 情境 | 狀態碼 | 回應 |
|---|---|---|
| `QUOTE_PROVIDER` 非 `fubon`（shared 模式，無本人 session） | 503 | `BROKER_ACCOUNTING_DISABLED`，通用訊息「券商帳務服務未啟用」 |
| `pool.require(user_id)` 拿不到 session（未綁定、`login_failed`、重連中） | 409 | `BROKER_ACCOUNT_NOT_BOUND`（沿用 per-user 工單既有錯誤碼，不新增） |
| `is_success = false` / SDK 例外 | 502 | `BROKER_QUERY_FAILED`，通用訊息「查詢券商餘額失敗」 |

⚠️ 券商回傳的 `message` 與 SDK 例外原文只寫 server log，不回前端；log 不得出現身分證字號、密碼、憑證。

### 程式落點

- `src/app/services/quote/fubon/client.py`：`FubonClient` 新增 `bank_remain() -> FubonBankRemain`（frozen dataclass：`currency`、`balance: int`、`available_balance: int`）。`client.py` 仍是唯一 `import fubon_neo` 的模組，`is_success = false` 或 SDK 例外對映為不帶原文的專案例外。
- `src/app/api/routes/account.py`（新增）：`GET /account/balance`，`def` 端點；`pool.require(current_user.id)` 取本人 `FubonQuoteProvider` → `.client.bank_remain()` → response model。pool 由 `deps.py` 提供（PR2 已掛 `app.state.broker_sessions`）。
- `src/app/api/schemas/account.py`（新增）：response model，`ConfigDict(alias_generator=to_camel)`。
- 不新增環境變數：本人 session 存在與否由 `QUOTE_PROVIDER=fubon` 與綁定狀態決定，沒有 `FUBON_*` 帳密變數。
- 不建 `BrokerClient` interface / ABC / factory。

### 非目標

- 不下單、不查委託、不查成交。
- 不做庫存、損益、交割金額、維持率——要哪個另開票。
- 不做綁定、解綁、重連；`pool.require` 拿不到 session 時直接 409，本票不嘗試登入或重試。
- 不做快取。
- 不動 shioaji 報價路徑；`make check-shioaji-isolation` 只 grep `shioaji`，本票不受影響。

### 驗收條件

- [ ] `GET /account/balance` 回 200 + `currency` / `balance` / `availableBalance` / `queriedAt`，金額為整數型別。
- [ ] 未登入 401。
- [ ] shared 模式（`QUOTE_PROVIDER=in_memory`）→ 503 `BROKER_ACCOUNTING_DISABLED`，且不載入 `app.services.quote.fubon*`。
- [ ] 未綁定或 session 不在 → 409 `BROKER_ACCOUNT_NOT_BOUND`。
- [ ] A、B 兩人各有 session 時，A 查到的是 A 的 client 回的餘額，不是 B 的。
- [ ] `is_success = false` 或 SDK 例外 → 502，前端拿到通用訊息，券商 `message` 只出現在 server log。
- [ ] 本票程式碼中沒有任何 `login()` 呼叫。
- [ ] 端點為同步 `def`。
- [ ] `make check` 全綠。

### 測試要求

- `tests/api/test_account_balance.py`：以 fake `FubonClient` 注入 pool（**不連真富邦 API**，CI 無憑證）。
  - 成功查詢回 200 與正確 JSON。
  - 文件那種字串金額（`"666666"`）也要能正確轉成 int。
  - `is_success = false` → 502 且回應不含券商 message。
  - shared 模式 → 503；未綁定 → 409。
  - 兩個使用者各自的 fake client 回不同餘額，驗證各查各的。
- 真實憑證的連線驗證屬手動 smoke test，需正式帳號（測試環境 `bank_remain` 不支援），寫在 PR 描述，不進 CI。

### 工程注意事項

- 命名遵循 snake_case（模組 / 函式 / 變數），response model class 用 PascalCase，對外 JSON 用 camelCase。
- `fubon_neo` 的 mypy override 由 per-user PR1 補，本票不重複。
- 不要在 `import` 時就碰 SDK；本票根本不登入，只取用 pool 裡已存在的 session。
