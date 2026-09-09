# 富邦行情 provider（訂閱股票現價）

> 狀態：尚未實作。本文件描述待開發需求，不是目前 API 或 schema。
> 前半為白話說明，後半〈工單細節（補充）〉為完整設計與驗收條件。

> 與四張富邦帳務查詢工單不同的是，**這張票是富邦系列的登入起點**——
> 它建立共用的登入連線，[帳戶現金餘額](fubon-account-balance.md)、[今日委託](fubon-order-query.md)、
> [持倉](fubon-position-query.md)、[交割款](fubon-settlement-query.md) 四張查詢票都改成沿用它。

---

## 一句話

**這張票 = 讓後端連上富邦，訂閱股票之後就會持續收到它的現價。**

沒有這個，系統就沒有價格可以看，到價提醒、停損停利、TWAP 全部動不了。

---

## 現在是什麼情況

目前的報價來源是**永豐的 demo 帳號**，有兩個很硬的限制：

- 最多只能同時盯 **5 檔**股票
- 而且只能盯**指定的那 4 檔**（2330、2317、0050、00878），其他一概不行

demo 給主管看還行，但使用者想盯自己的股票就卡住了。富邦是正式行情，這兩個限制都不存在
（單一連線可以盯到 300 檔）。

---

## 「訂閱」跟「去問一次」差在哪

拿現價有兩種方式，這張票兩種都要做：

| | 怎麼運作 | 用在哪 |
|---|---|---|
| **訂閱**（WebSocket） | 連線掛著不斷，價格一動富邦就主動推過來 | 盯盤：使用者設了到價提醒，系統要一直盯著 |
| **問一次**（REST） | 我方發問，券商回一次就結束 | 使用者剛設定提醒的那一刻，先確認是不是已經到價了 |

盯盤不能用「問一次」硬幹——每檔每秒問一次，10 檔就是每分鐘 600 次，
而富邦的上限是每分鐘 300 次，直接被擋。所以盯盤一定得用訂閱。

---

## 為什麼「登入富邦」這件事要放在這張票

原本[查餘額](fubon-account-balance.md)那張票寫的是它自己負責登入，其他三張沿用它。這次改成由本票負責，
理由是查文件時發現的一個硬限制：

> **富邦的登入連線數上限是 10。** 每登入一次就吃掉一條，超過就直接登不進去。

而且如果程式關掉時沒有正常「登出」，那條殘留的連線還會繼續佔著額度。
這種問題最難查——平常都好好的，反覆重啟幾次之後，某天突然登不進去了。

所以正解是：**整個程式從頭到尾只登入一次**。富邦 SDK 本來就是這樣設計的，
行情、帳務查詢、下單全部掛在同一個登入底下，本來就不是三個獨立的東西。

那由誰來管這唯一的一次登入？答案是**行情**，因為行情是「開機連到關機」的長命連線，
帳務查詢則是使用者點開頁面才查一次。**活得最久的那個負責管，其他人搭便車。**

（順帶一提，這也解釋了為什麼程式不能開多個 worker 同時跑：4 個 worker 就是 4 次登入，
一口氣吃掉 10 條額度裡的 4 條。）

---

## 兩個一定要寫進工單的坑

### 坑 1：「試撮價」不是真的成交價

台股在**收盤前 13:25–13:30** 這五分鐘是「試撮」——系統會不斷顯示「如果現在收盤，
價格大概會是多少」，但**那些價格沒有任何一筆真的成交**。

富邦推過來的資料裡有兩個很像的欄位：

- `lastPrice`：最後成交價，**但包含試撮**
- `lastTrade.price`：最後**真實成交**的價格

如果程式順手拿了前者，會發生什麼事？使用者設「跌到 500 通知我」，
試撮階段閃過一個 499，系統就通知他了——但市場上根本沒有人用 499 成交過。
到了階段三（觸發就直接下單），那會是真的送出一張委託。

所以工單明文規定用 `lastTrade.price`。

### 坑 2：斷線之後，「連上了」不等於「還在收價」

