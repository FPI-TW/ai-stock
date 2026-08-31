# F3：富邦持倉查詢（手上有哪些股票、幾股、可賣幾股）

## Metadata

- 分層：上線後（富邦串接系列）
- 優先序：F3
- ROM：**S**
- 依賴：[F1](F1-fubon-account-balance.md)（登入 session 與「取哪個帳號」的邏輯都在 F1，本票直接沿用，不另開登入）。
  與 [F2](F2-fubon-order-query.md) 無相互依賴，可平行做。
- 被誰依賴：停損停利建單流程（[P1](P1-take-profit-stop-loss-oco.md)）——使用者要對持有部位設停損，
  前提是知道自己有哪些股票、可賣幾股。
- 交付版本：V1
- 來源：2026-08-03 富邦方向（`PRODUCT_CONTEXT.md` 第 4 點：「持倉每次讀券商即時值」）

## 背景

前端目前完全看不到帳戶持有哪些股票。價值有兩層：

1. **產品功能面（主要）**：停損停利已定案「一律自己洗價、兩價必填」。使用者要對持有部位設停損，
   現在只能**手打股票代號與數量**，打錯系統也不知道。持倉查詢讓建單可以從「我手上有什麼」出發，
   數量上限也有依據（`tradable_qty`）。
2. **demo 面**：一個盯盤工具秀不出使用者手上有什麼股票，說服力差很多。

**持倉查詢是唯讀，與下單功能沒有依賴關係**——它是「讀」，下單是「寫」。階段一（只通知不下單）就能做完，
帳戶裡的庫存來自測試環境預先配發，已實打確認**有資料**。

## 富邦 API 事實（2026-08-31 以測試帳號實打確認）

`sdk.accounting.inventories(account)` 回 `Result{ is_success, message, data: List[Inventory] }`。

`Inventory` 欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `date` | str | 資料日期，格式 `"2026/08/31"`（斜線，非 ISO） |
| `account` / `branch_no` | str | 帳號 / 分行——**不回前端**（個資，理由同 F1） |
| `stock_no` | str | 股票代號。可能是 `"0050"`、`"00720B"` 這種帶英文字母的 ETF 代號 |
| `order_type` | enum | **`Stock`（現股）/ `Margin`（融資）/ `Short`（融券）** |
| `lastday_qty` | int | 昨日餘額（股） |
| `today_qty` | int | 今日餘額（股），**可為負數** |
| `tradable_qty` | int | 可交易股數 |
| `buy_qty` / `buy_filled_qty` / `buy_value` | int | 今日買進委託股數 / 成交股數 / 成交價金 |
| `sell_qty` / `sell_filled_qty` / `sell_value` | int | 今日賣出委託股數 / 成交股數 / 成交價金 |
| `odd` | `InventoryOdd` | **零股**的同構欄位（同上 9 個 qty/value 欄位，無 `stock_no` / `order_type`） |

⚠️ **`Inventory` 沒有成本價、沒有市值、沒有損益欄位。** 這決定了本票的邊界（見〈非目標〉）。

### 三個實打踩到的坑（最容易寫錯的地方）

**① 同一檔股票會有多筆，唯一鍵是 `(stock_no, order_type)`**

實打結果裡 `2330` 出現**三次**（`Stock` / `Margin` / `Short`），`2002` 出現兩次。
**絕對不可以用 `stock_no` 當 dict key 去 join 或去重**，會直接蓋掉資料。

**② `today_qty` 可為負數**

實打的 `2412` 是 `today_qty: -1000, sell_qty: 1000, sell_filled_qty: 1000, sell_value: 135500`
（今日先賣、尚未回補）。**負數要原樣保留**，不可以當成錯誤、不可以 `abs()`、不可以濾掉。

**③ 大量「全零」的空殼列**

實打 9 筆裡有 3 筆所有 qty 都是 0（該檔今天沒有任何部位或動作）。這是富邦的資料形狀，不是錯誤。

### 「我有幾股」到底看哪個欄位

三個數字意義不同，實作與前端顯示都會用到，別混：

| 用途 | 欄位 |
|---|---|
| 顯示「目前持有」 | `today_qty` |
| **設停損時的數量上限** | `tradable_qty`（能賣的才能停損；融資融券與今日已委託會讓它小於 `today_qty`） |
| 對照昨日 | `lastday_qty` |

## 結論（要做什麼）

新增 `GET /account/positions`，把富邦的庫存**原樣透傳**給前端，只做兩件加工：
`order_type` 翻成語意字串、濾掉全零的空殼列。

### 核心要求：什麼都不要自己算

這張票最大的風險不是寫不出來，是**寫太多**。以下全部明文禁止：

