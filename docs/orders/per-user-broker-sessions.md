# per-user 券商帳號綁定與每使用者行情 session

> 狀態：尚未實作。本文件描述待開發需求，不是目前 API 或 schema。
> 前半為白話說明，後半〈工單細節（補充）〉為完整設計與驗收條件。

## 一句話

**這張工單 = 讓每個使用者綁定自己的券商帳號，系統用他的帳號幫他盯盤、通知，人不在線也一樣；下單留給下一張票。**

---

## 現況與缺口

現在整套系統只用 `.env` 裡的一組券商帳號（永豐 demo）連券商，所有人的股票都訂在這一條連線上。這有兩個問題：

1. **下一階段要下單時，單子沒有辦法掛在使用者自己的帳戶下。** 共用帳號送出去的委託是「我們的」，不是使用者的。
2. **訂閱上限被所有人共用。** 券商的訂閱額度綁在帳號上，共用一個帳號等於大家搶同一份額度。

組長定案（2026-09-16）：行情與下單都改用使用者本人的券商帳號，而且使用者沒開 APP 也要能通知。

---

## 這張工單做的事（白話）

**1. 管理員幫使用者綁定他的券商帳號**

富邦登入需要四樣東西：身分證字號、登入密碼、憑證檔、憑證密碼。憑證由我們代客戶申請，申請完這四樣就在我們手上，所以綁定由管理員代辦：填一次，系統當場試登入，成功才加密存起來。不開放使用者自助綁定，將來有客戶需要再於該客戶分支加回。

**2. 系統用他的帳號幫他盯盤**

綁定成功後，系統就用他的帳號連上券商、訂閱他建的單子的股票。伺服器重啟時，會對每個綁定過的人重新登入。

**3. 人不在線也照常通知**

券商連線是系統自己維持的，跟使用者有沒有登入 APP 無關。價位到了就通知，行為跟現在一樣。

**4. 解除綁定就取消他的單**

沒有連線就沒有行情，留著的單永遠不會觸發，所以解綁時一併取消。

**5. 達上限就明確拒絕**

一台機器能養的連線有限，超過就拒絕綁定並說明原因，不做排隊或輪替。

---

## 做完之後看得到什麼

- 使用者頁面多一組「券商帳號」唯讀資訊：狀態、券商帳號後四碼、憑證到期日。綁定與解除由管理員在後台代辦。憑證一年一換，更新就是重新綁定一次（整包覆蓋），不另設更新端點；到期前主動提醒本票不做。管理端在瀏覽器把 pfx 轉 base64 隨 JSON 送出，伺服器不另做檔案上傳、不保存憑證檔。
- 綁定後建單，行情從他自己的券商帳號來；沒綁定就不能建單，Telegram 也會回「請先綁定」。
- 富邦成為第一個真正接上的券商；永豐 demo 模式保留給展示用。

---

## 這張工單**不做**什麼

- **不下單。** 只做地基，`execution_mode` 仍固定 `notify_only`。
- **不做前端頁面。** 交付的是後端 API 與行為。
- **不做第二家券商的抽象。** 只有富邦一個實作，`broker` 欄位列舉一個值；第二家出現再抽介面。
- **不做行情健康層、多連線輪替、配額平台。**
- **不動舊軌。** 舊軌 TWAP 在 per-user 模式下暫時拿不到參考價，等 TWAP cutover 到新軌再處理。

---

## 工單細節（補充）

### Metadata

- 分層：富邦串接系列的起點；取代原 F5 行情票的「整個 process 只登入一次」設計
- ROM：**L**（三個 PR：富邦 provider、綁定 API 與 session pool、行情接線切換）
- 依賴：`fubon_neo 2.2.9` 已 vendored（`vendor/fubon/`），`src/` 內尚無富邦程式碼
- 被誰依賴：下單第二階段、富邦帳務查詢（餘額／委託／持倉／交割）、停損停利
- 來源：2026-09-15 組長定案可代管使用者券商金鑰；2026-09-16 定案行情也走使用者帳號、離線也通知、富邦優先

### 富邦事實（影響設計）

出處：`docs/vendor/fubon/fubon-llms-full.txt`、`docs/vendor/fubon/fubon-neo-verified-behavior.md`。