網路斷線重連很正常，但富邦 SDK 有個要命的行為：**重新連上之後，它不會把你原本訂閱的股票補回來。**

結果就是——連線狀態顯示正常、程式沒有報錯、log 一片乾淨，
**只是再也沒有任何價格進來了**。所有到價提醒就這樣安靜地失效，沒有人會發現。

所以工單要求：斷線之後不只要重連，還要把原本訂的每一檔**重新訂一遍**。

---

## 為什麼選 `aggregates` 這個頻道

富邦提供好幾種訂閱頻道，這張票只用 `aggregates`（聚合行情），因為它**一則訊息就把需要的都給了**：
成交價、買價、賣價全在裡面。

另一個選擇是同時訂 `trades`（成交）和 `books`（五檔報價）兩個頻道，然後自己把兩邊的資料湊起來——
現在接永豐就是這樣做的。能少寫一份合併邏輯就少寫一份。

代價是 `aggregates` 走的不是富邦的「極速模式」。但我們做的是「到價提醒」，
不是毫秒級的程式交易（產品定位文件明文寫了不做高頻），這個代價等於不存在。

---

## 這張票**不做**什麼

- **不碰前端**。這張票交付的是「後端拿得到現價」。價格要怎麼送到瀏覽器上顯示，是另一張票。
- **不刪永豐**。富邦只是多一個選項，環境變數改一個字就能切換，永豐原封不動。
  萬一富邦有狀況，改回去就好。
- **不下單、不查帳務**（那是四張帳務查詢工單和之後的下單票）。
- **不做期貨**，只做股票。
- **不把報價存進資料庫**。行情是每秒都在變的資料，存下來只會爆掉，
  而且系統只需要「現在多少錢」，不需要歷史。
- **不做「行情健康度」那一套**（斷線警示、行情異常通知等等）。這些在 6/3 的範圍收斂時
  已經整套拿掉了，不要因為「WebSocket 會斷」就把它加回來。斷線就是重連、重訂閱、寫 log，沒了。

---

## 做完之後看得到什麼

`.env` 裡把 `QUOTE_PROVIDER` 從 `shioaji_demo` 改成 `fubon`，重啟。

然後：

- 可以盯的股票從 4 檔變成不限
- 同時盯的數量從 5 檔變成 300 檔
- 到價提醒、停損停利、TWAP 拆單——**全部功能一行程式都不用改**，因為它們只認得
  系統內部統一的「報價」格式，不管價格是誰給的

這也正是為什麼當初要把報價來源做成可抽換的：換券商是改設定，不是改功能。

---

## 工單細節（補充）

### Metadata

- 分層：上線後（富邦串接系列）
- 排序：富邦串接系列的第一張（登入起點），排在四張帳務查詢票之前
- ROM：**M**（本系列最大的一張：長命 WebSocket 連線 + 執行緒 callback + 斷線重訂閱。
  帳務四票都是「登入 → 查一次 → 轉 JSON」的無狀態端點，這張是有狀態的常駐元件）
- 依賴：無。**本票是富邦串接系列的登入起點**，不依賴任何帳務票。
- 被誰依賴：
  - [帳戶現金餘額](fubon-account-balance.md) / [今日委託](fubon-order-query.md) /
    [持倉](fubon-position-query.md) / [交割款](fubon-settlement-query.md) 改為沿用本票的共用登入
    （見〈帳務四票的同步修改〉）
  - 「報價傳給前端」（SSE / 輪詢端點）尚未開票，依賴本票
- 交付版本：V1
- 來源：2026-08-03 富邦方向（`docs/product.md`〈設計紅線〉）；`docs/domain.md`〈交易日與行情〉

### 背景

`docs/product.md`（2026-08-03）已定案報價來源改採富邦 Neo API，SDK 也已 vendored 進來
（`fubon_neo 2.2.9`，`pyproject.toml` + `vendor/*.whl` + `tests/test_fubon_sdk_import.py`），
但 **`src/` 內至今沒有一行 fubon 程式碼**——`QuoteProviderName` 只有 `shioaji_demo` / `in_memory`。

