# F4：富邦交割款查詢（近三個交易日應收付多少錢、哪天交割）

## Metadata

- 分層：上線後（富邦串接系列）
- 優先序：F4
- ROM：**S**（比 F3 更小：無狀態碼映射、無濾除規則，只有登入 → 查 → 轉 JSON）
- 依賴：[F1](F1-fubon-account-balance.md)（登入 session 與「取哪個帳號」的邏輯都在 F1，本票直接沿用，不另開登入）。
  與 [F2](F2-fubon-order-query.md)、[F3](F3-fubon-position-query.md) 無相互依賴，可平行做。
- 被誰依賴：無硬依賴。階段二／三開始下單後價值放大（賣出後何時入帳、買進後 T+2 要準備多少錢）。
- 交付版本：V1
- 來源：2026-08-03 富邦方向（`PRODUCT_CONTEXT.md` 第 4 點）；F3 工單〈非目標〉列的「交割金額要哪個另開票」

## 背景

前端看得到持有什麼（F3）、看得到今日委託（F2），但看不到**錢**：賣掉的股票哪天入帳、
買進的部位 T+2 要準備多少現金。這件事在階段二／三（觸發後真的送委託）之前就有價值——
使用者自己在富邦 App 下的單也會產生交割款，盯盤工具秀得出「9/2 會進帳 134,901」很直觀。

**這支是唯讀，與下單無依賴關係。** 階段一（只通知不下單）就能做完，
測試帳號實打確認**有資料**（2026-08-31 有一筆賣出交割）。

## 富邦 API 事實（2026-09-01 以測試帳號實打確認）

`sdk.accounting.query_settlement(account, range)` 回
`Result{ is_success, message, data: SettlementData }`。

`SettlementData`：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `account` | `AccountRes{ branch_no, account }` | 帳號 / 分行——**不回前端**（個資，理由同 F1） |
| `details` | list[`Settlement`] | 每個交易日一筆 |

`Settlement` 欄位（**除 `date` 外全部 Optional**）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `date` | str | 查詢日（交易日），格式 `"2026/08/31"`（斜線，非 ISO） |
| `settlement_date` | str? | 交割日，同斜線格式。實打是 T+2（08/31 → 09/02） |
| `buy_value` / `buy_fee` / `buy_tax` / `buy_settlement` | int? | 買進金額 / 手續費 / 交易稅 / **應收付款** |
| `sell_value` / `sell_fee` / `sell_tax` / `sell_settlement` | int? | 賣出金額 / 手續費 / 交易稅 / **應收付款** |
| `total_bs_value` / `total_fee` / `total_tax` | int? | 合計買賣金額 / 手續費 / 交易稅 |
| `total_settlement_amount` | int? | **合計交割金額**（正＝應收入帳，負＝應付） |
| `currency` | str? | 幣別，實打為 `"TWD"` |

### `range` 參數：只有兩個有效值，沒有自訂區間

官方文件明載「目前有效值為 `"0d"`(當日)、`"3d"`」。**沒有 `start_date` / `end_date`**——
這支跟「查詢歷史成交」不同，我方**做不出自訂日期區間**，別去設計那個 UI。

### 三個實打踩到的坑（最容易寫錯的地方）

**① 無交易的日子整列是 `None`，不是 0**

實打回三筆，其中兩筆（08/28、09/01）除了 `date` 之外**每一個欄位都是 `None`**：

```
Settlement { date: "2026/08/28", settlement_date: None, buy_value: None, ... currency: None }
Settlement { date: "2026/08/31", settlement_date: "2026/09/02", buy_value: 0, ... sell_value: 135500, ... }
Settlement { date: "2026/09/01", settlement_date: None, buy_value: None, ... currency: None }
```

⚠️ 同一份實打資料裡**同時出現 `buy_value: 0` 與 `buy_value: None`**，兩者語意完全不同：

- `0`（08/31）＝那天有交割資料，買進金額確實是 0（當天只賣沒買）
- `None`（08/28、09/01）＝**那天沒有交割資料**

**禁止 `or 0`、禁止 `= 0` 當預設值、禁止把 `None` 轉成 0 再回前端。**
一轉，「那天沒買」跟「那天沒資料」就永遠分不出來了，回應 model 的欄位一律 `int | None`。

**② `"3d"` 是三個交易日，不是三個日曆日**

實打 09/01（週二）打 `"3d"` 回的是 **08/28、08/31、09/01**——中間跳過 08/29、08/30 週末。
**前端不可以假設回傳是連續三天日期**，也不要拿今天往回推兩天去對。日期一律用富邦給的 `date`。

**③ 交割金額有正負號，正負就是語意**

`total_settlement_amount` 正數＝**應收**（錢會進來），負數＝**應付**（要準備錢匯進交割戶）。
實打 08/31 是 `134901`（賣出 135500 扣手續費 193、交易稅 406）＝ 9/2 入帳。