- 登入四件套缺一不可：身分證字號、登入密碼、憑證 pfx 檔、憑證密碼。SDK 要的是憑證檔**路徑**，沒有免憑證的測試模式。
- `login().data` 多筆且順序不保證，證券帳號用 `account_type == "stock"` 過濾，禁止 `data[0]`。
- 行情 WebSocket 每連線 300 檔、每個 SDK 最多 7 條；REST `intraday/quote` 300/min。行情訊息 `lastPrice` 含試撮，觸發依據一律用 `lastTrade.price`。
- 行情 WS 客戶端（`fugle_marketdata` 2.5.0rc5）斷線後**完全沒有自動重連**，只發一個 `disconnect` 事件；訂閱狀態也不保留，必須自己重訂並重建 `symbol → channel_id` 對照表；取消訂閱用 channel id 不是 symbol。
- 收盤後富邦端會主動關閉行情 WS（2026-09-17 實測 14:05:49 斷線，錯誤「Connection to remote host was lost」，非 SDK health-check 逾時），之後行情 tick 全停；交易端登入不受影響，授權查詢持續成功。盤後可重新連上行情 WS；恢復方式是在同一個已登入 SDK 上重做 `init_realtime` → 重掛 listener → `connect` → 重訂閱，實測成功。因此開盤前必須有排程重建行情連線，否則隔日整天無行情。
- 兩個測試帳號各自在獨立行程登入互不影響，第二個帳號登入不會踢掉第一個。
- 登入連線上限 10（錯誤訊息「超過本應用程式連線限制==>[10]」）。未登出的殘留 session 會繼續佔額度。**這個 10 是每個券商帳號還是每個應用程式尚未實測**，若是後者，一套部署最多 10 位使用者。
- 行情、帳務、下單都掛在同一個 `sdk` 物件上，per-user 一個 SDK 實例天然涵蓋之後的帳務與下單。
- SDK 只有 Linux wheel，mac 開發與 CI 一律 fake client。2.2.8 曾有 Rust 核心 panic 殺掉 Python 行程的前科，任何新 SDK 呼叫先在獨立行程試打。

### 設計決定

- **Session pool**：`src/app/services/broker_session_pool.py` 的 `BrokerSessionPool`，`dict[user_id, QuoteProvider]` + `threading.Lock`，掛在 `app.state.broker_sessions`，取代 `app.state.quote_provider`。
  - `QUOTE_PROVIDER=fubon`：per-user 模式，每人一個 `FubonQuoteProvider`。
  - `QUOTE_PROVIDER=in_memory` 或 `shioaji_demo`：shared 模式，所有 user 共用同一個 provider 實例，行為與現在相同（既有測試與永豐 demo 不必綁定）。
