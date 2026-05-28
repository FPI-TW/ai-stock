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
    label: "健康檢查",
    description: "確認後端服務是否正常運作，包含資料庫連線。服務啟動後第一個該檢查的端點。",
  },

  // ---- symbols ---------------------------------------------------------
  {
    group: "symbols",
    method: "GET",
    path: "/symbols",
    implemented: true,
    label: "搜尋股票標的",
    description: "依代號或中文名稱（前綴比對）搜尋可交易的股票。下單前用來找標的、確認代號正不正確。",
    query: { q: "23", limit: 20 },
    queryParamSpecs: {
      q: { description: "輸入股票代號或中文名稱（例如 2330 或 台積電）", example: "23" },
      limit: { description: "最多回傳幾筆（1～50，預設 20）", example: 20, type: "number" },
    },
  },
  {
    group: "symbols",
    method: "GET",
    path: "/symbols/{symbol}",
    implemented: true,
    label: "查詢單一標的",
    description: "用代號查單一股票的詳細資料（市場、類型、是否可交易）。建立委託前可先確認標的存在。",
    pathParams: { symbol: "2330" },
    pathParamSpecs: {
      symbol: { description: "完整股票代號（例如 2330）", example: "2330" },
    },
  },

  // ---- trade-intents ---------------------------------------------------
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents",
    implemented: true,
    label: "建立委託",
    description: "建立新的買賣委託。支援四種策略：買進到價提醒 / 賣出到價提醒 / 限價買單 / 限價賣單。盤中建立後若行情已達標，會立即觸發並產生通知。",
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
    label: "我的委託列表",
    description: "查看目前使用者的所有委託，可依狀態過濾、可分頁。預設依建立時間由新到舊排序。",
    query: { status: "active", pageSize: 50 },
    queryParamSpecs: {
      status: { description: "只看特定狀態：active / scheduled / triggered / cancelled（多選用逗號分隔）", example: "active" },
      cursor: { description: "下一頁的起點（貼上前一次回應中的 nextCursor）", example: "" },
      pageSize: { description: "每頁顯示幾筆（最多 100，預設 50）", example: 50, type: "number" },
    },
  },
  {
    group: "trade-intents",
    method: "GET",
    path: "/trade-intents/{intent_id}",
    implemented: true,
    label: "委託詳情",
    description: "用委託 ID 查單一委託的完整資料（狀態、目標價、張數、成交資訊、各種時間戳）。",
    pathParams: { intent_id: "<intent uuid>" },
    pathParamSpecs: {
      intent_id: { description: "委託 ID（從列表頁複製）", example: "<貼上委託 ID>" },
    },
  },
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents/{intent_id}/cancel",
    implemented: true,
    label: "取消委託",
    description: "取消委託。只能取消「進行中」或「已排程」的委託；已觸發或已取消的委託不能再取消。",
    pathParams: { intent_id: "<intent uuid>" },
    pathParamSpecs: {
      intent_id: { description: "委託 ID（從列表頁複製）", example: "<貼上委託 ID>" },
    },
  },
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents/twap/preview",
    implemented: true,
    label: "TWAP 預覽",
    description: "預覽 TWAP（時間加權平均價格）拆單計畫：依目標數量、結束時間、間隔秒數，計算切片數、起始/結束時間與每筆 slice。不會落地。",
    examples: {
      "台積電多單 5 張 / 60 秒 / 13:25": {
        symbol: "2330",
        positionSide: "long",
        quantityLots: 5,
        intervalSeconds: 60,
        endTime: "13:25",
      },
      "台積電空單 3 張 / 30 秒 / 13:25": {
        symbol: "2330",
        positionSide: "short",
        quantityLots: 3,
        intervalSeconds: 30,
        endTime: "13:25",
      },
    },
  },
  {
    group: "trade-intents",
    method: "POST",
    path: "/trade-intents/twap/confirm",
    implemented: true,
    label: "TWAP 確認下單",
    description: "確認並落地 TWAP 計畫：建立 twap_order intent 與所有 slices；用同樣的 payload 先 preview 再 confirm 即可。",
    examples: {
      "台積電多單 5 張 / 60 秒 / 13:25": {
        symbol: "2330",
        positionSide: "long",
        quantityLots: 5,
        intervalSeconds: 60,
        endTime: "13:25",
      },
    },
  },

  // ---- notifications ---------------------------------------------------
  {
    group: "notifications",
    method: "GET",
    path: "/notifications",
    implemented: true,
    label: "通知列表",
    description: "查看通知列表，可只看未讀。通知會在委託被觸發時自動產生。",
    query: { unreadOnly: false, pageSize: 50 },
    queryParamSpecs: {
      unreadOnly: { description: "只看未讀通知（true / false）", example: "false", options: ["true", "false"] },
      cursor: { description: "下一頁的起點（貼上前一次回應中的 nextCursor）", example: "" },
      pageSize: { description: "每頁顯示幾筆（最多 100，預設 50）", example: 50, type: "number" },
    },
  },
  {
    group: "notifications",
    method: "POST",
    path: "/notifications/{notification_id}/read",
    implemented: true,
    label: "標記已讀",
    description: "把通知標記為已讀。重複呼叫不會更動已讀時間（多按沒事）。",
    pathParams: { notification_id: "<notification uuid>" },
    pathParamSpecs: {
      notification_id: { description: "通知 ID（從通知列表複製）", example: "<貼上通知 ID>" },
    },
  },

  // ---- quotes ----------------------------------------------------------
  {
    group: "quotes",
    method: "GET",
    path: "/quotes",
    implemented: true,
    label: "批次查詢即時報價",
    description: "傳入逗號分隔的股票代號，批次回傳最新行情快照（ask / bid / last）。快取未命中的標的回傳 stale=true 而不中斷整批。最多 50 個標的。",
    query: { symbols: "2330,2317" },
    queryParamSpecs: {
      symbols: { description: "逗號分隔的股票代號（例如 2330,2317），最多 50 個", example: "2330,2317" },
    },
  },
  {
    group: "quotes",
    method: "GET",
    path: "/quotes/current-price/{symbol}",
    implemented: true,
    label: "查詢單一即時價（測試用）",
    description: "向 broker demo provider 即時查單一標的的最新報價，僅允許白名單標的（2330 / 2317 / 0050 / 00878）。此 API 只在 QUOTE_PROVIDER=shioaji_demo 時可用，未來 V1 會改接正式行情。",
    pathParams: { symbol: "2330" },
    pathParamSpecs: {
      symbol: { description: "白名單股票代號（2330 / 2317 / 0050 / 00878）", example: "2330" },
    },
  },

  // ---- dev (LOCAL_MODE only) -------------------------------------------
  {
    group: "dev",
    method: "POST",
    path: "/dev/evaluate-quotes",
    implemented: true,
    label: "重新評估委託（Dev）",
    description: "對已存在的行情快照，重新跑一次評估，看看委託是否該觸發。一般 demo 用「推送假行情」即可；這個適合「推送過後再評估一次」的情況。",
    examples: {
      "全部進行中標的": {},
      "只評估單一標的": { symbols: ["2330"] },
    },
  },
  {
    group: "dev",
    method: "POST",
    path: "/dev/push-quote",
    implemented: true,
    label: "推送假行情（Dev）",
    description: "送一筆假的行情報價到系統，模擬市場有報價進來。若有符合條件的委託會立即觸發並產生通知。Demo 端到端流程的核心動作。",
    examples: {
      "2330 ask=599（觸發 600 買單）": { symbol: "2330", askPrice: "599" },
      "2330 bid=651（觸發 650 賣單）": { symbol: "2330", bidPrice: "651" },
      "2330 只有成交價 598": { symbol: "2330", lastPrice: "598" },
    },
  },
  {
    group: "dev",
    method: "POST",
    path: "/dev/set-clock",
    implemented: true,
    label: "控制系統時鐘（Dev）",
    description: "凍結 / 推進 / 重置系統時鐘。用來測試「盤中」「盤外」「週末」等不同情況下委託的行為。",
    examples: {
      "凍結在盤中 09:30": { fakeNow: "2026-05-11T09:30:00+08:00" },
      "凍結在盤外 14:30": { fakeNow: "2026-05-11T14:30:00+08:00" },
      "把時鐘向前推 60 秒": { advanceSeconds: 60 },
      "回到實際時間": {},
    },
  },
  {
    group: "dev",
    method: "GET",
    path: "/dev/server-state",
    implemented: true,
    label: "Server 狀態（Dev）",
    description: "查看目前 server 狀態：環境模式、目前使用者、時鐘狀態、行情來源、訂閱中的標的。除錯與確認模式設定用。",
  },
  {
    group: "dev",
    method: "POST",
    path: "/dev/twap/process-due-slices",
    implemented: true,
    label: "TWAP 處理到期切片（Dev）",
    description: "手動觸發 TWAP worker：把已到時的 slices 推進到下一狀態。正式環境會有定時 worker，本地 demo 用此 API 模擬。回傳 processedCount。",
    examples: { "全部到期切片": {} },
  },
  {
    group: "dev",
    method: "POST",
    path: "/dev/twap/process-price-followups",
    implemented: true,
    label: "TWAP 處理價格 follow-up（Dev）",
    description: "手動觸發 TWAP worker 的價格追蹤流程：對未成交切片依當下行情判斷是否成交或續延。回傳 processedCount。",
    examples: { "全部追蹤": {} },
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