現況報價走永豐 demo（`QUOTE_PROVIDER=shioaji_demo`），受兩個 demo 限制卡住：
訂閱上限 5 檔（`DEFAULT_MAX_SUBSCRIPTIONS = 5`）、可訂標的只有 allowlist 裡的 4 檔
（`DEFAULT_DEMO_ALLOWED_SYMBOLS = {"2330","2317","0050","00878"}`）。富邦是正式行情，兩個限制都消失。

這張票補的缺口只有一句話：**讓後端能訂閱一檔股票並持續拿到它的現價**。
既有的 `QuoteEvaluationDispatcher` / `TradeIntentCoreDispatcher` 只認 `QuoteSnapshot`，
所以做完之後，到價提醒 / 停損停利 / TWAP 全部一行不改就吃得到富邦報價。

**這支是唯讀行情，與下單無關**，階段一（只通知不下單）就能做完。

### 富邦 API 事實（出自 `docs/api/fubon-llms-full.txt`，標行號）

#### 取價有兩條路，本票兩條都要

| 用途 | 做法 | 對應本專案 |
|---|---|---|
| 持續盯盤 | WebSocket 訂閱 `aggregates` | `QuoteProvider.subscribe` / `get_quotes` |
| 問一次就好 | REST `intraday/quote/{symbol}` | `CurrentPriceProvider.get_current_price` |

#### WebSocket：`Mode.Normal` + `channel='aggregates'`

**行情必須先登入才有權限**——文件每個行情範例都重複「需登入後，才能取得行情權限」這句。

```python
from fubon_neo.sdk import FubonSDK, Mode

sdk = FubonSDK()
sdk.login(...)                    # 必須先登入
sdk.init_realtime(Mode.Normal)    # aggregates 只在 Normal
stock = sdk.marketdata.websocket_client.stock
stock.on('message', handle_message)
stock.connect()
stock.subscribe({'channel': 'aggregates', 'symbol': '2330'})
```

- **`aggregates` 只在 `Mode.Normal` 下可用**。`init_realtime()` 不帶參數＝`Mode.Speed`，
  Speed 的可訂頻道只有 `trades` / `books` / `indices`（L4712-4741 的 Channels 清單、
  L5001 Aggregates 章節的 Python 範例用的是 `Mode.Normal`、L5821 模式切換）。
- 訂閱成功回 `{"event":"subscribed","data":{"id":"<CHANNEL_ID>","channel":...,"symbol":...}}`。
  ⚠️ **一次訂多檔時 `data` 是陣列**，單檔是物件（L4791-4811），解析要兩種都吃。
- 資料推播 `{"event":"data","data":{...},"id":...,"channel":"aggregates"}`，
  欄位表在 L5030 起、完整範例 L5171 起。
- 其他事件：`authenticated` / `error` / `heartbeat`（server 每 30 秒）/ `pong`。
  SDK 每 5 秒自動送 ping。

#### 選 `aggregates` 而不是 `trades` + `books` 的理由

`QuoteSnapshot`（`src/app/services/quote/base.py`）需要 bid / ask / last 三者，
`docs/domain.md`〈交易日與行情〉也規定「bid／ask／last 全缺時不觸發」，各策略缺 side price 時才 fallback last。

- `aggregates` 一則訊息就同時有 `lastTrade` + `bids` + `asks` → **單一 callback 組出完整 snapshot**。
- `trades`（L5670）只有成交 `price` / `bid` / `ask`（成交當下的買賣價，非五檔），
  `books`（L5263）只有五檔 → 要兩個 channel、兩個 callback，再寫一份合併邏輯
  （＝現有 shioaji 的 `_on_tick` + `_on_bidask` + `build_snapshot(previous=...)` 那套）。

代價是放棄 `Mode.Speed` 的低延遲。`docs/domain.md`〈交易日與行情〉只要求「10 秒內的有效行情」，
`docs/product.md`〈設計紅線〉也明文不做高頻與微秒級延遲工程，這個代價不存在。