- **金鑰儲存**：`broker_accounts` 以獨立 `id` 當主鍵、`user_id` 加 unique（現階段一人一帳戶；日後開放多帳戶只需拿掉 unique 並在單子上加 `broker_account_id`，不必重建表），`credentials_encrypted` 一欄存加密後的 JSON（富邦：`personal_id`（對應 SDK `login(personal_id, ...)` 參數名）、`password`、`cert_pfx_base64`、`cert_password`），不為每家券商開專屬欄位。加密重用 `app.core.mfa_crypto.encrypt_secret/decrypt_secret`，金鑰為 `MFA_ENCRYPTION_KEY`。
- **憑證檔**：登入時把 pfx 解密寫到 `tempfile.NamedTemporaryFile`（0600），呼叫 `sdk.login(...)` 後立刻刪除。若實測發現 SDK 重連時會重讀憑證檔，改為存在 `key/<user_id>.pfx` 並在 `stop` 時刪。
- **連線重建迴圈（週期檢查，不做每日固定重登）**：行情 WS 每日收盤後被富邦端斷掉且 SDK 不重連；登入本身實測跨夜不失效（2026-09-17 10:26～09-18 09:50 連續 23.5 小時授權查詢全數成功），但富邦有文件化的登入斷線事件可偵測。兩種斷線都只設旗標，恢復統一交給 pool 一支每 30 秒的週期任務（形狀比照 `IdempotencyCleanupScheduler`）。迴圈只在 `TradingSessionService.is_trading_day(today)` 為真且台北時間 08:30～13:35 內動作，週末與時段外不重連（避免收盤後反覆斷連）：
  - 行情 `on_disconnect` → `_realtime_connected = False`。迴圈看到只做 `reconnect_realtime()`（重做 `init_realtime` → 重掛 listener → `connect` → 重訂 `_subscribed`）。
  - 交易端 `sdk.set_on_event` 收到 `300`（斷線）、`301`（未收到 pong）、`304`（API Key 異動強制登出）任一 → `_login_alive = False`，pool 把 `user_id` 加入與 provider 物件獨立的 `_reconnect_pending`。`302` 是本系統自己 logout 的回音，不處理；`201`（登入警示，如 90 天未換密碼）只記安全的 warning code 給管理員看。
  - 迴圈每個 tick 對 `_reconnect_pending` 每人最多試一次：從 DB 解密金鑰，用 `pool.prepare` 建立候選 provider，登入並重訂 owner 的 active symbols，先 `mark_login_ok` + commit，再用 `pool.activate` 原子切換、移除 pending，最後才 shutdown 舊 provider。DB commit 前任一失敗都先 rollback，若候選 provider 已建立再 `pool.discard(candidate)`，接著 `mark_login_failed(code)` + commit；pending 保留到下個 tick，不另做退避。啟動或綁定當下的登入失敗不加入 pending，仍需重新綁定或重啟。這個迴圈只負責重連，不產生任何行情健康狀態或通知。
- **Dispatcher 依 owner 過濾**：同一 symbol 會從 N 條 session 各來一次 tick，`TradeIntentCoreDispatcher.dispatch(snapshot, *, owner_user_id=None)` → `repo.system_list_active_by_symbols(symbols, owner_user_id=...)`。pool 用 `functools.partial(dispatch, owner_user_id=uid)` 掛 listener；shared 模式傳 `None` 掃全部。
- **Telegram 路徑不能靠 request user 取 session**：webhook 無 Bearer，owner 由 `TELEGRAM_OWNER_EMAIL` 在 command 內解析，因此 `CreateTradeIntentCommand` 改注入 pool，執行時 `pool.require(inp.owner_user_id)`。
- **連線上限**：`BROKER_MAX_SESSIONS`（預設 2，實測後調，硬上限不超過 10），達上限綁定回 409。
- **解綁取消該人 active intent**：沿用 `cancel_active_for_owner`。
- **舊軌**：fubon 模式沒有系統 session，舊軌 `QuoteEvaluationDispatcher` 不掛；`TwapSliceScheduler` 注入空 `InMemoryQuoteProvider()`（slice 照時間通知、無參考價），等 TWAP cutover 到新軌後改用 `pool.get(owner)` 取價。shared 模式維持現狀。寫入 `technical-debt.md`。
- **不抽介面**：富邦程式碼放 `src/app/services/quote/fubon/`，`client.py` 是唯一 `import fubon_neo` 的模組，比照 `shioaji_demo/` 的隔離紀律；不加 Makefile gate、不建 ABC／registry／factory。

### PR1 `feat/fubon-quote-provider`（純新增，不接線）

