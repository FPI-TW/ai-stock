# 富邦 fubon_neo 2.2.9：實測行為筆記

> 這份是**實跑出來的事實**，不是官方文件的摘要。官方文件見同目錄 `fubon-neo-api.md`；
> 兩邊衝突時**以本文為準**，因為官方文件已被驗出有省略與範例不一致。
>
> - 實測日期：2026-08-25（第 1～6 節）、2026-08-26（第 7 節，條件單）
> - 版本：**fubon_neo 2.2.9**（與本專案 `vendor/` 一致）。
>   下方標「2.2.8」之處為升版前的舊行為，保留供對照。
> - 環境：`wss://neoapitest.fbs.com.tw/TASP/XCPXWS`（測試環境）
> - 帳號：測試帳號 41610792（證券 20203-7900280）、58581758（證券 20203-7900015 + 期權 15000-9623985）
> - 重跑腳本：`fubon-test` 專案的 `smoke_test_accounting.py`（第 1～6 節）、
>   `verify_multi_condition_logic.py`（第 7 節）。兩支的連線位址／帳號都走環境變數。

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

## 7. 條件單：`multi_condition` 的多個條件是 **AND**，且測試環境觸發後送不出委託

官方文件只寫「Condition List」，**沒有任何一句說明多個條件之間是 AND 還是 OR**。
這決定了條件單能不能拿來做「A 或 B 就出場」，所以實跑驗了一次。

### 實驗設計

用一組**互為否定**的條件，讓 AND 與 OR 的結果必然不同：

```python
cond_true  = Condition(..., trigger=MatchedPrice, trigger_value="1", comparison=GreaterThan)  # 盤中恆真
cond_false = Condition(..., trigger=MatchedPrice, trigger_value="1", comparison=LessThan)     # 恆假
```

- AND → 永遠不觸發
- OR → 立刻觸發（恆真那條就夠了）

**對照組不可省**：另掛一張「只有 `cond_true`」的 `single_condition`。
若對照組也沒觸發，代表測試環境根本沒在洗價，主實驗的「沒觸發」就不能解讀成 AND。

### 實測結果（2026-08-26 10:2x 盤中，2881 富邦金 lastPrice=139、`isContinuous=True`）

| 組別 | 條件 | 建立當下 `status` | 90 秒後 `status` |
| --- | --- | --- | --- |
| 主實驗 `multi_condition` | 恆真 + 恆假 | `洗價中(Y)` | `洗價中(Y)` **未觸發** |
| 對照組 `single_condition` | 只有恆真 | `觸發-委託失敗(X)` | `觸發-委託失敗(X)` |

三重佐證，指向同一個結論：

1. `get_condition_order_by_id()` 回傳的 `condition_content` **直接用「，且 」串接**：
   `'當於2026/08/26 富邦金成交價大於1元，且 富邦金成交價小於1元 全部成交為止'`
2. 矛盾條件停在洗價中不觸發 —— 若是 OR，恆真那條早該觸發
3. **對照組秒觸發**，證明測試環境確實在洗價 → 主實驗的「沒觸發」不是環境不動造成的誤判

**結論：多條件 = AND，條件越多越難觸發。**
要做「A 或 B 任一成立就動作」，必須**掛兩張獨立條件單**，不能塞進同一個 Condition List。

### ⚠️ 測試環境條件單觸發後委託會被後台擋掉

對照組觸發後 `error_message = '網路下單未開戶(後檯)[4385002]'`。
即測試帳號 41610792 的條件單**可掛、可洗價、可觸發，但觸發後委託送不出去**。

➡️ 「觸發之後」的一切行為在測試環境**驗不到**，包含官方文件那句
「待主單完全成交後，停損停利部分才會啟動」——這句仍是**未經實測的文件說法**。
同第 4 節的損益問題，要等正式環境帳號。

### 對本專案的影響

P1 已定案**自己洗價、自己送委託，不外包富邦條件單**（見 PROJECT_HISTORY 2026-08-19），
所以本節不改變 P1 的做法。記錄於此是為了：