#### 欄位對應（aggregates → `QuoteSnapshot`）

| `QuoteSnapshot` 欄位 | 富邦欄位 | 備註 |
|---|---|---|
| `symbol` | `symbol` | |
| `last_price` | **`lastTrade.price`** | 見下方 ⚠️ |
| `bid_price` | `bids[0].price` | 最佳一檔委買；`bids` 可能為空 |
| `ask_price` | `asks[0].price` | 最佳一檔委賣；`asks` 可能為空 |
| `quote_time` | `lastTrade.time` | **微秒** epoch，轉 Asia/Taipei tz-aware |
| `received_at` | — | 本地 UTC now |

⚠️ **`lastPrice` 是「含試撮」的最後成交價，絕對不可拿來當觸發依據。**

文件對 `lastPrice` 的定義原文就是「最後一筆成交價（**含試撮**）」，
另有獨立的 `lastTrial`（最後一筆試撮資訊）與 `lastTrade`（最後一筆成交資訊）兩個物件。

盤前試撮（08:30–09:00）會被 `QuoteValidator` 的「`quote_time` 必須在 regular session」擋掉，
**但收盤前試撮 13:25–13:30 落在 regular session 內**——用 `lastPrice` 就是拿一個
從未真正成交的價位去觸發使用者的委託。到了階段三（觸發即下單）這是會真的送出委託的錯誤。

`quote_time` 同理取 `lastTrade.time`，不要取 `total.time`（累計資訊時間）或訊息到達時間。

#### REST 單次查詢（給 `get_current_price`）

```python
from fubon_neo.fugle_marketdata.rest.base_rest import FugleAPIError

reststock = sdk.marketdata.rest_client.stock
try:
    quote = reststock.intraday.quote(symbol='2330')      # intraday/quote/{symbol}，L1801
except FugleAPIError as e:
    e.status_code    # 例: 429
    e.response_text  # {"statusCode":429,"message":"Rate limit exceeded"}
```

回傳欄位與 aggregates 幾乎一致（`lastPrice` / `lastTrade` / `bids` / `asks` / `avgPrice`…），
所以 **normalize 邏輯兩條路共用一份**。

⚠️ 2.2.4 起錯誤走 **Exception**，不是回傳錯誤碼（L1906-1918）。沒攔 `FugleAPIError` 會直接冒到上層。

#### 上限與連線數（L4334 建立連線、L4478 行情速率限制、L53403 交易速率限制）

| 項目 | 上限 |
|---|---|
| 行情 WebSocket | **單一連線 300 訂閱數；最多 7 條連線** |
| 日內行情 REST（`intraday/quote`） | 300 / min |
| 行情快照 REST | 300 / min |
| 歷史行情 REST | 60 / min |
| **交易連線數（`login`）** | **10** |

- REST 超限 → `429` + `{"statusCode":429,"message":"Rate limit exceeded"}`，需等 1 分鐘。
- WebSocket 訂閱超限 → `{"event":"error","data":{"code":1001,"message":"Maximum number of connections reached"}}`。
- **`login` 超限 → `Result{ is_success: False, message: "Login Error, 超過本應用程式連線限制==>[10]" }`。**
- 短時間內持續大量建立連線會被判定為攻擊並阻擋（404）。

ℹ️ 上表是這些數字在 repo 內的**唯一記載**。舊的 `PRODUCT_CONTEXT.md` 曾寫「每條連線 200 檔、
最多 5 條連線」（2.2.8 時期的舊數字，已不正確），該檔已於文件重構併入 `docs/product.md`，
而新版刻意只保留原則不寫具體數字：「券商的訂閱與速率上限是外部硬限制；達上限時清楚拒絕，
不建立配額平台或動態擴容」。施工時以本節為準，出處見 `docs/api/fubon-llms-full.txt`〈速率限制〉。

#### 斷線重連（L4951）與取消訂閱（L4813）

```python
def handle_disconnect(code, message):
    stock.connect()
    stock.subscribe({          # 重新訂閱您已訂閱過的 Channel 與 Symbol
        'channel': '<CHANNEL_NAME>', 'symbol': '<SYMBOL_ID>'
    })
```

