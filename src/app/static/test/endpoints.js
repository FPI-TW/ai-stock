// Endpoint catalog for the /test page.
//
// Single source of truth for what the UI lists. `implemented: true` entries
// are interactive; `implemented: false` entries are shown disabled and tagged
// with the work-order ticket that will deliver them. When a planned endpoint
// lands, flip its `implemented` flag in the same PR — that flip is the UI
// acceptance check for the work order.
//
// Keep examples small and copy-pasteable. They are pre-filled into the
// request body editor when the user clicks an endpoint or picks from the
// "Fill example" dropdown.

export const ENDPOINTS = [
  // ---- health ----------------------------------------------------------
  {
    group: "health",
    method: "GET",
    path: "/health",
    implemented: true,
    label: "Liveness + DB check",
    description: "確認 server 與 DB 連線狀態。回 service 名稱、版本、environment、database=ok/down。佈署後第一個該打的 endpoint。",
  },

  // ---- symbols ---------------------------------------------------------
  {
    group: "symbols",
    method: "GET",
    path: "/symbols",
    implemented: true,
    label: "List tradable symbols",
    description: "依代號 prefix 或中文名稱搜尋可交易標的；下單頁的 symbol picker 用這個。可帶 q (搜尋字串) 與 limit (最多 50 筆，預設 20)。",
    query: { q: "23", limit: 20 },
    queryParamSpecs: {
      q: { description: "代號或名稱關鍵字（prefix 比對）", example: "23" },
      limit: { description: "回傳筆數上限 (1-50)", example: 20, type: "number" },
    },
  },
  {
    group: "symbols",
    method: "GET",
    path: "/symbols/{symbol}",
    implemented: true,
    label: "Lookup symbol",
    description: "依代號精確查詢單一標的的市場、類型、可交易狀態。建立 intent 前可先用這個驗 symbol 存在。",
    pathParams: { symbol: "2330" },
    pathParamSpecs: {
      symbol: { description: "標的代號（精確）", example: "2330" },
    },
  },

  // ---- trade-intents ---------------------------------------------------
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents",
    implemented: true,
    label: "Create intent",
    description: "建立新的 trade intent。Pydantic discriminated union 依 strategy 自動 dispatch 對應 schema；盤中建立會在同 transaction 內嘗試 immediate trigger。owner 取自 X-Local-User-Id header (LOCAL_MODE)。",
    examples: {
      "buy_price_alert (台積電 @600)": {
        strategy: "buy_price_alert",
        symbol: "2330",
        quantityLots: 1,
        targetPrice: "600",
      },
      "sell_price_alert (台積電 @650)": {
        strategy: "sell_price_alert",
        symbol: "2330",
        quantityLots: 1,
        targetPrice: "650",
      },
      "limit_buy_order (台積電 @600)": {
        strategy: "limit_buy_order",
        symbol: "2330",
        quantityLots: 2,
        targetPrice: "600",
        transactionMode: "partial_fill_allowed",
      },
      "limit_sell_order (台積電 @650)": {
        strategy: "limit_sell_order",
        symbol: "2330",
        quantityLots: 1,
        targetPrice: "650",
      },
    },
  },
  {
    group: "trade-intents",
    method: "GET",
    path: "/trade-intents",
    implemented: true,
    label: "List intents (cursor pagination)",
    description: "列出當前使用者的 trade intents；keyset cursor pagination。可用 status 過濾（多值用逗號或重複 query），預設依 created_at desc 排序。",
    query: { status: "active", pageSize: 50 },
    queryParamSpecs: {
      status: { description: "過濾狀態（active / scheduled / triggered / cancelled，多值用逗號）", example: "active" },
      cursor: { description: "下一頁 cursor（intent UUID）", example: "" },
      pageSize: { description: "每頁筆數 (1-100，預設 50)", example: 50, type: "number" },
    },
  },
  {
    group: "trade-intents",
    method: "GET",
    path: "/trade-intents/{intent_id}",
    implemented: true,
    label: "Get intent",
    description: "依 ID 取得單一 intent 完整資料（含 transactionMode / filledQuantityLots / lastFillAt 等 V0.5-15 欄位）。Owner 不匹配回 404。",
    pathParams: { intent_id: "<intent uuid>" },
    pathParamSpecs: {
      intent_id: { description: "Intent UUID", example: "<貼上 intent uuid>" },
    },
  },
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents/{intent_id}/cancel",
    implemented: true,
    label: "Cancel intent",
    description: "取消 active / scheduled intent；triggered 與 cancelled 狀態回 409 CANCEL_NOT_ALLOWED。並行 best-effort 釋放 quote provider 訂閱。",
    pathParams: { intent_id: "<intent uuid>" },
    pathParamSpecs: {
      intent_id: { description: "Intent UUID", example: "<貼上 intent uuid>" },
    },
  },

  // ---- notifications ---------------------------------------------------
  {
    group: "notifications",
    method: "GET",
    path: "/notifications",
    implemented: true,
    label: "List notifications",
    description: "列出當前使用者的通知；trigger transaction 寫入後立即可見。unreadOnly=true 只看未讀；keyset cursor pagination。",
    query: { unreadOnly: false, pageSize: 50 },
    queryParamSpecs: {
      unreadOnly: { description: "只看未讀 (true/false)", example: "false", options: ["true", "false"] },
      cursor: { description: "下一頁 cursor（notification UUID）", example: "" },
      pageSize: { description: "每頁筆數 (1-100，預設 50)", example: 50, type: "number" },
    },
  },
  {
    group: "notifications",
    method: "POST",
    path: "/notifications/{notification_id}/read",
    implemented: true,
    label: "Mark notification read",
    description: "標通知為已讀；冪等（重複呼叫不變 read_at）。Owner 不匹配回 404，不洩漏存在性。",
    pathParams: { notification_id: "<notification uuid>" },
    pathParamSpecs: {
      notification_id: { description: "Notification UUID", example: "<貼上 notification uuid>" },
    },
  },

  // ---- dev (LOCAL_MODE only) -------------------------------------------
  {
    group: "dev",
    method: "POST",
    path: "/dev/evaluate-quotes",
    implemented: true,
    label: "Force re-evaluate active intents",
    description: "強制對既有 in-memory snapshot 重跑 evaluator → trigger。一般 demo 用 /dev/push-quote 即可；這個 endpoint 留給「push 完之後想再評估一次」的 corner case。",
    examples: {
      "all active symbols": {},
      "single symbol": { symbols: ["2330"] },
    },
  },
  {
    group: "dev",
    method: "POST",
    path: "/dev/push-quote",
    implemented: true,
    label: "Push synthetic quote → listeners fire",
    description: "對 InMemoryQuoteProvider 推合成 quote，listener (QuoteEvaluationDispatcher) 立即執行 evaluator + trigger。end-to-end demo 的核心。shioaji_demo provider 回 400。",
    examples: {
      "2330 ask=599 (triggers buy@600)": { symbol: "2330", askPrice: "599" },
      "2330 bid=651 (triggers sell@650)": { symbol: "2330", bidPrice: "651" },
      "2330 last_fallback @598": { symbol: "2330", lastPrice: "598" },
    },
  },
  {
    group: "dev",
    method: "POST",
    path: "/dev/set-clock",
    implemented: true,
    label: "Freeze / advance / reset trading clock",
    description: "凍結 / 推進 / 重置 process-global TradingSessionService 時鐘。{} 重設回 system clock；advanceSeconds 從目前時鐘推進；單 worker LOCAL_MODE 用。",
    examples: {
      "freeze at 盤中 09:30": { fakeNow: "2026-05-11T09:30:00+08:00" },
      "freeze at 盤外 14:30": { fakeNow: "2026-05-11T14:30:00+08:00" },
      "advance 60s": { advanceSeconds: 60 },
      "reset to system": {},
    },
  },
  {
    group: "dev",
    method: "GET",
    path: "/dev/server-state",
    implemented: true,
    label: "Snapshot: user / clock / provider / pid",
    description: "回 server 端目前狀態：app_env、local_mode、quote provider type、current_user (header or default)、clock state、worker pid、active subscriptions。UI top banner 用這個。",
  },

  // ---- planned: V0.5-14 batch -----------------------------------------
  {
    group: "trade-intents (planned)",
    method: "POST",
    path: "/trade-intents/batch",
    implemented: false,
    ticket: "BE-V0.5-14",
    label: "Batch create (≤20 rows)",
  },

  // ---- planned: V1-01 auth --------------------------------------------
  {
    group: "auth (planned)",
    method: "POST",
    path: "/auth/login",
    implemented: false,
    ticket: "BE-V1-01",
    label: "Login (replaces LOCAL_USER_ID)",
  },
  {
    group: "auth (planned)",
    method: "POST",
    path: "/auth/logout",
    implemented: false,
    ticket: "BE-V1-01",
    label: "Logout",
  },
  {
    group: "auth (planned)",
    method: "POST",
    path: "/auth/refresh",
    implemented: false,
    ticket: "BE-V1-01",
    label: "Refresh session",
  },
  {
    group: "auth (planned)",
    method: "POST",
    path: "/auth/password-reset/request",
    implemented: false,
    ticket: "BE-V1-01",
    label: "Request password reset",
  },
  {
    group: "auth (planned)",
    method: "POST",
    path: "/auth/password-reset/confirm",
    implemented: false,
    ticket: "BE-V1-01",
    label: "Confirm password reset",
  },

  // ---- planned: V1-10 CSV ---------------------------------------------
  {
    group: "trade-intents (planned)",
    method: "POST",
    path: "/trade-intents/csv/preview",
    implemented: false,
    ticket: "BE-V1-10",
    label: "Preview CSV upload (replaces batch)",
  },
  {
    group: "trade-intents (planned)",
    method: "POST",
    path: "/trade-intents/csv/confirm",
    implemented: false,
    ticket: "BE-V1-10",
    label: "Confirm CSV upload",
  },

  // ---- planned: V1-11/12 Telegram + notification settings -------------
  {
    group: "user (planned)",
    method: "POST",
    path: "/auth/telegram/bind",
    implemented: false,
    ticket: "BE-V1-11",
    label: "Bind Telegram",
  },
  {
    group: "user (planned)",
    method: "POST",
    path: "/auth/telegram/unbind",
    implemented: false,
    ticket: "BE-V1-11",
    label: "Unbind Telegram",
  },
  {
    group: "user (planned)",
    method: "GET",
    path: "/user/notification-settings",
    implemented: false,
    ticket: "BE-V1-12",
    label: "Read notification settings",
  },
  {
    group: "user (planned)",
    method: "PUT",
    path: "/user/notification-settings",
    implemented: false,
    ticket: "BE-V1-12",
    label: "Update notification settings",
  },

  // ---- planned: V1-13/14/15 admin -------------------------------------
  {
    group: "admin (planned)",
    method: "GET",
    path: "/admin/users",
    implemented: false,
    ticket: "BE-V1-13",
    label: "List users",
  },
  {
    group: "admin (planned)",
    method: "POST",
    path: "/admin/users",
    implemented: false,
    ticket: "BE-V1-13",
    label: "Create user",
  },
  {
    group: "admin (planned)",
    method: "POST",
    path: "/admin/users/{id}/invitations",
    implemented: false,
    ticket: "BE-V1-13",
    label: "Send invitation",
  },
  {
    group: "admin (planned)",
    method: "POST",
    path: "/admin/users/{id}/disable",
    implemented: false,
    ticket: "BE-V1-13",
    label: "Disable user",
  },
  {
    group: "admin (planned)",
    method: "POST",
    path: "/admin/data-overrides/symbols",
    implemented: false,
    ticket: "BE-V1-14",
    label: "Override symbol metadata",
  },
  {
    group: "admin (planned)",
    method: "GET",
    path: "/admin/monitoring/alerts",
    implemented: false,
    ticket: "BE-V1-15",
    label: "Alert backlog",
  },
  {
    group: "admin (planned)",
    method: "GET",
    path: "/admin/monitoring/metrics",
    implemented: false,
    ticket: "BE-V1-15",
    label: "Runtime metrics",
  },
];