- 若日後評估「改用富邦條件單」，AND 語意是硬限制，OCO／二擇一這類需求做不到
- 我方自己實作多條件時，**語意要和券商一致（AND）**，否則使用者從富邦 App 搬過來的
  心智模型會對不上

### 條件設計地雷：`TotalQuantity` 配 `LessThan` 幾乎必為廢條件

總量是盤中**單調遞增**的累積成交量，`總量 < N` 只在早盤成立，突破 N 之後永遠回不去。
與價格條件 AND 起來，整張單的實際壽命只到當日量能突破 N 為止——
之後價格就算跌穿目標也不會觸發，而使用者不會收到任何提示。

➡️ 我方若提供類似條件，**遞增型指標配 `<` 要在建單時就警告**，不能讓它靜默失效。

---

## 8. 登入 session 跨夜不失效，但行情 WS 收盤後會被斷且 SDK 不重連

實測：2026-09-17 10:26 登入（41610792），單一 SDK 實例訂 2330 `aggregates`，每 60 秒打一次 `stock.get_order_results` 並記錄行情 tick，
連續跑到 2026-09-18 09:50，約 23.5 小時。重跑腳本：`fubon-test` 專案的 `probe_session_lifetime.py`、`probe_after_hours_reconnect.py`。

| 項目 | 結果 |
| --- | --- |
| 交易端登入 | 23.5 小時內授權查詢全數成功，`set_on_event` 沒收到 `300`／`301`／`304`；盤中、收盤後、深夜到隔日開盤後都沒被踢 |
| 行情 WS | 14:05:49 被富邦端主動關閉（`WebSocketConnectionClosedException('Connection to remote host was lost.')`），`disconnect` 事件不帶 reason，代表**不是** SDK health-check 逾時 |
| 斷線後 | tick 全停，SDK **不會**自動重連，`disconnect` 之後什麼都不做 |
| 盤後重連 | 16:33 另一帳號（58581758）盤後可正常連上行情 WS；同一個已登入 SDK 重做 `init_realtime` → 重掛 listener → `connect` → `subscribe` 成功 |
| 兩帳號並存 | 兩個測試帳號在不同行程同時登入互不影響，第二個登入不會踢掉第一個 |
| `logout()` | 回 `True`，並觸發 `set_on_event('302', 'manual disconnect')`；**只登出交易端，不關行情 WS**：`fugle_marketdata` 的 reader thread 是 non-daemon `Thread(run_forever)`，logout 後不會退出，腳本要自行 `os._exit`。要收乾淨得先 `sdk.marketdata.websocket_client.stock.disconnect()`（會 `ws.close()` 並取消 auth／ping timer） |

原因：行情 WS 客戶端是 `fugle_marketdata` 2.5.0rc5 的 `WebSocketClient`，`__on_close` 只 emit `DISCONNECT_EVENT`，沒有任何重連邏輯；
health-check 只負責偵測（30 秒 ping、連續 2 次沒回應就主動 `disconnect`），不負責恢復。

同一個 `WebSocketClient.connect()` 是**無 sleep 的 busy-spin**：起 reader thread 後 `while True:` 輪詢 `auth_status`，直到 auth ack 或 `auth_timer`（約 5 秒）逾時，期間該 thread 滿載搶 GIL。
呼叫端不能把它放在任何 lock 內，也不能直接在 asyncio event loop 上呼叫。

行情 callback 拋例外不會弄斷連線，但會被記成假的連線錯誤：`pyee` 的 `_emit_run` 不攔例外，一路傳回 `websocket-client` 的 `_callback`，
那裡 catch 後改呼叫 `on_error`，於是 `fugle` emit `ERROR_EVENT`、我方 `_on_error` 記一條「websocket error」——但 socket 與 reader thread 都還活著，真正發生的只是那一筆 frame 被丟掉。
另外 `fugle.__on_message` 是**先** emit `MESSAGE_EVENT` 才處理 `authenticated`／`error`，所以 handler 拋例外還會跳過 SDK 自己的認證記帳。
結論：我方的行情 handler 必須自己攔下所有例外，不得拋回 SDK thread。