- **SDK 不會自動重訂閱。** 重連完成後是「連上了，但一檔都沒訂」——
  這是最危險的失敗模式：連線狀態看起來正常，只是再也不會有價格進來，所有到價提醒靜默失效。
- callback 簽章：`connect()` 無參數、`disconnect(code, message)` 兩參數、
  `error(error)` 一參數、`message(message)` 一參數（L4890-4930）。
- **取消訂閱用的是訂閱回傳的 `id`，不是 symbol**：`stock.unsubscribe({'id': '<CHANNEL_ID>'})`。
  必須自己維護 `symbol → channel_id` 對照表，否則 `unsubscribe` 根本做不出來。

#### 登入回傳（`docs/api/fubon-neo-verified-behavior.md` 地雷節，已實測）

一次登入回證券帳號 + 期權帳號**多筆且順序不保證**。**禁止 `login.data[0]`**：

```python
stock_accounts = [a for a in login.data if a.account_type == "stock"]
account = stock_accounts[0]
```

### 結論（要做什麼）

三塊：**共用登入** + **`FubonQuoteProvider`** + **接線**。

#### A. 富邦共用登入 session（本票新建，四張帳務票沿用）

**這是本票與[餘額票](fubon-account-balance.md)的邊界變更，理由是硬限制不是偏好**：富邦交易連線數上限 10，
每次 `sdk.login()` 吃掉一條，而 SDK 的 `marketdata` / `accounting` / `stock`
本來就掛在**同一個 `sdk` 物件**底下。行情與帳務各登一次，是自己製造問題。

- **整個 process 只建一個 `FubonSDK` 實例、只 `login()` 一次。**
- 生命週期由 `main.py` 的 lifespan 管：`FUBON_ENABLED=true` 時啟動登入、關閉時 `logout()`。
  **`logout()` 不可省**——沒登出的殘留 session 會繼續佔著 10 條額度中的一條，
  反覆重啟後會出現「昨天還好好的，今天登不進去」。
- 取證券帳號一律用 `account_type == "stock"` 過濾，禁止 `data[0]`。
- 為什麼由行情擁有：行情是開機連到關機的**長命連線**，帳務是請求時查一次。
  生命週期最長的那個負責管理，其他人附掛——這與現有 `app.state.quote_provider` 的形狀一致。

**環境變數沿用[餘額票](fubon-account-balance.md)已定的那組**（本票不另創）：

| 變數 | 說明 |
|---|---|
| `FUBON_ENABLED` | bool，預設 `false`。false 時不載入 SDK、不讀憑證 |
| `FUBON_ID` | 身分證字號 |
| `FUBON_PASSWORD` | 登入密碼（`SecretStr`） |
| `FUBON_CERT_PATH` | 憑證檔路徑（放 `key/`，已在 `.gitignore`） |
| `FUBON_CERT_PASSWORD` | 憑證密碼（`SecretStr`） |
| `FUBON_MAX_SUBSCRIPTIONS` | int，預設 300（券商端單連線上限） |

- `QUOTE_PROVIDER=fubon` 但上述任一未設 → **啟動時 fail fast**
  （比照 `config.py` 既有的 `_enforce_shioaji_credentials`）。
- 密碼欄位一律 `SecretStr`；log 不得出現帳號 / 密碼 / 憑證路徑。
- 公版填券商測試環境帳號，客戶版換填客戶自己的；**不做 per-user 綁定券商帳號**。

#### B. `FubonQuoteProvider`

新增 `src/app/services/quote/fubon/`，分檔**照抄 `shioaji_demo/` 既有結構**（不是新設計，是沿用）：

| 檔案 | 職責 |
|---|---|
| `client.py` | **唯一 `import fubon_neo` 的模組**：login / logout / init_realtime / connect / subscribe / unsubscribe / REST quote |
| `normalize.py` | 純函式：aggregates 或 REST quote 的 dict → `QuoteSnapshot`。**不 import SDK**，可單獨測 |
| `provider.py` | `QuoteProvider` 八個方法 + `CurrentPriceProvider.get_current_price` |

