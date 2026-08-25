# 富邦 fubon_neo 2.2.9：實測行為筆記

> 這份是**實跑出來的事實**，不是官方文件的摘要。官方文件見同目錄 `fubon-neo-api.md`；
> 兩邊衝突時**以本文為準**，因為官方文件已被驗出有省略與範例不一致。
>
> - 實測日期：2026-08-25
> - 版本：**fubon_neo 2.2.9**（與本專案 `vendor/` 一致）。
>   下方標「2.2.8」之處為升版前的舊行為，保留供對照。
> - 環境：`wss://neoapitest.fbs.com.tw/TASP/XCPXWS`（測試環境）
> - 帳號：測試帳號 41610792（證券 20203-7900280）、58581758（證券 20203-7900015 + 期權 15000-9623985）
> - 重跑腳本：`fubon-test` 專案的 `smoke_test_accounting.py`（連線位址／帳號走環境變數）

## API 中文對照（查官方文件時用中文標題搜比較快）

| SDK 方法 | 官方中文標題 | 日期參數 | 查詢範圍 |
| --- | --- | --- | --- |
| `stock.filled_history` | 查詢歷史成交 | ✅ `start_date` + `end_date`（optional） | 歷史，單次最多 30 天 |
| `stock.order_history` | 查詢歷史委託 | ✅ `start_date` + `end_date`（optional） | 歷史，單次最多 30 天 |
| `stock.get_order_results` | 取得委託單結果 | ❌ | **僅當日** |
| `stock.get_order_results_detail` | 取得委託單結果 (含歷程) | ❌ | **僅當日** |
| `accounting.inventories` | 庫存查詢 | ❌ | 現時快照 |
| `accounting.realized_gains_and_loses` | 已實現損益查詢 | ❌ | **區間富邦說了算，未驗出** |
| `accounting.realized_gains_and_loses_summary` | 已實現損益彙總 | ❌ | 同上，回傳含 `start_date`/`end_date` |
| `accounting.unrealized_gains_and_loses` | 未實現損益查詢 | ❌ | 現時快照 |
| `accounting.bank_remain` | 銀行餘額查詢 | ❌ | ⚠️ 測試帳號的交割銀行不支援，見第 6 節 |

---

## 1. 日期參數：兩種格式都吃，可混用

適用「查詢歷史成交」`filled_history` 與「查詢歷史委託」`order_history`。
官方 docstring 同時出現 `"20231011"` 與 `"2023/09/15"` 兩種寫法，實測是**都吃，甚至能混用**。

| 傳入 | 結果 |
| --- | --- |
| `("20250401", "20250501")` | ✅ 26 筆 |
| `("2025/04/01", "2025/05/01")` | ✅ 26 筆（同上） |
| `("20250401", "2025/05/01")` | ✅ 26 筆（混用也吃） |
| `("2025-04-01", "2025-05-01")` | ❌ `日期格式錯誤` |
| `("20251301", "20251331")` | ❌ `日期格式錯誤` |
| `("", "")` | ❌ `日期格式錯誤` |

另外 `end_date` 是 optional，不帶則預設與 `start_date` 相同（官方文件有載，未另行實測）。

**對實作的影響**：我方對外 API 統一收 `YYYY-MM-DD`（ISO，前端友善），
**往富邦丟之前自己正規化成 `YYYYMMDD`**。不要把使用者輸入直接轉發——
`YYYY-MM-DD` 正好是富邦唯一不吃的那種。

## 2. 查詢區間硬上限 30 天

官方文件有載（「v2.1.1 起可查詢長期歷史資料，每次查詢最大設定 30 日日期區間」），
實測確認了**邊界是含 30 天**：

| 區間 | 結果 |
| --- | --- |
| `20250401 ~ 20250501`（30 天） | ✅ |
| `20250401 ~ 20250502`（31 天） | ❌ `Maximum query period is 30 days. Please adjust the parameters.` |

**筆數則沒有上限**：`20260801 ~ 20260825` 單次回 **205 筆**，無截斷跡象
（排除了 100/200 這類常見硬上限）。

**對實作的影響**：後端**必須自己切 30 天視窗迴圈再合併**。
前端傳「近三個月」這種區間不能直接轉發給富邦，否則整包失敗。

## 3. 區間邊界：錯誤的輸入不會報錯

| 情境 | 富邦回應 |
| --- | --- |
| 反向區間 `20250501 ~ 20250401` | ⚠️ `is_success=True` + **空陣列** |
| 未來區間 `20260825 ~ 20260901` | ⚠️ `is_success=True` + 空陣列 |
| 同一天 `20250429 ~ 20250429` | ✅ 合法，回 2 筆 |

**對實作的影響**：`start > end` 富邦不擋，**我方要在入口自己擋並回 400**。
否則使用者參數傳反了，畫面只會顯示「查無資料」，沒人知道是自己傳錯。

## 4. 損益三支 API 在測試環境完全不可用

```
realized_gains_and_loses(account)          已實現損益查詢 → is_success=False, "證券報表/表單定義資料不存在"
realized_gains_and_loses_summary(account)  已實現損益彙總 → is_success=False, "證券報表/表單定義資料不存在"
unrealized_gains_and_loses(account)        未實現損益查詢 → is_success=False, "證券報表/表單定義資料不存在"
```

已排除的可能原因：