`init_realtime()` 只是換屬性，不會關掉舊連線：`fubon_neo/sdk.py` 裡它就一行 `self.marketdata = MarketData(sdk_token, mode, version)`。
舊的 `WebSocketClient` 仍被自己的 reader thread 參考著而不會被回收，`self.ee` 又是每個實例一份，所以我方先前掛上去的 `_on_message` 依然有效。
在舊 socket 還活著時重做 `init_realtime`，結果是每筆 frame 送進 handler 兩次，且舊 socket 繼續佔一個 WS 配額。重建前一定要自己 `disconnect()` 舊的。

**2026-09-22 14:18 盤後實測**（測試環境、帳號 58581758、SDK 2.2.9 mac arm64、腳本 `fubon-test/probe_teardown_and_reinit.py`）：

| 階段 | `threading.enumerate()`（主執行緒除外） |
| --- | --- |
| 登入後、尚未 `init_realtime` | 空 |
| 第一條 socket connect + subscribe 後 | `Thread-1 (run_forever)` **non-daemon**、`Thread-2` non-daemon、`Thread-3` |
| **不 disconnect** 直接重做 `init_realtime` + connect | `Thread-1 (run_forever)` **仍在**、`Thread-4 (run_forever)` non-daemon、`Thread-5` non-daemon、`Thread-3`／`Thread-6` |
| 兩條都 `disconnect()` 後 | **空** |
| 再 `logout()` 後 | 空 |

結論：(1) 兩個 `run_forever` reader thread 確實並存，BUG-018 的前提為實測確認，不是推論；
(2) `disconnect()` 是收掉 reader thread 的**必要且充分**步驟，`logout()` 不負責這件事——上表第四列 disconnect 之後就已經清空，logout 沒有再改變什麼；
(3) `main()` 返回後行程自然結束，exit code 0，**不需要 `os._exit`**，證實 BUG-011 的修法有效。

WS 事件名稱以廠商文件為準，只有 `authenticated`、`data`、`error`、`heartbeat`、`pong`、`subscribed`、`unsubscribed` 七種；
文件查無 `snapshot` 事件（PR1 曾誤加該分支，2026-09-22 移除）。

### 交易端事件代碼（`sdk.set_on_event(callback)`，官方文件「事件代碼 (Event Code)」）

| 代碼 | 意義 | 實測 |
| --- | --- | --- |
| `100` | 連線建立成功 | |
| `200` | 登入成功 | |
| `201` | 登入警示（例如 90 天未更換密碼） | |
| `300` | 斷線 | 23.5 小時內未出現 |
| `301` | 未收到連線 pong 回傳 | 23.5 小時內未出現 |
| `302` | 用戶執行登出，並斷線 | ✅ `logout()` 後立刻收到 `('302', 'manual disconnect')` |
| `304` | API Key 異動 (Revoked)，已強制登出（2.2.7 新增） | |
| `500` | 錯誤 | |

官方「自動重連」範例：收到 `300` → `logout()` → **建新的 `FubonSDK()`** → `login()` → 重新 `set_on_*` 所有 callback → 重連行情 WS，用 lock 防重入。
callback 的 code 是字串，比對時用 `"300"` 不是 `300`。

### 殘留 session 會讓行情 WS 被拒（2026-09-18 實測）

同一帳號（41610792）先後有三個行程未經 `logout()` 就結束（一個被 `kill`、兩個因例外中止）後，
新行程 `login()` **仍成功**，但 `init_realtime` → `connect()` 立刻收到伺服器關閉：

```
opcode=8 data=b'\x03\xe9Maximum number of connections reached'
→ fugle_marketdata 端拋 Exception("authentication timeout")
```

換乾淨的帳號（58581758）同一段程式一次通過。結論：