`provider.py` 沿用 shioaji provider 已驗證的執行緒模型：`threading.RLock` 保護
`_snapshots` / `_subscribed` / `_channel_ids` / `_listeners`，**listener 在鎖外觸發**且逐一
try/except + `logger.exception`（misbehaving listener 不得殺掉 SDK 執行緒或阻塞後續派送）。

#### C. 接線

- `src/app/core/config.py`：`QuoteProviderName` Literal 加 `"fubon"`，新增上表環境變數。
- `src/app/services/quote/factory.py`：加一個 `fubon` 分支，**維持 lazy import**
  （`QUOTE_PROVIDER=in_memory` 時不得把 SDK 拉進來）。
- `src/app/main.py`：lifespan 不需要改邏輯——`provider.startup()` / `shutdown()` 已經是既有的
  掛載點，富邦的 login / logout 就掛在這兩個方法裡。

### 關鍵工程要求（施工時最容易寫錯的地方）

1. **只開 1 條行情連線。** `init_realtime()` 每呼叫一次＝多開一條連線，要多條得自己迴圈並各自
   保存 `websocket_client.stock`。demo 用量數十檔，單連線 300 檔綽綽有餘。
   **不做多連線輪替、不做配額管理、不做動態擴容**（`docs/product.md`〈設計紅線〉明文禁止）。
2. **斷線後必須重連 + 重訂閱。** `_subscribed` set 是重訂閱的唯一真相來源
   （形狀與 shioaji provider 一致）。重連後 `channel_id` 會換新，對照表要一起重建。
3. **維護 `symbol → channel_id` 對照表**，否則 `unsubscribe` 做不到（見上）。
4. **callback 跑在 SDK worker thread**，不可觸碰 request-scoped SQLAlchemy session
   （沿用 shioaji provider 檔頭既有的說明）。
5. **訂閱上限**到量時丟一個 fubon 自己的例外（HTTP 409），
   **不可重用 `QuoteSubscriptionLimitExceeded`**——`Makefile` 的 `check-shioaji-isolation`
   會把它視為 demo-only 符號外洩而讓 `make check` 紅燈。
6. **不做 allowlist。** `DEFAULT_DEMO_ALLOWED_SYMBOLS` 是永豐 demo 的限制，富邦沒有這回事，
   不要照抄。
7. **REST 路徑要攔 `FugleAPIError`**（2.2.4 起錯誤走例外），並把 429 對映成明確錯誤而非 500。
8. **先在獨立行程試打一次。** 照 `docs/api/fubon-neo-verified-behavior.md` 的通則：
   2.2.8 的 `bank_remain()` 是 Rust 核心直接 panic 殺掉 Python 行程，`try/except` 攔不住。
   行情這幾支（`init_realtime` / `connect` / `subscribe`）**尚未實測過**，接進服務前先單獨跑。

### 需同步處理的既有硬編碼

`src/app/core/config.py` 有兩個常數硬綁永豐：

```python
REQUIRED_CURRENT_PRICE_PROVIDER: QuoteProviderName = "shioaji_demo"
CURRENT_PRICE_SOURCE_NAME = "shioaji"
```

`api/deps.py` 的 `get_current_price_provider` 用第一個做 gate，
`schemas/quote.py` 的 `map_current_price` 用第二個填 response 的 `source` 欄位。
**不改的話，`QUOTE_PROVIDER=fubon` 之下 `GET /quotes/current-price/{symbol}` 會直接 503。**

本票要把這兩個改成隨 provider 走（gate 條件改為「provider 是否實作 `CurrentPriceProvider`」，
`source` 改為由 provider 自報），`config.py` 已在 isolation gate 的豁免清單內，不會踩線。

### 非目標

- **不做「報價傳給前端」**：SSE 端點、前端 WebSocket、把 `GET /quotes/current-price/{symbol}`
  改成讀記憶體快取——這些都是另一張票。本票交付的是**後端拿得到現價**，止於 `QuoteSnapshot`。