- `src/app/services/quote/fubon/client.py`：`FubonClient(*, personal_id, password, cert_pfx: bytes, cert_password)`，`login()`（temp 檔寫 pfx → `sdk.login` → 過濾 `account_type=="stock"`；失敗對映為 PR1 內定義的安全 `FubonLoginFailureCode` 與不帶 SDK 原文的 `QuoteProviderUnavailableError("fubon")`）、`logout()`、`init_realtime(Mode.Normal)` + `connect`、`subscribe`／`unsubscribe`（維護 `symbol → channel_id`）、`on_disconnect` 只記安全 log 並把 `_realtime_connected` 設為 `False`、`set_on_event` 收 `300`／`301`／`304` 把 `_login_alive` 設為 `False`（`201` 只記 code、`302` 忽略）、`connect_realtime()` 可重複呼叫（重做 `init_realtime` → 重掛 listener → `connect`，channel_id 對照清空後由 `subscribed` 事件重建；`init_realtime` 換 token 失敗才視為登入失效，`ws.connect` 失敗只標記行情未連線）。訂閱清單由 provider 單一擁有，provider 的 `reconnect_realtime()` = `client.connect_realtime()` 後逐檔重訂、`get_stock_quote(symbol)`（REST，攔 `FugleAPIError`，429 對映 provider 錯誤，對外 `details` 不回傳 SDK 原文）。
- `src/app/services/quote/fubon/normalize.py`：純函式，aggregates／REST dict → `QuoteSnapshot`；`last_price` 取 `lastTrade.price`，`bids/asks` 空 → `None`，`lastTrade` 缺 → `last_price/last_trade_time` 皆 `None`。
- `src/app/services/quote/fubon/provider.py`：`FubonQuoteProvider`，實作 `QuoteProvider` 與 `CurrentPriceProvider`，執行緒模型照抄 `shioaji_demo/provider.py`；`max_subscriptions` 預設 300，超限丟 fubon 自己的例外（409）；無 allowlist。
- `QuoteSnapshot` 兩個券商時間並存：`quote_time: datetime`（frame 時間，富邦取 `lastUpdated`、缺時退回 `received_at`；永豐取 tick／bidask 各自的時間）與 `last_trade_time: datetime | None`（成交時間，富邦取 `lastTrade.time`，永豐只在 tick frame 更新）。時段與新鮮度檢查用 `quote_time`，因為觸發先讀 bid／ask；`last_price` 只是 bid／ask 缺失時的備援，evaluator 另外要求 `last_trade_time` 也在 10 秒內，否則視為無成交價。對外 JSON `quoteTime` 映射 `quote_time`，另加 `lastTradeTime` 可為 `null`。（2026-09-18 原定案只留 `last_trade_time` 並以 property 回退，2026-09-22 review 發現冷門股只有掛單在動時每個 frame 都被判過期，改為現制。）
- `config.py`：`QuoteProviderName` 加 `"fubon"`；`REQUIRED_CURRENT_PRICE_PROVIDER`／`CURRENT_PRICE_SOURCE_NAME` 硬綁永豐解除（gate 改「provider 是否實作 `CurrentPriceProvider`」，`source` 由 provider 自報）。
- `factory.py`：`build_quote_provider(settings, *, credentials=None)`，`fubon` 分支 lazy import，缺 credentials → `ValueError`。`FubonCredentials`（frozen dataclass、repr 遮罩）先放 `fubon/client.py`，PR2 的 domain 模組直接引用或搬移。`config.py` 加 `FUBON_WS_URL`（預設測試環境 `wss://neoapitest.fbs.com.tw/TASP/XCPXWS`，真單環境才改），仍無任何 `FUBON_*` 帳密變數。PR1 合併後 `QUOTE_PROVIDER=fubon` 啟動會因缺 credentials 失敗，屬預期，PR3 接線後解除。
- 合併前實測：用測試帳號在獨立行程跑 `login → init_realtime → connect → subscribe('2330') → logout`，確認不 panic、記錄單一 SDK 實例 RSS。2026-09-18 本機（mac wheel）實測發現：同帳號殘留 session 未登出時，行情 WS 會被拒 `Maximum number of connections reached`，而交易端登入仍成功；因此 `connect_realtime` 的 WS 失敗**不可**視為登入失效，只有 `init_realtime` 換 token 失敗才是。

### PR2 `feat/broker-account-binding`（schema + 綁定 API + pool；不改行情接線）

**Schema**