- 殘留 session 佔的是**行情 WS 額度**，交易端登入不受影響；額度多久釋放未測。
- 這是「WS 連線失敗」不是「登入失效」：程式不可據此重登，否則每次重試都再多一條殘留。
- 任何實測腳本一律 `try/finally: sdk.logout()`；用 `os._exit` 前記得 `sys.stdout.reconfigure(line_buffering=True)`，不然輸出會被吃掉。

### 本專案 client 的 session 釋放規則（2026-09-22，PR #95 review 定案）

上面兩節的共同結論是「登入一旦成功，任何失敗路徑都必須把 session 還給券商」，否則殘留累積到 WS 額度上限。
`FubonClient`／`FubonQuoteProvider` 依此收斂成下列規則，每條都有對應的單元測試：

| 路徑 | 規則 | 依據 |
| --- | --- | --- |
| `login()` 成功但 `data` 內無 `account_type == "stock"` | 先 `sdk.logout()` 再拒絕；此時 `_sdk` 尚未設定，之後的 `client.logout()` 會是 no-op，所以必須在原地釋放 | `login().data` 多筆、測試帳號期權帳戶排前面（見地雷節） |
| `login()` 回 `is_success=False` | **不**呼叫 logout；未實測登入被拒後 SDK 是否殘留連線，也未驗證對未登入成功的 SDK 呼叫 `logout()` 的行為 | 待 Linux 冒煙 probe |
| `provider.startup()`：login 成功、`connect_realtime()` 失敗 | 補 `client.logout()` 再 re-raise；`shutdown()` 以 `_started` 為門檻，此時仍為 False 不會替你 logout | 2026-09-18「殘留 session 會讓行情 WS 被拒」 |
| lifespan：`provider.startup()` 之後 DB reconcile 或 dispatcher 掛載失敗 | startup 之後到 `yield` 的整段都在同一個 try/finally 內，失敗一律 `provider.shutdown()` | 同上 |
| `provider.shutdown()` 的 unsubscribe | 每筆各自 best-effort，拋錯只記 warning，`logout()` 必跑 | 14:05 券商主動關 WS 且不重連，晚間停機時 unsubscribe 會拋 `WebSocketConnectionClosedException` |
| `client.logout()` | 先 best-effort `ws.disconnect()` 再 `sdk.logout()`；主動關閉觸發的 `disconnect` 事件記 info 不記 warning | `logout()` 不關 WS（上表） |
| `client.connect_realtime()` 重建行情連線 | `init_realtime` 前先 best-effort `disconnect()` 目前的 socket（與 `logout()` 共用 `_close_realtime_socket`）；首次連線時 `marketdata` 為 None 直接跳過 | `init_realtime` 只換屬性不關舊連線（上節）；工單已定案此方法「可重複呼叫」，實作必須真的能重複 |
| `client._on_message()` 收到畸形 `data` frame | 正規化與 handler 整段包 try/except，記 `frame dropped` warning 後繼續；每筆記一條，不去重也不限流 | 例外會被 SDK 吞掉並轉成假的 `websocket error`（上節）；廠商標 `symbol`／`time` 必填，實際觸發機率低但代價是誤導性告警 |
| `provider.startup()`／`reconnect_realtime()` 的 login／connect | 在 lock 外執行；lock 內只設 `_connecting` 旗標（已在連線中則直接返回，防重入）。連線期間 `subscribe()` 只記錄 `_subscribed` 不打 client，重連完成後在 lock 內一次重送整組，每檔恰好一次。PR2 的重連迴圈須以 `asyncio.to_thread` 呼叫 | `connect()` 是 busy-spin（上節）；否則 `get_quotes`／`subscribe`／`_on_snapshot` 全部排隊最長 5 秒，N 個使用者就是 N×5 秒 |
| `client.subscribe()`／`unsubscribe()` 在 WS 已斷後被呼叫 | SDK 拋自己的 `WebSocketConnectionClosedException`，client 轉成 `QuoteProviderUnavailableError("fubon", "subscribe_failed"/"unsubscribe_failed")`，不夾帶 SDK 原文；API 層只把 `QuoteProviderError` 映射成 503，否則建單掉 500、取消時 provider 的本地訂閱狀態清不掉 | 14:05 券商主動關 WS 且不重連；對齊 `ShioajiClient` 的包裝方式 |

