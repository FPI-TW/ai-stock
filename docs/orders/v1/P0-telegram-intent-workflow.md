# Telegram inbound intent workflow（demo）

## 範圍

`POST /telegram/webhook` 只接受 Telegram 設定的 `TELEGRAM_CHAT_ID` 群組，並以
`X-Telegram-Bot-Api-Secret-Token` 比對 `TELEGRAM_WEBHOOK_SECRET`。其他 chat、bot
訊息、非文字更新都回 HTTP 200 且不觸發 LLM 或資料庫副作用。

BotFather 的群組 Privacy Mode 必須關閉，否則 Telegram 不會把普通群組文字送給
bot；正式環境也必須先設定 active owner 與 webhook secret。

群組中的 owner 固定由 `TELEGRAM_OWNER_EMAIL`（預設 `admin@tingfong.com`）解析為
active user；群組成員都能補充、確認、取消同一筆 draft。每個設定群組最多一筆
尚未完成（`pending` 或 `confirming`）interaction，TTL 五分鐘。

## LLM 邊界

每則 allowed group 的非 bot 文字都送 DeepSeek。預設 model 是
`deepseek-v4-flash`、timeout 10 秒，請求明確使用
`thinking: {"type":"disabled"}`。回應必須是 `extra=forbid` 的 Pydantic JSON
schema；capability 僅允許：

| capability | strategy | transaction_mode | target |
| --- | --- | --- | --- |
| `limit_buy` | `limit_buy_order` | `single_notification` | required |
| `limit_sell` | `limit_sell_order` | `single_notification` | required |
| `market_buy` | `market_buy_order` | `partial_fill_allowed` | none |
| `market_sell` | `market_sell_order` | `partial_fill_allowed` | none |

`quantityLots` 必須是正整數；symbol 必須是數字代號；限價 target 必須通過既有
tick-size / symbol validation。ordinary chat 與 unsupported response 靜默忽略。

## Draft lifecycle

不完整的 supported request 建立 clarification interaction，只有「取消」按鈕；
完整 request 建立 draft，顯示「確認建立／取消」。同一 request 的補充會更新原
interaction 並 edit 同一 bot message；不同交易 request 不覆蓋現有 draft，而提示
先補完或取消。過期、取消、確認均以 `WHERE status='pending'` 的條件更新防止一般
重複 callback；確認先以條件更新 claim 為 `confirming`，再呼叫 core command。

確認只呼叫 `app.commands.trade_intent_core.CreateTradeIntentCommand`，交易意圖及
`telegram_intent_interactions.created_trade_intent_id` 都屬新軌；interaction FK
永遠指向 `trade_intent_core.id`，不引用 legacy `trade_intents`。

Telegram API send/edit/answer 都是 best-effort：失敗只安全記錄 method/status，
不記錄 bot token，webhook 仍回 200。callback 會 answer query，並移除原訊息按鈕。

## 部署

CD 在 app healthcheck 成功後，以 `TELEGRAM_WEBHOOK_URL`、bot token 與 secret 呼叫
`setWebhook`，並要求 `allowed_updates=["message", "callback_query"]`；接著呼叫
`getWebhookInfo`，精確驗證 URL 與 allowlist，驗證失敗即讓 CD job 失敗。

部署所需設定為：GitHub Secrets `TELEGRAM_BOT_TOKEN`、`TELEGRAM_WEBHOOK_SECRET`、
`DEEPSEEK_API_KEY`；GitHub Variables `TELEGRAM_CHAT_ID`、`TELEGRAM_OWNER_EMAIL`、
`TELEGRAM_WEBHOOK_URL`、`TELEGRAM_LLM_MODEL`、`TELEGRAM_LLM_TIMEOUT_SECONDS`（以及
既有 `TELEGRAM_TIMEOUT_SECONDS`）。`TELEGRAM_WEBHOOK_URL` 是公開 HTTPS base URL，
CD 會補上 `/telegram/webhook`。

這是 demo 的 best-effort 邊界：Bot API send/edit/answer 失敗會安全記錄並仍回 webhook
200；若程序在 core 建單 commit 後、interaction confirmed 更新前崩潰，interaction
會停在 `confirming`，後續 callback 不會再次建單，但需人工清理或重新輸入。Telegram
本身重送 webhook 也由條件狀態轉移防止一般重複處理。