- Migration `202609160001_create_broker_accounts.py`，`down_revision` 接當時的 head。
- `broker_accounts`：`id` UUID PK、`user_id` UUID NOT NULL FK users.id UNIQUE、`broker` TEXT CHECK IN ('fubon')、`credentials_encrypted` BYTEA NOT NULL、`broker_account_no` TEXT NOT NULL（登入結果的證券帳號，非機密，顯示用）、`cert_expires_at` TIMESTAMPTZ NOT NULL（綁定時以 `cryptography` 的 `pkcs12.load_key_and_certificates` 解 pfx 取 `not_valid_after_utc`，同時驗證憑證密碼）、`status` TEXT CHECK IN ('active','login_failed')、`last_login_at` TIMESTAMPTZ NULL、`last_error` TEXT NULL、`created_at/updated_at`。`last_error` 只能存白名單的安全訊息，不存 SDK exception 原文；downgrade 直接 drop。
- Model `src/app/db/models/broker_account.py`；Domain `src/app/domain/broker_account.py`（`BrokerAccountData`、`FubonCredentials` 僅記憶體且 `__repr__` 遮罩、`BrokerLoginFailureCode = Literal["login_rejected", "session_limit", "provider_unavailable", "unknown"]`、`BrokerAccountError` → `BrokerAccountNotBoundError`／`BrokerLoginFailedError`／`BrokerSessionLimitReachedError`）；command 把 PR1 的 `FubonLoginFailureCode` 對映為 broker domain code。Repo `src/app/repositories/broker_account_repository.py`（`get_by_user_id`、`list_all`、`upsert`、`delete`、`mark_login_ok`、`mark_login_failed(code)`、`get_credentials` 在 repo 內解密）。`mark_login_failed` 只把 code 對映成固定的安全中文訊息；DB、API 與 log 都不得保留 `str(exc)`，log 只記 `user_id`、failure code 與 request id。`mark_login_ok` 同時設 `status='active'`、更新 `last_login_at` 並清空 `last_error`。

**Pool**

- `BrokerSessionPool(settings, *, shared=None)`：`set_quote_listener`、`get`、`require`、`prepare(user_id, credentials) -> PreparedBrokerSession`、`activate(candidate)`、`discard(candidate)`、`stop`、`stop_all`、`bound_user_ids`。`prepare` 在鎖內為 user 取得單一操作 token 並保留名額，再於鎖外建 provider 與登入，不改動正式 session；同一 user 的第二個 prepare、stop 或解綁必須等候或明確拒絕，不得與候選切換並行。替換同一 user 不多佔應用層名額，但候選登入仍可能被富邦外部硬上限拒絕，此時舊 session 保留。`prepare` 自身失敗時必須釋放 token 與名額保留。`activate` 只在鎖內驗證 token、掛 owner listener、原子替換 dict 並消耗名額保留，不做網路或 DB I/O，回傳舊 provider 供鎖外 shutdown。`discard` 先在鎖內釋放 token 與名額保留，再於鎖外 shutdown 未啟用的候選 provider。`stop` 必呼叫 `logout`；log 只記 user_id 與安全錯誤碼。
- `config.py` 加 `broker_max_sessions`（預設 2，validator 1..10）、`BrokerName = Literal["fubon"]`、`BROKER_NAMES`。

**綁定 API**（全部需登入；寫入只開給管理員）

| 端點 | 行為 |
|---|---|
| `GET /me/broker-account` | 200：`broker`、`brokerAccountNo`（遮罩後四碼）、`status`、`certExpiresAt`、`lastLoginAt`、`lastError`、`updatedAt`；無 → 404 |
| `PUT /admin/users/{user_id}/broker-account` | 需 `AdminUserDep`，目標使用者須為 active。body `broker, personalId, password, certPfxBase64, certPassword`（`extra="forbid"`，pfx ≤ 64KB）。`pool.prepare` 建候選 provider（失敗 → 422 `BROKER_LOGIN_FAILED`，舊 session 不動、不落 DB）→ 候選 provider 訂閱該 user 的 active symbols → `upsert` → audit `broker_account_bound`（`actor_type="admin"`）→ commit → `pool.activate` 原子切換 → 鎖外 shutdown 舊 provider。commit 前任一步失敗只 rollback 並 `pool.discard(candidate)`，舊 session 繼續盯盤；`activate` 不做可失敗的 I/O。舊 provider shutdown 失敗只記安全 warning，不回滾已生效的新綁定。已有 admin 認證，不另加 rate limit |
| `DELETE /admin/users/{user_id}/broker-account` | 需 `AdminUserDep`。204 冪等：`cancel_active_for_owner` → `delete` → audit `broker_account_unbound`（`actor_type="admin"`）→ commit → `pool.stop` |