尚未驗證（合併前一併做）：登入被拒（`is_success=False`）後 SDK 是否殘留連線（需刻意失敗登入，有鎖帳號風險，未執行）；`aggregates` frame 的 `lastUpdated` 在只有掛單變動（無成交）時是否前進（需盤中，見下節）。`disconnect()` 後行程能否自然結束已於 2026-09-22 實測通過（見上表）。

### 行情 frame 的兩個時間：`lastUpdated` 與 `lastTrade.time`（2026-09-22，PR #95 review 定案）

`aggregates` 頻道與 REST `intraday/quote` 的 frame 都同時帶兩個時間，意義不同，本專案分開用：

| 欄位 | 意義 | 對應 `QuoteSnapshot` 欄位 | 用途 |
| --- | --- | --- | --- |
| `lastUpdated` | frame 最後更新時間，掛單（`bids`／`asks`）變動就會動 | `quote_time`（缺時退回 `received_at`；官方文件未標為必揭示欄位） | 交易時段檢查與 10 秒新鮮度閘門。觸發先讀 bid／ask，所以新鮮度看掛單時間 |
| `lastTrade.time` | 最後一筆成交時間，沒人成交就不動 | `last_trade_time`（`lastTrade` 缺時為 `None`） | 只在 bid／ask 缺失、退用 `lastTrade.price` 時檢查：成交超過 10 秒視為無成交價 |

為什麼要分：冷門股可能幾十分鐘沒成交但掛單一直在動。若新鮮度綁 `lastTrade.time`，每個 frame 都被判過期，限價／到價意圖要等下一筆成交才可能觸發（PR1 原本就是這樣寫，review 抓到）。反過來若只看 `lastUpdated`，掛單全空時會拿十分鐘前的成交價當現價，這是組長的顧慮，所以備援路徑另看成交時間。

永豐對照：tick／bidask 各自帶一個時間，`quote_time` 取各 frame 時間，`last_trade_time` 只在 tick frame 更新。REST snapshot 只有一個 `ts`，兩欄同值。

`lastPrice` 含試撮，觸發一律不用（既有結論）。

⚠️ 未實測：`lastUpdated` 在只有掛單變動時是否真的前進，官方文件只寫「最後更新時間」。挑一檔冷門股訂閱幾分鐘，對照 `lastUpdated` 與 `lastTrade.time` 的變化即可確認；若不前進，`quote_time` 要改退回 `received_at`。

對本專案的影響：行情 WS 斷線與登入斷線（`300`／`301`／`304`）都可偵測，交由同一支重建迴圈在交易時段內恢復；登入本身不需要每天重做。
尚未驗證：登入超過 24 小時、跨週末是否失效（若失效預期會以 `300`／`301` 事件浮現）。

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

### `FubonSDK(url=...)` 不傳就是正式環境

2.2.9 二進位裡唯一內建的交易端 WS URL 是 `wss://neoapi.fbs.com.tw/TASP/XCPXWS`（production）；`url=None` 或省略就連真單，
登入照樣成功、不會有任何警告。測試環境 `wss://neoapitest.fbs.com.tw/TASP/XCPXWS` 一定要明傳。
本專案 `FubonClient` 的 `ws_url` 因此是必填參數（漏傳在建構時就 `TypeError`），設定層由 `FUBON_WS_URL` 供值、預設測試環境；
任何 dev script 或綁定流程直接建 client 都不得省略。

### `login().data` 是多筆，不能寫死 `data[0]`

一次登入會回傳證券帳號 + 期權帳號（測試帳號 58581758 就是兩筆），**順序不保證**。

```python
stock_accounts = [a for a in login.data if a.account_type == "stock"]
account = stock_accounts[0]
```

期權帳號打證券 API 會回「帳號類別錯誤」——這個錯誤訊息**跟報表不存在的錯誤是不同句**，
debug 時可以用來區分「帳號選錯」還是「環境沒資料」。