**禁止 `abs()`、禁止只顯示絕對值。** 把應付顯示成應收，使用者會以為有錢進帳而不去補款，
違約交割的後果是券商層級的，不是畫面小 bug。

## 結論（要做什麼）

新增 `GET /account/settlements`，固定用 `"3d"` 打富邦，把 `details` **原樣透傳**給前端。
`account` / `branch_no` 不回傳。**沒有任何加工。**

### 核心要求：什麼都不要自己算

這張票唯一的風險是**寫太多**。以下全部明文禁止：

| 禁止 | 原因 |
|---|---|
| 自行加總 / 驗算（例如檢查 `total_fee == buy_fee + sell_fee`） | 券商的帳本是唯一真相。驗算對不上時你也不敢用自己的數字，那就別算 |
| 用欄位互推缺漏值 | 官方文件的回傳範例**自己就對不起來**（`buy_value: 735500` 配 `buy_settlement: -1429513`），互推出來的是錯的 |
| 把 `None` 補成 0 | 見坑 ①，會抹掉「無資料」語意 |
| `abs()` 或自行判斷應收應付改寫數字 | 見坑 ③ |
| 幣別換算、多幣別加總 | `currency` 原樣帶出，前端要怎麼呈現自己決定 |
| 濾掉「整列 None」的空殼列 | **與 F3 相反，這裡不濾**（理由見下） |
| 把交割資料寫進我們的資料庫 / 做對帳 | 同 F3：使用者隨時會自己在富邦 App 買賣，本地快照立刻失真 |
| 日期格式轉 ISO | 同 F2 `last_time`、F3 `date` 的教訓，原樣透傳 |

**券商是唯一真相來源**，後端職責只有：登入 → 查 → 去掉帳號欄位 → 轉 JSON 欄位命名 → 回傳。

### 為什麼不濾空殼列（F3 濾、這裡不濾）

F3 濾掉全零庫存，是因為那 9 筆裡有 3 筆純粹是雜訊、**沒有任何識別意義**。
這裡不同：**每一列就是一個日期**。濾掉 09/01 那列，前端就無法區分
「今天沒有交割款」和「今天這一天不存在」——而「今天目前沒有應收付」本身就是使用者要看的答案。

三筆而已，不是雜訊。原樣三筆回傳，`null` 保留為 `null`。

### 對外介面

`GET /account/settlements`，掛 `ActiveUserDep`。Response 200：

```json
{
  "data": [
    {
      "date": "2026/08/28",
      "settlementDate": null,
      "buyValue": null,
      "buyFee": null,
      "buyTax": null,
      "buySettlement": null,
      "sellValue": null,
      "sellFee": null,
      "sellTax": null,
      "sellSettlement": null,
      "totalBsValue": null,
      "totalFee": null,
      "totalTax": null,
      "totalSettlementAmount": null,
      "currency": null
    },
    {
      "date": "2026/08/31",
      "settlementDate": "2026/09/02",
      "buyValue": 0,
      "buyFee": 0,
      "buyTax": 0,
      "buySettlement": 0,
      "sellValue": 135500,
      "sellFee": 193,
      "sellTax": 406,
      "sellSettlement": 134901,
      "totalBsValue": 135500,
      "totalFee": 193,
      "totalTax": 406,
      "totalSettlementAmount": 134901,
      "currency": "TWD"
    }
  ],
  "queriedAt": "2026-09-01T01:23:45Z"
}
```

- **不回傳 `branch_no` / `account`**（理由同 F1：一套部署一個帳號，帳號屬個資）。
- 排序：照富邦回傳順序（實打是日期升冪），**不另外排序、不反轉**。
- `details` 為空 → 200 + 空陣列，不是 404。
- 不提供 `range` query param（見〈非目標〉）。

### 錯誤處理（比照 F1 / F2 / F3，不要另創一套）

| 情境 | 狀態碼 |
|---|---|
| 未設定富邦憑證（`FUBON_ENABLED=false`） | 503 |
| 登入失敗 / `is_success = false` / `data` 為 `None` / SDK 例外 | 502 |

`Result.message`**不回前端**，只寫 server log。

### 程式落點

- `src/app/services/fubon/settlement_query.py`（新增）：`query_settlement` 查詢 + 轉 dataclass。
- `src/app/schemas/account.py`（F1 建立）：加交割款的 response model。
- `src/app/api/routes/account.py`（F1 建立）：加這支端點。
- **沿用 F1 的登入 session 與取帳號邏輯**，不要另開一套，也不要各自寫 `accounts.data[0]`
  （登入會回證券 + 期權多筆且順序不保證，見 `docs/api/fubon-neo-verified-behavior.md`）。
- 端點同樣是同步 `def`（SDK 阻塞，`async def` 會卡 event loop）。