| 禁止 | 原因 |
|---|---|
| 自建持倉表 / 任何本地庫存快照 | 使用者隨時會開富邦 App 自己買賣，本地快照立刻失真，接著就得寫對帳邏輯＝把券商帳本重建一遍 |
| 自行計算成本均價 | 富邦的 `Inventory` 根本沒給成本價，用今日 `buy_value / buy_filled_qty` 湊出來的**不是**持倉成本（不含昨日部位、不含費用），會是錯的數字 |
| 估算手續費、交易稅 | 同上，算錯會誤導使用者的損益判斷 |
| 用報價 × 股數算市值或損益 | 沒有成本價，算不出損益；市值讓前端顯示層自己算，後端不介入 |
| 把整股與 `odd` 零股加總 | 兩者單位語意不同，合併會出錯。`odd` 原樣帶出，前端要怎麼呈現自己決定 |
| 把持倉歸戶到 user | 子帳號系統已於 2026-08-28 整套取消，見〈非目標〉 |

**券商是唯一真相來源**，後端的職責只有：登入 → 查 → 翻 `order_type` → 濾空殼 → 轉 JSON 欄位命名 → 回傳。

### `order_type` 映射（比照 F2 的狀態碼原則）

| 語意值 | 富邦值 | 白話 |
|---|---|---|
| `stock` | `Stock` | 現股 |
| `margin` | `Margin` | 融資 |
| `short` | `Short` | 融券 |

- 映射寫成一個明確的 dict，**不做動態推導**。
- **表上沒有的值** → 回 `unknown`，把原始值放進 `rawOrderType` 一併回傳，並寫 warning log。
  **不可以默默當成現股**——融資融券的部位性質完全不同，誤判會讓使用者對著融券部位設錯方向的停損。
- `rawOrderType` 一律回傳原始值：出事時才查得出來語意是從哪個值翻的。

### 空殼列的濾除規則

**整股與零股的 9 個 qty/value 欄位全部為 0 的筆，後端濾掉、不回前端。**

一筆全零代表「這檔今天沒有部位也沒有動作」，顯示給使用者只是雜訊（實打 9 筆有 3 筆是這種）。
濾除條件寫成一個明確的判斷式，**不要**做成可設定的過濾器或策略。

⚠️ 濾除的是「全零」，**不是「`today_qty <= 0`」**——`2412` 那種 `today_qty: -1000` 的負數部位
是真實部位，必須保留。

### 對外介面

`GET /account/positions`，掛 `ActiveUserDep`。Response 200：

```json
{
  "data": [
    {
      "stockNo": "2330",
      "orderType": "margin",
      "rawOrderType": "Margin",
      "lastdayQty": 500000,
      "todayQty": 500000,
      "tradableQty": 500000,
      "buyQty": 0,
      "buyFilledQty": 0,
      "buyValue": 0,
      "sellQty": 0,
      "sellFilledQty": 0,
      "sellValue": 0,
      "odd": {
        "lastdayQty": 0,
        "todayQty": 0,
        "tradableQty": 0,
        "buyQty": 0,
        "buyFilledQty": 0,
        "buyValue": 0,
        "sellQty": 0,
        "sellFilledQty": 0,
        "sellValue": 0
      }
    }
  ],
  "date": "2026/08/31",
  "queriedAt": "2026-08-31T01:23:45Z"
}
```

- **不回傳 `branch_no` / `account`**（理由同 F1：一套部署一個帳號，帳號屬個資）。
- `date` 是富邦給的資料日期，**原樣透傳斜線格式，不要幫它轉 ISO**（同 F2 `last_time` 的教訓：
  後端自作聰明改格式，跨日或格式變動時會出事）。每筆的 `date` 都相同，提到頂層即可；
  若富邦回傳的各筆 `date` 不一致，以第一筆為準並寫 warning log。
- 無持倉（或全部被濾掉）時回**空陣列 + 200**，不是 404。
- 排序：照富邦回傳順序，不另外排。

### 錯誤處理（比照 F1 / F2，不要另創一套）

| 情境 | 狀態碼 |
|---|---|
| 未設定富邦憑證（`FUBON_ENABLED=false`） | 503 |
| 登入失敗 / `is_success = false` / SDK 例外 | 502 |

`Result.message`（連線 / 認證層失敗）**不回前端**，只寫 server log。

### 程式落點

- `src/app/services/fubon/position_query.py`（新增）：`inventories` 查詢 + `order_type` 映射 + 濾空殼 + 轉 dataclass。
- `src/app/schemas/account.py`（F1 建立）：加持倉的 response model。
- `src/app/api/routes/account.py`（F1 建立）：加這支端點。
- **沿用 F1 的登入 session 與取帳號邏輯**，不要另開一套，也不要各自寫 `accounts.data[0]`。
- 端點同樣是同步 `def`（SDK 阻塞，`async def` 會卡 event loop）。

## 非目標