- 不是登入問題 —— `login().is_success = True`
- 不是帳號權限問題 —— 同一個帳號打「庫存查詢」`inventories()` 正常回 7 筆
- 不是單一帳號問題 —— 41610792 與 58581758 兩組測試帳號結果相同
- 不是帳號類別選錯 —— 期權帳號的錯誤訊息是另一句「帳號類別錯誤」

**結論：測試環境沒佈報表資料，損益功能無法在測試環境驗證。**

**因此以下兩題仍是未知數，規劃損益功能時不可當成已知**：

1. 「已實現損益查詢」`realized_gains_and_loses` 沒有日期參數，**它回傳的區間到底多長**
   （當年度？近半年？近一個月？）。若富邦只回近一個月，「自訂區間損益」這個功能就不成立。
   → 可先打「已實現損益彙總」`realized_gains_and_loses_summary` 看回傳的
   `start_date` / `end_date` 是什麼，那是唯一會自報區間的地方。
2. `Realized` 物件**是否有隱藏的 `filled_no` / `order_no`**（官方 docstring 有省略號，
   不能當完整欄位表）。有 → 逐筆掛損益可行；沒有 → 只能維持「損益獨立端點」。

➡️ 待正式環境帳號到手後重跑 `smoke_test_accounting.py`，或直接問富邦窗口。

## 5. `FilledData` 完整欄位（實測 `dir()` 取得，非文件抄錄）

「查詢歷史成交」`filled_history` 回傳的每一筆：

```
date, branch_no, account, order_no, stock_no, buy_sell, order_type,
seq_no, filled_no, filled_avg_price, filled_qty, filled_price, filled_time, user_def
```

實測值範例：`seq_no` 為 `None`、`user_def` 為 `None`、`filled_no` 為 `"00000008700"` 這種 11 碼字串。
（官方文件註明 `seq_no` 與 `user_def` 只有主動回報才回傳，歷史查詢確實是 `None`，對得上。）

**注意**：成交紀錄這邊有 `filled_no` / `order_no` 可當 key，
但**損益那邊有沒有對應 key 尚未驗證**（見第 4 節）。在驗證之前，不要假設兩者能逐筆對起來。

## 6. 「銀行餘額查詢」`bank_remain`：2.2.9 已不再 panic，但測試環境仍拿不到資料

**2.2.9（現行版本）**：行為正常，回明確錯誤訊息。

```
bank_remain(證券帳號) → is_success=False, data=None
    message="此帳戶之交割銀行不在銀行餘額查詢支援範圍，目前支援範圍請參閱官方文件或洽營業員"
bank_remain(期權帳號) → is_success=False, message="帳號類別錯誤"
```

兩組測試帳號（41610792 / 58581758）的證券帳號**都是同一句「交割銀行不在支援範圍」**。

**對 F1 工單（富邦帳戶餘額查詢）的影響**：

- ✅ 不會炸掉服務（見下方 2.2.8 的舊行為），可以安全接。
- ⚠️ 但**測試環境驗不到成功回傳的形狀**——`data` 恆為 `None`。
  F1 的欄位映射目前仍是照官方文件猜的，**未經實測驗證**。
- ⚠️ 「交割銀行不在支援範圍」是**帳戶層級的限制，不是環境問題**。
  正式環境上線前必須確認：我方那個券商帳號的交割銀行**在富邦的支援名單內**。
  若不在，F1 這個功能對該帳號**根本拿不到餘額**，端點做出來也只會回這句錯誤。
  ➡️ 這題不是寫 code 能解的，要問富邦窗口或營業員。
- 端點設計上，這句訊息應該原樣往上帶（比照 F2 的 `rawStatus` 作法），
  否則使用者只會看到「查詢失敗」，不知道是自己的交割銀行不支援。

---

## ⚠️ 地雷

### （已修）2.2.8 的 `bank_remain()` 會 panic 打掛整個 Python 行程

**此問題在 2.2.9 已修復**，記錄於此僅供對照與版本決策參考（詳見 BUG_LOG.md BUG-007）。

2.2.8 呼叫 `bank_remain()` 不是回 `is_success=False`，而是**直接讓行程死掉**：

```
pyo3_runtime.PanicException: called `Option::unwrap()` on a `None` value
  at sdk-core/src/fubon/models/stock/bank_remain.rs:21:28
```

Python 層 `try/except` **攔不住**。根因是 Rust 核心對缺值欄位直接 `unwrap()`——
測試帳號的交割銀行不支援、`data` 為空，2.2.8 就炸；2.2.9 改成正常回傳錯誤訊息。

➡️ **本專案不可退版到 2.2.8。**
➡️ 通則仍然成立：任何新接的 `sdk.*` API，**先在獨立行程單獨試打一次**確認不會 panic，
再接進服務——第三方 Rust 綁定的失敗模式不一定是回傳值，可能是直接殺行程，
`is_success` 這層防禦是假的。

### `login().data` 是多筆，不能寫死 `data[0]`

一次登入會回傳證券帳號 + 期權帳號（測試帳號 58581758 就是兩筆），**順序不保證**。

```python
stock_accounts = [a for a in login.data if a.account_type == "stock"]
account = stock_accounts[0]
```

期權帳號打證券 API 會回「帳號類別錯誤」——這個錯誤訊息**跟報表不存在的錯誤是不同句**，
debug 時可以用來區分「帳號選錯」還是「環境沒資料」。