- 不做自助 `PUT/DELETE /me/broker-account`、不做 CLI 預寫：綁定情境只有我們代辦，管理員端點一條路即可；系統可在無人綁定時啟動，管理員登入後再綁第一位。
- 錯誤碼：`BROKER_ACCOUNT_NOT_BOUND`(409)、`BROKER_LOGIN_FAILED`(422)、`BROKER_SESSION_LIMIT_REACHED`(409)。回應與 log 不含身分證字號、密碼、憑證。
- Commands `src/app/commands/broker_account.py`；Schemas `src/app/api/schemas/broker_account.py` 逐欄 alias。

### PR3 `feat/per-user-quote-sessions`（接線切換）

- `main.py` lifespan：建 dispatcher 並 `pool.set_quote_listener`；per-user 模式對 `broker_accounts.list_all()` 逐一 `pool.prepare`，讓候選 provider 依 `active_or_scheduled_symbols_by_owner()` 訂閱，先 `mark_login_ok` + commit，再 `pool.activate`。失敗則 rollback、`pool.discard(candidate)`、`mark_login_failed(code)` + 安全 warning 並 commit，然後 continue（啟動絕不因單人失敗中止）；shutdown `pool.stop_all()`。移除 `app.state.quote_provider`。
- `quote_dispatcher_core.py`、`trade_intent_core_repository.py`：owner 過濾。
- `commands/trade_intent_core.py`：create 在限額檢查後、`repo.create` 前 `pool.require(owner)`；cancel 用 `count_active_or_scheduled_for_user_symbol` 決定退訂（跨 owner 計數會讓本人 session 永不退訂）。
- `deps.py`：`get_quote_provider` 改由 pool 取本人 session；`current-price` 與 `/dev/*` 因此需登入。
- `commands/account.py`：disable 後 `pool.stop`（列保留）；reactivate 後有金鑰就走與 lifespan 相同的 `prepare` → 訂閱 → 狀態 commit → `activate` 流程，失敗會清理候選 provider 並標記 `login_failed`，但不讓復權失敗。
- `commands/telegram_intent.py`：`BrokerAccountNotBoundError` 回覆「尚未綁定券商帳號，請聯絡管理員綁定後再確認。」，draft 維持 pending。
- 設定與部署：`.env*`、`cd.yml` 加 `BROKER_MAX_SESSIONS`；不需要任何 `FUBON_*` 帳密變數。無人綁定時可啟動但行情全停，由管理員登入後綁第一位。
- 合併前實測：登入 session 能活多久、富邦會不會收盤後或深夜強制登出、憑證是否只在 `login()` 時讀取。已實測：盤中不踢（2026-09-17 10:26～14:01 本機單 session 訂 2330，授權查詢與行情全程正常）；收盤後 14:05 行情 WS 被斷但登入仍有效（見富邦事實）；登入從盤中到隔日開盤後仍未失效（同日 10:26 至次日 09:50 連續 23.5 小時授權查詢全數成功，無任何 `300`／`301`／`304` 事件）。行情與登入斷線都靠同一支重建迴圈在交易日時段內接回；登入斷線以官方事件代碼 300／301／304 偵測，不排程重登。兩個測試帳號同 process 登入確認連線上限語意；量測 1／2／3 個 SDK 實例各訂 5 檔跑 10 分鐘的 RSS，決定 `BROKER_MAX_SESSIONS` 預設。
- 文件同步：`architecture.md`（一個 process 持有 N 條 session，仍不可多 worker）、`api.md`、`operations.md`（記憶體、重啟全員重登、殘留 session 佔額度、`login_failed` 處置、`MFA_ENCRYPTION_KEY` 輪替涵蓋 `broker_accounts`）、`technical-debt.md`（舊軌 TWAP 無參考價、每 tick 每 session 重跑 lifecycle UPDATE、dispatcher 觸發後不退訂）。完成後刪除本工單。

### 對其他富邦工作的影響

- 原 F5 行情票的「共用登入／`.env` 帳密／整個 process 只登入一次」作廢，技術內容併入 PR1。
- 帳務查詢（餘額／委託／持倉／交割）改用 `pool.require(current_user)` 取本人 session 的 `sdk.accounting`，端點自然是本人帳務。
- 下單第二階段：`FubonClient.place_order()`、`SubmitOrderCommand` 用同一個 session 下單、`broker_orders` 表與 `execution_mode` 放寬都在那張票才加，本票不預埋。

