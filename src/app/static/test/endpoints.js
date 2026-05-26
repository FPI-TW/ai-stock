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
  },

  // ---- symbols ---------------------------------------------------------
  {
    group: "symbols",
    method: "GET",
    path: "/symbols",
    implemented: true,
    label: "List tradable symbols",
    query: { q: "23", limit: 20 },
  },
  {
    group: "symbols",
    method: "GET",
    path: "/symbols/{symbol}",
    implemented: true,
    label: "Lookup symbol",
    pathParams: { symbol: "2330" },
  },

  // ---- trade-intents ---------------------------------------------------
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents",
    implemented: true,
    label: "Create intent",
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
    query: { status: "active", pageSize: 50 },
  },
  {
    group: "trade-intents",
    method: "GET",
    path: "/trade-intents/{intent_id}",
    implemented: true,
    label: "Get intent",
    pathParams: { intent_id: "<intent uuid>" },
  },
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents/{intent_id}/cancel",
    implemented: true,
    label: "Cancel intent",
    pathParams: { intent_id: "<intent uuid>" },
  },

  // ---- notifications ---------------------------------------------------
  {
    group: "notifications",
    method: "GET",
    path: "/notifications",
    implemented: true,
    label: "List notifications",
    query: { unreadOnly: false, pageSize: 50 },
  },
  {
    group: "notifications",
    method: "POST",
    path: "/notifications/{notification_id}/read",
    implemented: true,
    label: "Mark notification read",
    pathParams: { notification_id: "<notification uuid>" },
  },

  // ---- dev (LOCAL_MODE only) -------------------------------------------
  {
    group: "dev",
    method: "POST",
    path: "/dev/evaluate-quotes",
    implemented: true,
    label: "Force re-evaluate active intents",
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