- **不刪 `shioaji_demo/`、不改 `QUOTE_PROVIDER` 預設值。** `fubon` 只是多一個可選值。
  對齊 `docs/product.md`「永豐是否續用是未知數」，也讓本票可以單獨 revert。
- **不下單、不查帳務**（帳務屬四張帳務查詢工單；下單是另一系列）。
- **不做 `candles` / `books` / `indices` / `trades` 頻道**，只訂 `aggregates`。
- **不做期貨**（`websocket_client.futopt` / `rest_client.futopt`），只做證券。
- **不做行情健康層**：`quote_unhealthy` / `last_quote_health` 這類欄位與通知已於 06-03
  範圍收斂中整套移除，不要因為「WebSocket 會斷」就把它加回來。斷線的處置只有重連 + 重訂閱 + log。
- **不做多連線、配額管理、多帳號輪替。**
- **不做行情落地**：不建報價歷史表。只有觸發當下的 quote snapshot 需要保存，輪詢行情不留歷史。
- **不抽 `BrokerClient` interface / ABC / factory**（同帳務票：只有一家券商、一個實作，
  第二家出現再抽——`docs/product.md`〈設計紅線〉明文要求）。

### 驗收條件

- [ ] `QUOTE_PROVIDER=fubon` 可啟動；lifespan 完成登入、建立行情連線、reconcile 既有 intent 的訂閱。
- [ ] `login()` 在整個 process 生命週期**只被呼叫一次**；`shutdown()` 有呼叫 `logout()`。
- [ ] 收到 aggregates 推播後，`get_quotes([symbol])` 回得出 `QuoteSnapshot`，
      且 `bid_price` / `ask_price` / `last_price` / `quote_time` 皆正確填入。
- [ ] **`last_price` 取自 `lastTrade.price`，不是 `lastPrice`**；有 `lastTrial` 的訊息
      不會污染 `last_price`。
- [ ] `quote_time` 為 Asia/Taipei tz-aware（微秒 epoch 已正確換算），`received_at` 為 UTC tz-aware。
- [ ] `bids` / `asks` 為空時 `bid_price` / `ask_price` 為 `None`，不是 0、不拋例外。
- [ ] 兩個 dispatcher（`QuoteEvaluationDispatcher` / `TradeIntentCoreDispatcher`）
      掛上 listener 後收得到 snapshot，**兩者的程式碼一行未改**。
- [ ] `unsubscribe(symbol)` 真的送出 `{'id': <channel_id>}`；對未訂閱的 symbol 為 no-op。
- [ ] 斷線事件觸發後：重新 `connect()`，且 `_subscribed` 裡每一檔都被重新 `subscribe`，
      `channel_id` 對照表重建。
- [ ] 訂閱數達 `FUBON_MAX_SUBSCRIPTIONS` → 回 409，錯誤類別**不是** `QuoteSubscriptionLimitExceeded`。
- [ ] `GET /quotes/current-price/{symbol}` 在 `QUOTE_PROVIDER=fubon` 下回 200
      （`REQUIRED_CURRENT_PRICE_PROVIDER` / `CURRENT_PRICE_SOURCE_NAME` 已解除硬綁）。
- [ ] REST 回 429 → 對映成明確的 provider 錯誤，不是 500；券商 message 只寫 log。
- [ ] `QUOTE_PROVIDER=in_memory` 時 `app.services.quote.fubon*` **不出現在 `sys.modules`**。
- [ ] `QUOTE_PROVIDER=fubon` 但憑證變數缺任一 → 啟動即 fail fast。
- [ ] `shioaji_demo/` 一行未動，`QUOTE_PROVIDER` 預設值未改。
- [ ] log 與回應皆不含帳號 / 密碼 / 憑證路徑。
- [ ] `make check` 全綠（含 `check-shioaji-isolation`）。

### 測試要求

一律以 fake SDK / fake client 注入，**不連真富邦**（CI 無憑證，必須綠）。