## 非目標

- **不做 `"0d"` 參數**。`"3d"` 的回傳已包含當日（實打的第三筆就是 09/01），
  `"0d"` 是它的子集。開一個 query param 就多一組要驗的分支，換不到任何看不到的資料。
  前端只想看當日，自己取 `date` 等於今天的那筆。
- **不做自訂日期區間**：富邦這支只吃 `"0d"` / `"3d"`，做不到。要查更早的交割，
  只能走「查詢歷史成交」自己算——那是另一件事、也違反「什麼都不要自己算」，不做。
- **不做交割款提醒通知**（「9/2 要付 30 萬，前一天提醒我」）：需要排程 + 通知管道，
  屬 P2 outbox 範疇，要就另開票，不要塞進這支唯讀端點。
- 不做銀行餘額（`bank_remain`，屬 F1）、損益（測試環境無報表，見 F3）、維持率（`maintenance`）。
- 不下單、不改單、不刪單。
- **不把交割資料寫進我們的資料庫**（見上表，這是產品紅線不是本票偷懶）。
- 不做快取（理由同 F1：手動查看，不是輪詢）。
- 不做期貨（`sdk.futopt.*`），只做證券。
- 不做多帳號選擇。

## 驗收條件

- [ ] `GET /account/settlements` 回 200 + 交割清單（camelCase），欄位如上。
- [ ] **`None` 原樣回 `null`，沒有被補成 0**；同一回應中 `buyValue: 0` 與 `buyValue: null` 並存正確。
- [ ] 整列皆 `null` 的日期**沒有被濾掉**（與 F3 的濾除規則不同）。
- [ ] `date` / `settlementDate` 原樣是富邦的 `"YYYY/MM/DD"` 字串，後端未轉 ISO。
- [ ] `totalSettlementAmount` 為負數時**原樣回負數**，沒有 `abs()`、沒有被改寫。
- [ ] 回傳順序與富邦一致，未重新排序。
- [ ] 後端沒有任何加總、驗算、欄位互推、幣別換算。
- [ ] 回應與 log 皆不含 `account` / `branch_no`。
- [ ] `details` 為空 → 200 + `[]`。
- [ ] `is_success = false`、`data` 為 `None` 或 SDK 例外 → 502，券商 `message` 只在 log。
- [ ] 未登入 401；`FUBON_ENABLED=false` → 503。
- [ ] 沒有新增任何交割相關資料表；沿用 F1 的登入 session，未新增第二套登入流程。
- [ ] `make check` 全綠。

## 測試要求

`tests/api/test_account_settlements.py`，以 monkeypatch 注入 fake SDK（**不連真富邦 API**）。
fake 資料直接照 2026-09-01 的實打結果建（帳號與分行代號改成假值）：

- 正常查詢回 200 與正確 JSON（三筆：null 列、有資料列、null 列）。
- **`buyValue: 0` 與 `buyValue: null` 在同一回應中各自正確**——這是本票最容易寫錯的地方。
- 整列 null 的筆**有**出現在回應中。
- `totalSettlementAmount` 為負數（買進應付）的 case 原樣回負數。
- `date` / `settlementDate` 維持斜線格式。
- `details` 空清單 → 200 + `[]`。
- `is_success = false` → 502 且回應不含券商 message。
- `data` 為 `None`（`is_success = true` 但無 data）→ 502，不是 500。
- `FUBON_ENABLED=false` → 503。
- 回應 JSON 不含 `account` / `branchNo`。

真實帳號的連線驗證屬手動 smoke test，寫在 PR 描述。

## 工程注意事項

- Response model 每個數值欄位都是 `int | None`，**不要給 default `0`**，不要 `Field(default=0)`，
  不要在 dataclass 轉換時 `or 0`。ruff / mypy 不會抓這種錯，只有 code review 會。
- 金額型別以富邦實際回傳為準，比照 F1 的教訓（文件標 int、範例給字串）：施工時實打確認，
  該轉就轉一次再放進 response model，不要直接信任文件型別。
- `Settlement` 是 Rust 擴充物件，取值用屬性存取；缺值是 `None` 不是空字串，
  但仍要對「回空字串」留一手（官方 C++ 範例的缺值印出來是空白）——空字串一律視為 `None`。
- 富邦這支在測試環境**打得通、有資料**，與損益三支（報表不存在）不同，可以放心排期。
- 呼叫前照 `fubon-neo-verified-behavior.md` 的通則：新接的 `sdk.*` API 先獨立行程試打一次，
  確認不會像 2.2.8 的 `bank_remain()` 那樣直接 panic 打掛行程。本票的 `query_settlement`
  已於 2026-09-01 實打過，未 panic。
- 命名遵循 snake_case（模組 / 函式 / 變數），response model class 用 PascalCase，對外 JSON camelCase。