- **不做未實現損益（`unrealized_gains_and_loses`）**。2026-08-31 實打測試帳號直接失敗：

  ```
  Result { is_success: False, message: 證券報表/表單定義資料不存在, data: None }
  ```

  測試環境沒有這張報表 → 階段一**無法驗證也無法 demo**，做了就是寫一段自己測不到的程式。
  正式環境是否有，等真的接上正式帳號再實打確認、屆時另開票。

  ⚠️ 連帶結論：**本票交付的是「我有哪些股票、幾股、可賣幾股」，不是「賺賠多少」。**
  排期與 demo 預期請照這個範圍設定。

- **不做已實現損益（`realized_gains_and_loses`）**：它沒有委託單號、對不回任何東西，
  階段一單純是一張看不懂來源的數字表。
- 不做交割金額（`query_settlement`）、維持率（`maintenance`）——要哪個另開票。
- 不下單、不改單、不刪單。
- **不把持倉寫進我們的資料庫**（見上表，這是產品紅線不是本票偷懶）。
- 不做快取（理由同 F1：手動查看，不是輪詢）。
- 不做期貨（`sdk.futopt.*`），只做證券。
- 不做多帳號選擇。
- **不做 per-trader 持倉／績效歸戶**：子帳號系統已於 2026-08-28 整套取消。
  一個帳號底下只有一個庫存池、股票可替代，「誰的股票被賣掉」這件事不存在；
  富邦的持倉全是帳號層級合計，券商端永遠無法佐證我方歸戶。要分人就一人一戶（＝多套部署）。

## 驗收條件

- [ ] `GET /account/positions` 回 200 + 持倉陣列（camelCase），欄位如上。
- [ ] **同一 `stockNo` 的多筆（現股／融資／融券）全部保留**，沒有被合併或去重。
- [ ] `order_type` 正確翻成語意值：`Stock`→`stock`、`Margin`→`margin`、`Short`→`short`。
- [ ] 未知 `order_type` → `unknown` + `rawOrderType` 原值 + warning log，不被誤判成現股。
- [ ] 每筆都帶 `rawOrderType` 原始值。
- [ ] 整股與零股欄位全零的筆被濾掉；**`todayQty` 為負數的筆保留**。
- [ ] `odd` 零股欄位原樣帶出，未與整股加總。
- [ ] `date` 原樣是富邦的 `"YYYY/MM/DD"` 字串，後端未轉格式。
- [ ] 帳戶無持倉（或全被濾掉）→ 200 + 空陣列。
- [ ] 回應與 log 皆不含 `account` / `branch_no`。
- [ ] `is_success = false` 或 SDK 例外 → 502，券商 `message` 只在 log。
- [ ] 未登入 401；`FUBON_ENABLED=false` → 503。
- [ ] 後端沒有任何成本均價、市值、手續費、稅、報酬率的計算；沒有新增任何持倉相關資料表。
- [ ] 沿用 F1 的登入 session，未新增第二套登入流程；取帳號邏輯只有一處。
- [ ] `make check` 全綠。

## 測試要求

`tests/api/test_account_positions.py`，以 monkeypatch 注入 fake SDK（**不連真富邦 API**）。
fake 資料直接照 2026-08-31 的實打結果建（帳號與分行代號改成假值）：

- 正常持倉回 200 與正確 JSON。
- **同一檔三筆（`Stock` / `Margin` / `Short`）全部出現在回應中**——這是本票最容易寫錯的地方。
- 三種 `order_type` 各一個 case 驗證語意映射。
- 未知 `order_type`（例如 `"Foo"`）→ `unknown` + `rawOrderType: "Foo"`。
- 全零的筆被濾掉。
- `todayQty: -1000` 的筆**沒有**被濾掉。
- `odd` 有值時原樣回傳，未與整股相加。
- 空清單 → 200 + `[]`；全部都是全零 → 200 + `[]`。
- `is_success = false` → 502 且回應不含券商 message。
- `FUBON_ENABLED=false` → 503。
- 回應 JSON 不含 `account` / `branchNo`。

真實帳號的連線驗證屬手動 smoke test，寫在 PR 描述。

## 工程注意事項

- `order_type` 映射是一個 dict + 一個「查不到就 unknown」的分支，**不要**做成 enum 階層、策略類別或設定檔驅動。
- `order_type` 從 SDK 拿到的是 Rust 擴充的列舉物件，不是純字串。取原值時用 `str(...)` 轉一次再查表，
  並在測試裡確認 fake 與真實 SDK 的行為一致（實打的 repr 顯示為 `Stock` / `Margin` / `Short`）。
- 股數單位是**股**（實打的 `500000` ＝ 500 張）。後端不換算張數，前端要顯示張數自己除。
- 金額與股數的型別以富邦實際回傳為準，比照 F1 的教訓（文件標 int、範例給字串）：
  施工時實打確認，該轉就轉一次再放進 response model，不要直接信任文件型別。
- `stock_no` 可能含英文字母（`"00720B"`），不要用 int 解析或做數字驗證。
- 命名遵循 snake_case（模組 / 函式 / 變數），response model class 用 PascalCase，對外 JSON camelCase。