`tests/unit/quote/test_fubon_normalize.py`（純函式，不需 SDK）：
- 正常 aggregates 訊息 → 完整 `QuoteSnapshot`
- **`lastTrial` 有值、`lastTrade` 是舊值** → `last_price` 取 `lastTrade.price`，不受試撮影響
- `bids` / `asks` 為空陣列 → `bid_price` / `ask_price` 為 `None`
- 微秒 epoch → Asia/Taipei tz-aware 的換算正確（含跨日邊界）
- REST `intraday.quote` 的回傳走同一份 normalize，結果一致

`tests/unit/quote/test_fubon_provider.py`（fake client）：
- `subscribe` → 記下 `channel_id`；`unsubscribe` 送出的是 `id` 不是 symbol
- 重複 `subscribe` 同一 symbol 為 idempotent
- 達上限 → 丟 409 錯誤
- 斷線 callback → `connect` 被重打，且每個 `_subscribed` 的 symbol 都被重新 `subscribe`
- listener 拋例外不影響後續 listener，也不冒到 SDK 執行緒
- `startup()` 呼叫兩次只 `login` 一次

`tests/unit/quote/test_factory.py`（既有檔，補案例）：
- `QUOTE_PROVIDER=fubon` → 建出 `FubonQuoteProvider`
- `QUOTE_PROVIDER=in_memory` → `sys.modules` 不含 `app.services.quote.fubon*`
  （比照既有的 shioaji lazy-import 契約測試）

真實連線驗證屬**手動 smoke test**（盤中跑，盤後只拿得到收盤靜態值），結果寫在 PR 描述。

### 工程注意事項

- SDK 是**同步阻塞**：端點用 `def`（FastAPI 自動丟 threadpool），不要 `async def`。
- 價格一律轉 `Decimal`，不可用 float（沿用 `normalize.to_decimal` 的既有做法）。
  文件標型別為 `number`，實際回傳可能是 int 或 float，實打確認後轉一次。
- `main.py` 既有的 **SINGLE-PROCESS ONLY** 註記對富邦同樣成立，而且理由更硬：
  N 個 uvicorn worker ＝ N 次 `login`，直接吃掉 N/10 的連線額度。註記內容要一併更新
  （目前只寫了「多數券商會踢掉前一個 session」，富邦是**吃額度而非互踢**，失敗形狀不同）。
- 命名遵循 snake_case（模組 / 函式 / 變數），class 用 PascalCase，對外 JSON camelCase。
- 券商回傳的 `message` 只寫 server log，不回前端（對齊 `SECURITY_AUDIT.md` 既有原則）。

### 帳務四票的同步修改（✅ 已完成，2026-09-09）

四張帳務票各在獨立分支上（`docs/f1-…` ~ `docs/f4-…`），皆未 merge。
本票取得共用登入的擁有權後，那四張已同步改完：

- **[富邦帳戶現金餘額查詢](fubon-account-balance.md)**（PR #85）〈登入 session 處理〉整節改寫：原本的「module 級延遲初始化 +
  `threading.Lock`，第一次查詢才登入」作廢，改為沿用本票的共用 session；
  〈程式落點〉的 `account_balance.py` 不再自己 login；驗收條件改為
  「本票程式碼無任何 `login()` 呼叫」+「全 process 只登入一次」。
  〈環境變數〉那節保留不動（本票沿用同一組 `FUBON_*`）。
- **委託查詢 / 持倉查詢 / 交割款查詢**（PR #87 / #88 / #89）Metadata 的「依賴餘額票的登入 session」改指向本票，
  〈程式落點〉與驗收條件同步；餘額票保留為 `schemas/account.py` 與 `routes/account.py` 的建立者。
- 四張票的 `accounts.data[0]` 一併收緊為「先以 `account_type == "stock"` 過濾再取第一個」
  ——這是原本各票都照抄官方文件範例留下的錯誤（見上方〈登入回傳〉）。

⚠️ 五張票都會改 `README.md` 的同一個區塊，merge 時必然衝突，各自 rebase 即可。