### 驗收條件

- [ ] `QUOTE_PROVIDER=fubon` 可啟動；已綁定使用者於 lifespan 各登入一次並訂閱自己的 active symbols；其中一人登入失敗不影響其他人與 `/health`。
- [ ] `PUT /admin/users/{user_id}/broker-account` 登入成功才落列，`credentials_encrypted` 可用 `decrypt_secret` 還原；首次綁定失敗回 422 且無列。重新綁定時，候選登入、訂閱、DB、audit 或 commit 任一失敗都不取代或停止舊 session；全部成功才原子切換，舊 provider shutdown 失敗不回滾新綁定。
- [ ] `GET /me/broker-account` 不回任何機密；一般使用者呼叫 admin PUT/DELETE 回 403；`DELETE` 後該人 active intent 皆 cancelled、session 已 logout。
- [ ] `BROKER_MAX_SESSIONS=1` 時第二人綁定回 409。
- [ ] 兩個 owner 同 symbol 各有 active intent，A 的 session 推價只觸發 A 的單。
- [ ] 未綁定者建單回 409 `BROKER_ACCOUNT_NOT_BOUND`；Telegram 確認時回覆提示。
- [ ] `last_price` 取自 `lastTrade.price`；`lastTrade` 缺時 snapshot 仍建立且 validator 以 `received_at` 擋盤前試撮。
- [ ] `on_disconnect` 只標記不重連；重建迴圈僅在 `is_trading_day` 且台北時間 08:30～13:35 內對 `not connected` 的 session 呼叫 `reconnect_realtime()`，重訂 `_subscribed` 內每一檔並重建 channel_id 對照表；週末與時段外不重連。
- [ ] `set_on_event` 收到 `300`／`301`／`304` 後，`user_id` 進入獨立的 `_reconnect_pending`；重建迴圈在交易日時段內以候選 provider 重登並重訂，失敗後 pending 保留且下個 tick 再試，成功後 `mark_login_ok` 清掉 `login_failed` 與 `last_error`，其他 session 不受影響。
- [ ] `QUOTE_PROVIDER=in_memory` 時 `app.services.quote.fubon*` 不出現在 `sys.modules`；`shioaji_demo/` 行為不變。
- [ ] 用內含機密 sentinel 的 fake SDK exception 驗證 DB `last_error`、log 與回應皆不含身分證字號、密碼、憑證或 exception 原文，只出現白名單的安全訊息。
- [ ] `make check` 全綠（含 `check-shioaji-isolation`）；migration up → down → up 通過。

### 已知風險

- 連線上限 10 若是每應用程式，一套部署最多 10 位使用者，且異常關機殘留 session 會暫時吃掉額度。
- 交易時段內斷線到重建迴圈接回之間（最多約 30 秒加重連耗時）收不到行情，價位若在這段時間內掃過觸發價又回頭，該次觸發會漏掉；只通知階段可接受，不做逐筆回補。進入下單階段再評估是否在接回後補查一次 REST 現價。
- 每次部署全員重登券商。啟動或綁定時登入失敗的 `login_failed` 不會自動重試（沒有斷線事件可觸發），需重新綁定或重啟；斷線後重登失敗的則由重建迴圈每 30 秒再試。登入實測跨夜不失效；跨週末或連續多日是否失效尚未驗證；若失效，事件 `300`／`301` 會觸發重建迴圈自動重登。
- `TradingSessionService` 目前只能排除週末，還沒有台股休市日 calendar；平日休市的 08:30～13:35 仍可能嘗試重連。市場 calendar 上線後，重建迴圈改用同一份交易日資料。
- 憑證檔必須落地成暫存檔才能登入；暫存檔生命週期要實測。
- `MFA_ENCRYPTION_KEY` 現在同時保護券商金鑰。
- SDK Rust 核心 panic 攔不住，會殺掉整個 process，所有使用者一起斷。
- mac 無 SDK，per-user 真連線只能在 Linux／EC2 驗證。
