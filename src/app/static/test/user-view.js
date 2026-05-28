// ai-stock /test — 使用者視角模式
//
// Dashboard + 下單 sheet flow，呼真實 V0.5 API。底層 user 切換沿用
// app.js 的 X-Local-User-Id header 機制。
//
// 匯出：
//   initUserView()       — 綁定全 view DOM event
//   onEnterUserMode()    — 切到使用者模式時刷新 dashboard
//   showToast(msg, type) — 也讓 app.js 可呼叫

import {
  USERS,
  getSelectedUser,
  setSelectedUser,
  sendRequest,
  attachSegmentedIndicator,
  attachPopover,
  renderIdentityMenu,
  syncDevUserSwitcher,
} from "/test-assets/app.js";
import { mountQuoteBoard, unmountQuoteBoard, addToWatchlist, setAddSymbolHandler } from "./quote-board.js";

// ---------------------------------------------------------------------------
// Strategy 中文 label — 與 notification_template.py 同步
// ---------------------------------------------------------------------------

const STRATEGY_LABEL = {
  buy_price_alert: "買進到價提醒",
  sell_price_alert: "賣出到價提醒",
  limit_buy_order: "限價買單",
  limit_sell_order: "限價賣單",
};

const STRATEGY_TRIGGER_DESC = {
  buy_price_alert: "ask ≤ 目標價時通知",
  sell_price_alert: "bid ≥ 目標價時通知",
  limit_buy_order: "ask ≤ 目標價時觸發",
  limit_sell_order: "bid ≥ 目標價時觸發",
};

const STATUS_LABEL = {
  active: "進行中",
  scheduled: "已排程",
  triggered: "已觸發",
  cancelled: "已取消",
};

// ---------------------------------------------------------------------------
// Sheet state
// ---------------------------------------------------------------------------

const sheetState = {
  step: 1,
  symbol: null, // { symbol, displayName }
  strategy: null,
  quantityLots: 1,
  targetPrice: "",
  transactionMode: "single_notification",
};

let searchAbortController = null;
let searchDebounceTimer = null;

let lastIntentsList = []; // cached for client-side filter
let intentFilter = "all";  // all | active | triggered | cancelled

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtRelativeTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const diffSec = Math.round((Date.now() - date.getTime()) / 1000);
  if (diffSec < 60) return "剛剛";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分鐘前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小時前`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)} 天前`;
  return date.toLocaleDateString("zh-TW", { month: "short", day: "numeric" });
}

function fmtUserLabel(label) {
  if (label === "default") return "預設使用者";
  // Match the title-cased form used by USER_DISPLAY_NAME in app.js so the
  // topbar pill, dashboard greeting, and confirm-summary footer all agree.
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function currentUserName() {
  const label = getSelectedUser();
  return fmtUserLabel(label);
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "className") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "onClick") node.addEventListener("click", v);
    else if (v === false || v === null || v === undefined) continue;
    else node.setAttribute(k, String(v));
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

// ---------------------------------------------------------------------------
// Dashboard render
// ---------------------------------------------------------------------------

async function refreshDashboard() {
  document.getElementById("user-greeting").textContent = currentUserName();
  await Promise.all([refreshIntents(), refreshNotifications()]);
}

// ---------------------------------------------------------------------------
// Dashboard auto-refresh
// ---------------------------------------------------------------------------
// 服務端推送行情觸發 intent 後，user mode 的 intent 狀態與通知計數會變。
// 不輪詢就只能仰賴使用者手動按 refresh 或切換模式來抓新狀態。
// 5 秒一次足夠（變化頻率遠低於行情），分頁不在前景時暫停以省流量。

const DASHBOARD_POLL_MS = 5000;
const STALE_DEBOUNCE_MS = 250;
let dashboardPollTimer = null;
let dashboardVisibilityBound = false;
let dashboardStaleChannel = null;
let staleDebounceTimer = null;

function startDashboardPoll() {
  stopDashboardPoll();
  dashboardPollTimer = setInterval(() => {
    if (document.visibilityState === "hidden") return;
    void refreshDashboard();
  }, DASHBOARD_POLL_MS);
  if (!dashboardVisibilityBound) {
    dashboardVisibilityBound = true;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && dashboardPollTimer) {
        void refreshDashboard();
      }
    });
  }
  // 即時跨分頁更新：服務端推送行情 / 改時鐘 / 重評估 完成後會 broadcast，
  // 同瀏覽器的 user mode 分頁立即重抓 dashboard，不必等下一個 5 秒 tick。
  // Debounce 250ms 處理連續推送（避免 10 連推就 10 連 refresh）。
  try {
    dashboardStaleChannel = new BroadcastChannel("ai-stock-test:dashboard");
    dashboardStaleChannel.onmessage = (ev) => {
      if (ev.data?.type !== "stale") return;
      clearTimeout(staleDebounceTimer);
      staleDebounceTimer = setTimeout(() => void refreshDashboard(), STALE_DEBOUNCE_MS);
    };
  } catch (_) {
    // BroadcastChannel unavailable — polling fallback still works.
  }
}

function stopDashboardPoll() {
  if (dashboardPollTimer) {
    clearInterval(dashboardPollTimer);
    dashboardPollTimer = null;
  }
  if (dashboardStaleChannel) {
    dashboardStaleChannel.close();
    dashboardStaleChannel = null;
  }
  clearTimeout(staleDebounceTimer);
}

async function refreshIntents() {
  const list = document.getElementById("uv-intent-list");
  list.innerHTML = "";
  list.appendChild(el("div", { className: "uv-loading" }, ["載入中…"]));

  const resp = await sendRequest({
    method: "GET",
    path: "/trade-intents",
    query: { pageSize: 100 },
  });

  if (resp.status !== 200) {
    list.innerHTML = "";
    list.appendChild(emptyState("", "讀取委託失敗", resp.body?.error?.message || `HTTP ${resp.status}`));
    document.getElementById("uv-intent-count").textContent = "—";
    return;
  }

  lastIntentsList = resp.body?.data || [];
  renderIntentsFiltered();
}

function renderIntentsFiltered() {
  const list = document.getElementById("uv-intent-list");
  list.innerHTML = "";

  const active = lastIntentsList.filter((i) => i.status === "active" || i.status === "scheduled");
  const triggered = lastIntentsList.filter((i) => i.status === "triggered");
  const cancelled = lastIntentsList.filter((i) => i.status === "cancelled");

  document.getElementById("uv-intent-count").textContent =
    `${active.length} 進行 · ${triggered.length} 已觸發 · ${cancelled.length} 已取消`;

  let visible;
  if (intentFilter === "active") visible = active;
  else if (intentFilter === "triggered") visible = triggered;
  else if (intentFilter === "cancelled") visible = cancelled;
  else visible = [...active, ...triggered, ...cancelled];

  if (lastIntentsList.length === 0) {
    list.appendChild(emptyState("", "尚無委託", "點右上『+ 新增委託』開始建立"));
    return;
  }
  if (visible.length === 0) {
    const filterLabel = { active: "進行中", triggered: "已觸發", cancelled: "已取消" }[intentFilter];
    list.appendChild(emptyState("", `沒有${filterLabel}的委託`, "切其他分類試試"));
    return;
  }

  for (const intent of visible) {
    list.appendChild(renderIntentCard(intent, { actionable: intent.status === "active" || intent.status === "scheduled" }));
  }
}

function renderIntentCard(intent, { actionable }) {
  const strategy = STRATEGY_LABEL[intent.strategy] || intent.strategy;
  const triggerDesc = STRATEGY_TRIGGER_DESC[intent.strategy] || "";
  const status = intent.status;

  const filledRow =
    intent.filledQuantityLots > 0
      ? el("div", { className: "intent-card-meta" }, [
          el("span", {}, [
            el("span", { className: "label" }, ["成交"]),
            `${intent.filledQuantityLots} / ${intent.quantityLots} 張`,
          ]),
        ])
      : null;

  const card = el("article", { className: "intent-card", role: "button", tabindex: "0" }, [
    el("div", { className: "intent-card-row" }, [
      el("div", { className: "intent-card-symbol" }, [intent.symbol]),
      el("div", { className: "intent-card-strategy" }, [strategy]),
      el("span", { className: `intent-card-status ${status}` }, [STATUS_LABEL[status] || status]),
    ]),
    el("div", { className: "intent-card-meta" }, [
      el("span", {}, [el("span", { className: "label" }, ["目標"]), intent.targetPriceEffective]),
      el("span", {}, [el("span", { className: "label" }, ["張數"]), `${intent.quantityLots}`]),
      el("span", { className: "intent-card-strategy", style: "font-size:11px" }, [triggerDesc]),
    ]),
    filledRow,
    el("div", { className: "intent-card-footer" }, [
      el("span", {}, [`建立於 ${fmtRelativeTime(intent.createdAt)}`]),
      actionable
        ? el("button", {
            className: "intent-card-cancel",
            onClick: (ev) => {
              ev.stopPropagation();
              cancelIntent(intent.id, intent.symbol);
            },
          }, ["取消委託"])
        : el("span", {}, [intent.cancelledAt ? `取消於 ${fmtRelativeTime(intent.cancelledAt)}` : ""]),
    ]),
  ]);

  card.style.cursor = "pointer";
  card.addEventListener("click", () => openIntentDetail(intent));
  card.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openIntentDetail(intent);
    }
  });
  return card;
}

async function refreshNotifications() {
  const list = document.getElementById("uv-notif-list");
  list.innerHTML = "";
  list.appendChild(el("div", { className: "uv-loading" }, ["載入中…"]));

  const resp = await sendRequest({
    method: "GET",
    path: "/notifications",
    query: { pageSize: 10 },
  });

  list.innerHTML = "";
  if (resp.status !== 200) {
    list.appendChild(emptyState("", "讀取通知失敗", resp.body?.error?.message || `HTTP ${resp.status}`));
    document.getElementById("uv-notif-count").textContent = "—";
    return;
  }

  const notifs = resp.body?.data || [];
  const unread = notifs.filter((n) => !n.readAt).length;
  renderNotifCount(unread, notifs.length);

  if (notifs.length === 0) {
    list.appendChild(emptyState("", "尚無通知", "委託觸發後會在這裡顯示"));
    return;
  }

  for (const notif of notifs) list.appendChild(renderNotifCard(notif));
}

function renderNotifCount(unread, total) {
  const countEl = document.getElementById("uv-notif-count");
  countEl.innerHTML = "";
  if (total === 0) {
    countEl.textContent = "—";
    return;
  }
  const badgeClass = unread > 0 ? "unread" : "all-read";
  const badgeText = unread > 0 ? `${unread} 未讀` : "全部已讀";
  countEl.appendChild(el("span", { className: `uv-count-badge ${badgeClass}` }, [badgeText]));
  countEl.appendChild(el("span", { className: "uv-count-total" }, [`/ ${total} 筆`]));
}

function renderNotifCard(notif) {
  const isLimit = notif.type === "limit_order_triggered";
  const iconLabel = isLimit ? "限" : "到";
  const summary = (notif.renderedBody || "")
    .split("\n")
    .filter((line) => line.trim() && !line.startsWith("僅通知"))
    .slice(0, 3)
    .join(" · ");

  const card = el("article", { className: `notif-card ${notif.readAt ? "" : "unread"}`, role: "button", tabindex: "0" }, [
    el("div", { className: `notif-icon ${isLimit ? "limit" : "alert"}` }, [iconLabel]),
    el("div", { className: "notif-body" }, [
      el("div", { className: "notif-title" }, [notif.renderedTitle]),
      el("div", { className: "notif-summary" }, [summary]),
    ]),
    el("div", { className: "notif-time" }, [fmtRelativeTime(notif.createdAt)]),
  ]);
  card.style.cursor = "pointer";
  card.addEventListener("click", () => openNotifDetail(notif));
  card.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      openNotifDetail(notif);
    }
  });
  return card;
}

function emptyState(_icon, title, hint) {
  return el("div", { className: "uv-empty" }, [
    el("div", { className: "uv-empty-title" }, [title]),
    el("div", { className: "uv-empty-hint" }, [hint]),
  ]);
}

// ---------------------------------------------------------------------------
// Intent actions
// ---------------------------------------------------------------------------

async function cancelIntent(intentId, symbol) {
  const ok = await customConfirm({
    title: "取消委託",
    message: `確定要取消 ${symbol} 的委託嗎？取消後無法復原。`,
    okText: "取消委託",
    destructive: true,
  });
  if (!ok) return;

  const resp = await sendRequest({
    method: "POST",
    path: `/trade-intents/${intentId}/cancel`,
  });
  if (resp.status === 200) {
    showToast("已取消委託", "success");
    closeDetail();
    refreshIntents();
  } else {
    showToast(resp.body?.error?.message || `取消失敗 (${resp.status})`, "error");
  }
}

async function markNotifRead(notifId) {
  const resp = await sendRequest({
    method: "POST",
    path: `/notifications/${notifId}/read`,
  });
  if (resp.status === 200) refreshNotifications();
  else showToast(resp.body?.error?.message || `標已讀失敗 (${resp.status})`, "error");
}

// ---------------------------------------------------------------------------
// Sheet — 下單 flow
// ---------------------------------------------------------------------------

function resetSheet() {
  sheetState.step = 1;
  sheetState.symbol = null;
  sheetState.strategy = null;
  sheetState.quantityLots = 1;
  sheetState.targetPrice = "";
  sheetState.transactionMode = "single_notification";

  document.getElementById("symbol-search").value = "";
  document.getElementById("symbol-selected").hidden = true;
  showSymbolResultsHint();
  document.getElementById("form-quantity").value = "1";
  document.getElementById("form-target").value = "";
  for (const r of document.querySelectorAll('input[name="strategy"]')) r.checked = false;
  document.querySelector('input[name="transactionMode"][value="single_notification"]').checked = true;
  document.getElementById("form-transaction-group").hidden = true;
  showSheetStep(1);
}

function showSheetStep(step) {
  sheetState.step = step;
  for (const node of document.querySelectorAll(".sheet-step")) {
    node.hidden = Number(node.dataset.step) !== step;
  }
  for (const dot of document.querySelectorAll("#sheet-stepper .step-dot")) {
    const dotStep = Number(dot.dataset.step);
    dot.classList.toggle("active", dotStep === step);
    dot.classList.toggle("completed", dotStep < step);
  }
  // single left action per step: step 1 shows 取消, step 2+ shows ← 上一步
  document.getElementById("sheet-back").hidden = step === 1;
  document.getElementById("sheet-cancel").hidden = step !== 1;
  document.getElementById("sheet-title").textContent = step === 3 ? "確認送出" : "新增委託";
  const next = document.getElementById("sheet-next");
  next.textContent = step === 3 ? "送出" : "下一步";
  next.disabled = !validateStep(step);
}

function validateStep(step) {
  if (step === 1) return sheetState.symbol !== null;
  if (step === 2) {
    if (!sheetState.strategy) return false;
    if (!sheetState.targetPrice || Number(sheetState.targetPrice) <= 0) return false;
    if (!Number.isInteger(Number(sheetState.quantityLots)) || Number(sheetState.quantityLots) <= 0) return false;
    return true;
  }
  return true;
}

function openSheet() {
  resetSheet();
  document.getElementById("sheet-overlay").hidden = false;
  setTimeout(() => document.getElementById("symbol-search").focus(), 50);
}

function closeSheet() {
  document.getElementById("sheet-overlay").hidden = true;
}

function escapeHtmlLocal(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function highlightText(text, needle) {
  const safe = escapeHtmlLocal(text);
  if (!needle) return safe;
  const idx = text.toLowerCase().indexOf(needle.toLowerCase());
  if (idx < 0) return safe;
  const end = idx + needle.length;
  return (
    escapeHtmlLocal(text.slice(0, idx)) +
    `<mark class="symbol-match">${escapeHtmlLocal(text.slice(idx, end))}</mark>` +
    escapeHtmlLocal(text.slice(end))
  );
}

function showSymbolResultsHint() {
  const results = document.getElementById("symbol-results");
  results.classList.remove("hidden");
  results.innerHTML = `
    <div class="symbol-results-state">
      <div class="symbol-results-state-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16.5" y2="16.5"/>
        </svg>
      </div>
      <div class="symbol-results-state-title">輸入代號或名稱開始搜尋</div>
      <div class="symbol-results-state-hint">例如：2330、台積電</div>
    </div>
  `;
}

async function searchSymbols(query) {
  const results = document.getElementById("symbol-results");
  results.classList.remove("hidden");

  if (!query || query.length < 1) {
    showSymbolResultsHint();
    return;
  }

  if (searchAbortController) searchAbortController.abort();
  searchAbortController = new AbortController();
  const signal = searchAbortController.signal;

  const resp = await sendRequest({
    method: "GET",
    path: "/symbols",
    query: { q: query, limit: 10 },
    signal,
  }).catch((err) => {
    if (err?.name === "AbortError") return "aborted";
    return null;
  });

  if (resp === "aborted") return;
  if (!resp || resp.status !== 200) {
    results.innerHTML = `
      <div class="symbol-results-state">
        <div class="symbol-results-state-title">搜尋失敗</div>
        <div class="symbol-results-state-hint">稍候再試一次</div>
      </div>
    `;
    return;
  }

  const items = resp.body?.data || [];
  if (items.length === 0) {
    results.innerHTML = `
      <div class="symbol-results-state">
        <div class="symbol-results-state-title">找不到「${escapeHtmlLocal(query)}」</div>
        <div class="symbol-results-state-hint">試試其他代號或中文名稱</div>
      </div>
    `;
    return;
  }

  results.innerHTML = "";
  for (const sym of items) {
    const code = sym.symbol;
    const name = sym.displayName || code;
    const initial = code.charAt(0).toUpperCase();
    const item = document.createElement("div");
    item.className = "symbol-result-item";
    item.innerHTML = `
      <span class="symbol-result-avatar">${escapeHtmlLocal(initial)}</span>
      <div class="symbol-result-text">
        <span class="symbol-result-code">${highlightText(code, query)}</span>
        <span class="symbol-result-name">${highlightText(name, query)}</span>
      </div>
      ${sym.market ? `<span class="symbol-result-market">${escapeHtmlLocal(sym.market)}</span>` : ""}
    `;
    item.addEventListener("click", () => selectSymbol(sym));
    results.appendChild(item);
  }
}

function selectSymbol(sym) {
  sheetState.symbol = { symbol: sym.symbol, displayName: sym.displayName || sym.symbol };
  const results = document.getElementById("symbol-results");
  results.innerHTML = "";
  results.classList.add("hidden");
  document.getElementById("symbol-search").value = "";
  const selected = document.getElementById("symbol-selected");
  const code = sym.symbol;
  const name = sym.displayName || sym.symbol;
  const initial = code.charAt(0).toUpperCase();
  selected.innerHTML = "";
  selected.appendChild(el("span", { className: "symbol-selected-avatar" }, [initial]));
  selected.appendChild(el("div", { className: "symbol-selected-text" }, [
    el("span", { className: "symbol-selected-code" }, [code]),
    el("span", { className: "symbol-selected-name" }, [name]),
  ]));
  const changeBtn = el("button", { className: "symbol-selected-change", type: "button" }, ["更換"]);
  changeBtn.addEventListener("click", () => {
    sheetState.symbol = null;
    selected.hidden = true;
    document.getElementById("sheet-next").disabled = true;
    showSymbolResultsHint();
    setTimeout(() => document.getElementById("symbol-search").focus(), 0);
  });
  selected.appendChild(changeBtn);
  selected.hidden = false;
  showSheetStep(1); // refresh next button state
}

function onStrategyChange(value) {
  sheetState.strategy = value;
  const group = document.getElementById("form-transaction-group");
  group.hidden = !value?.startsWith("limit_");
  document.getElementById("sheet-next").disabled = !validateStep(2);
}

function renderConfirmSummary() {
  const container = document.getElementById("confirm-summary");
  container.innerHTML = "";

  const strategy = sheetState.strategy;
  const isBuy = strategy.startsWith("buy_") || strategy === "limit_buy_order";
  const isLimit = strategy.startsWith("limit_");
  const code = sheetState.symbol.symbol;
  const name = sheetState.symbol.displayName || code;
  const initial = code.charAt(0).toUpperCase();

  const header = el("div", { className: "confirm-card-header" }, [
    el("span", { className: `confirm-card-avatar ${isBuy ? "buy" : "sell"}` }, [initial]),
    el("div", { className: "confirm-card-symbol" }, [
      el("span", { className: "code" }, [code]),
      el("span", { className: "name" }, [name]),
    ]),
    el("span", { className: `confirm-card-strategy ${isBuy ? "buy" : "sell"}` }, [STRATEGY_LABEL[strategy]]),
  ]);

  const trigger = el("div", { className: "confirm-card-trigger" }, [
    STRATEGY_TRIGGER_DESC[strategy].replace("目標價", `${sheetState.targetPrice}`),
  ]);

  const grid = el("div", { className: "confirm-card-grid" }, [
    el("div", { className: "confirm-stat" }, [
      el("span", { className: "label" }, ["目標價"]),
      el("span", { className: "value" }, [sheetState.targetPrice]),
    ]),
    el("div", { className: "confirm-stat" }, [
      el("span", { className: "label" }, ["張數"]),
      el("span", { className: "value" }, [
        `${sheetState.quantityLots}`,
        el("small", {}, [` 張 · ${(sheetState.quantityLots * 1000).toLocaleString()} 股`]),
      ]),
    ]),
  ]);
  if (isLimit) {
    const modeLabel = sheetState.transactionMode === "partial_fill_allowed" ? "允許部分成交" : "單次通知";
    grid.appendChild(el("div", { className: "confirm-stat" }, [
      el("span", { className: "label" }, ["交易模式"]),
      el("span", { className: "value small" }, [modeLabel]),
    ]));
  }

  const meta = el("div", { className: "confirm-card-meta" }, [
    el("span", { className: "label" }, ["送出身份"]),
    el("strong", {}, [currentUserName()]),
  ]);

  container.appendChild(el("div", { className: "confirm-card" }, [header, trigger, grid, meta]));
}

async function submitCreate() {
  const body = {
    strategy: sheetState.strategy,
    symbol: sheetState.symbol.symbol,
    quantityLots: Number(sheetState.quantityLots),
    targetPrice: sheetState.targetPrice,
  };
  if (sheetState.strategy.startsWith("limit_")) {
    body.transactionMode = sheetState.transactionMode;
  }

  const resp = await sendRequest({ method: "POST", path: "/trade-intents", body });
  if (resp.status === 201) {
    closeSheet();
    showToast(`委託已建立 — ${resp.body.data.symbol} ${STRATEGY_LABEL[resp.body.data.strategy]}`, "success");
    refreshDashboard();
  } else {
    const msg = resp.body?.error?.message || `建立失敗 (${resp.status})`;
    showToast(msg, "error");
  }
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

export function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const iconChar = type === "success" ? "✓" : type === "error" ? "!" : "i";
  const toast = el("div", { className: `toast ${type}` }, [
    el("div", { className: "toast-icon" }, [iconChar]),
    el("div", { className: "toast-message" }, [message]),
    el(
      "button",
      {
        className: "toast-close",
        "aria-label": "關閉",
        onClick: () => dismissToast(toast),
      },
      ["×"]
    ),
  ]);
  container.appendChild(toast);
  if (type !== "error") {
    setTimeout(() => dismissToast(toast), 3500);
  }
}

function dismissToast(toast) {
  if (!toast || toast.classList.contains("removing")) return;
  toast.classList.add("removing");
  setTimeout(() => toast.remove(), 250);
}

// ---------------------------------------------------------------------------
// Detail sheet (intent + notification)
// ---------------------------------------------------------------------------

function openIntentDetail(intent) {
  const body = document.getElementById("detail-body");
  document.getElementById("detail-title").textContent = `${intent.symbol} ${STRATEGY_LABEL[intent.strategy] || intent.strategy}`;

  const grid = el("dl", { className: "detail-grid" }, [
    row("狀態", STATUS_LABEL[intent.status] || intent.status),
    row("策略", STRATEGY_LABEL[intent.strategy] || intent.strategy),
    row("觸發條件", STRATEGY_TRIGGER_DESC[intent.strategy] || "—"),
    row("目標價", intent.targetPriceEffective),
    row("張數 (委託 / 成交)", `${intent.quantityLots} / ${intent.filledQuantityLots ?? 0}`),
    row("執行模式", intent.executionMode),
    row("時效", intent.timeInForce),
    row("交易模式", intent.transactionMode || "—"),
    row("通知模式", intent.notificationMode || "—"),
    row("交易日", intent.tradingDate),
    row("建立時間", new Date(intent.createdAt).toLocaleString("zh-TW")),
    intent.triggeredAt ? row("觸發時間", new Date(intent.triggeredAt).toLocaleString("zh-TW")) : null,
    intent.lastFillAt ? row("最後成交", new Date(intent.lastFillAt).toLocaleString("zh-TW")) : null,
    intent.cancelledAt ? row("取消時間", new Date(intent.cancelledAt).toLocaleString("zh-TW")) : null,
  ].filter(Boolean));

  body.innerHTML = "";
  body.appendChild(el("div", { className: "detail-section" }, [
    el("div", { className: "detail-section-title" }, ["委託資訊"]),
    grid,
  ]));
  body.appendChild(el("div", { className: "detail-section" }, [
    el("div", { className: "detail-section-title" }, ["原始 ID"]),
    el("pre", {}, [intent.id]),
  ]));

  const actions = el("div", { className: "detail-actions" }, [
    el("button", { className: "detail-action-btn secondary", onClick: closeDetail }, ["關閉"]),
    (intent.status === "active" || intent.status === "scheduled")
      ? el("button", {
          className: "detail-action-btn destructive",
          onClick: () => cancelIntent(intent.id, intent.symbol),
        }, ["取消委託"])
      : null,
  ].filter(Boolean));
  body.appendChild(actions);

  document.getElementById("detail-overlay").hidden = false;
}

function openNotifDetail(notif) {
  const body = document.getElementById("detail-body");
  document.getElementById("detail-title").textContent = notif.renderedTitle;

  const grid = el("dl", { className: "detail-grid" }, [
    row("類型", notif.type === "limit_order_triggered" ? "限價單觸發" : "到價提醒"),
    row("通知時間", new Date(notif.createdAt).toLocaleString("zh-TW")),
    row("狀態", notif.readAt ? `已讀 (${new Date(notif.readAt).toLocaleString("zh-TW")})` : "未讀"),
    notif.tradeIntentId ? row("關聯委託 ID", notif.tradeIntentId) : null,
  ].filter(Boolean));

  body.innerHTML = "";
  body.appendChild(el("div", { className: "detail-section" }, [
    el("div", { className: "detail-section-title" }, ["通知資訊"]),
    grid,
  ]));
  body.appendChild(el("div", { className: "detail-section" }, [
    el("div", { className: "detail-section-title" }, ["完整內容"]),
    el("pre", {}, [notif.renderedBody]),
  ]));

  const actions = el("div", { className: "detail-actions" }, [
    el("button", { className: "detail-action-btn secondary", onClick: closeDetail }, ["關閉"]),
    notif.readAt
      ? null
      : el("button", {
          className: "detail-action-btn primary",
          onClick: async () => {
            await markNotifRead(notif.id);
            closeDetail();
          },
        }, ["標為已讀"]),
  ].filter(Boolean));
  body.appendChild(actions);

  document.getElementById("detail-overlay").hidden = false;
}

function row(label, value) {
  return el("div", {}, [el("dt", {}, [label]), el("dd", {}, [String(value)])]);
}

export function closeDetail() {
  document.getElementById("detail-overlay").hidden = true;
}

// ---------------------------------------------------------------------------
// Symbol picker for watchlist (quote board "加入自選")
// ---------------------------------------------------------------------------

function openSymbolPickerForWatchlist() {
  // Minimal version: prompt for code, validate via GET /symbols/{symbol},
  // then add to watchlist. A polished picker (reuse of order-sheet search) can
  // replace this later without affecting the quote-board public API.
  const code = window.prompt("加入自選 — 輸入股票代號（例如 2330）");
  if (!code) return;
  const trimmed = code.trim();
  if (!trimmed) return;
  sendRequest({ method: "GET", path: `/symbols/${encodeURIComponent(trimmed)}` })
    .then((resp) => {
      if (resp.status === 200) {
        addToWatchlist(trimmed);
      } else {
        alert(resp.body?.error?.message || `找不到 ${trimmed}`);
      }
    })
    .catch(() => alert("加入失敗，請稍候再試"));
}

// ---------------------------------------------------------------------------
// Custom confirm dialog
// ---------------------------------------------------------------------------

function customConfirm({ title = "確認", message = "", okText = "確定", cancelText = "取消", destructive = false }) {
  return new Promise((resolve) => {
    const overlay = document.getElementById("confirm-overlay");
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-message").textContent = message;
    const okBtn = document.getElementById("confirm-ok");
    const cancelBtn = document.getElementById("confirm-cancel");
    okBtn.textContent = okText;
    cancelBtn.textContent = cancelText;
    okBtn.classList.toggle("destructive", destructive);
    okBtn.classList.toggle("primary", !destructive);

    overlay.hidden = false;

    const cleanup = (result) => {
      overlay.hidden = true;
      okBtn.removeEventListener("click", onOk);
      cancelBtn.removeEventListener("click", onCancel);
      overlay.removeEventListener("click", onBackdrop);
      resolve(result);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onBackdrop = (ev) => {
      if (ev.target.id === "confirm-overlay") cleanup(false);
    };
    okBtn.addEventListener("click", onOk);
    cancelBtn.addEventListener("click", onCancel);
    overlay.addEventListener("click", onBackdrop);
  });
}

// ---------------------------------------------------------------------------
// Close-all (exported for Esc keyboard shortcut)
// ---------------------------------------------------------------------------

export function closeAllModals() {
  let closed = false;
  for (const id of ["detail-overlay", "sheet-overlay"]) {
    const node = document.getElementById(id);
    if (node && !node.hidden) {
      node.hidden = true;
      closed = true;
    }
  }
  const confirmOverlay = document.getElementById("confirm-overlay");
  if (confirmOverlay && !confirmOverlay.hidden) {
    document.getElementById("confirm-cancel")?.click();
    closed = true;
  }
  return closed;
}

// ---------------------------------------------------------------------------
// User-mode identity popover (mirrors dev mode switcher)
// ---------------------------------------------------------------------------

let userModePopover = null;

function initUserModeSwitcher() {
  const trigger = document.getElementById("user-mode-trigger");
  const menu = document.getElementById("user-mode-menu");
  if (!trigger || !menu) return;
  userModePopover = attachPopover(trigger, menu, { align: "right" });
  renderIdentityMenu(menu, (label) => {
    setSelectedUser(label);
    syncDevUserSwitcher();
    userModePopover.close();
    showToast(`已切換為 ${trigger.querySelector("strong")?.textContent || label}`, "info");
    refreshDashboard();
  });
  // Items were just created; sync active/avatar state into them.
  syncDevUserSwitcher();
}

// ---------------------------------------------------------------------------
// Exported lifecycle
// ---------------------------------------------------------------------------

export function initUserView() {
  document.getElementById("uv-create-btn").addEventListener("click", openSheet);
  document.getElementById("user-refresh-btn").addEventListener("click", refreshDashboard);
  initUserModeSwitcher();

  // filter chips
  const intentFilterGroup = document.getElementById("uv-intent-filter");
  const repositionIntentIndicator = attachSegmentedIndicator(intentFilterGroup);
  for (const btn of intentFilterGroup.querySelectorAll("button")) {
    btn.addEventListener("click", () => {
      intentFilter = btn.dataset.filter;
      for (const b of intentFilterGroup.querySelectorAll("button")) {
        b.classList.toggle("active", b === btn);
      }
      repositionIntentIndicator();
      renderIntentsFiltered();
    });
  }

  // detail sheet close
  document.getElementById("detail-overlay").addEventListener("click", (ev) => {
    if (ev.target.id === "detail-overlay") closeDetail();
  });
  for (const btn of document.querySelectorAll("[data-close-detail]")) {
    btn.addEventListener("click", closeDetail);
  }

  document.getElementById("sheet-cancel").addEventListener("click", closeSheet);
  document.getElementById("sheet-back").addEventListener("click", () => {
    if (sheetState.step > 1) showSheetStep(sheetState.step - 1);
  });
  document.getElementById("sheet-next").addEventListener("click", async () => {
    if (sheetState.step === 1 && validateStep(1)) showSheetStep(2);
    else if (sheetState.step === 2 && validateStep(2)) {
      renderConfirmSummary();
      showSheetStep(3);
    } else if (sheetState.step === 3) {
      await submitCreate();
    }
  });

  document.getElementById("sheet-overlay").addEventListener("click", (ev) => {
    if (ev.target.id === "sheet-overlay") closeSheet();
  });

  // Step 1: symbol search
  document.getElementById("symbol-search").addEventListener("input", (ev) => {
    sheetState.symbol = null;
    document.getElementById("symbol-selected").hidden = true;
    document.getElementById("sheet-next").disabled = true;
    clearTimeout(searchDebounceTimer);
    const q = ev.target.value.trim();
    searchDebounceTimer = setTimeout(() => searchSymbols(q), 220);
  });

  // Step 2: form change handlers
  for (const r of document.querySelectorAll('input[name="strategy"]')) {
    r.addEventListener("change", (ev) => onStrategyChange(ev.target.value));
  }
  for (const r of document.querySelectorAll('input[name="transactionMode"]')) {
    r.addEventListener("change", (ev) => {
      sheetState.transactionMode = ev.target.value;
    });
  }
  document.getElementById("form-quantity").addEventListener("input", (ev) => {
    sheetState.quantityLots = ev.target.value;
    document.getElementById("sheet-next").disabled = !validateStep(2);
  });
  document.getElementById("form-target").addEventListener("input", (ev) => {
    sheetState.targetPrice = ev.target.value;
    document.getElementById("sheet-next").disabled = !validateStep(2);
  });
}

export function onEnterUserMode() {
  setAddSymbolHandler(openSymbolPickerForWatchlist);
  mountQuoteBoard();
  refreshDashboard();
  startDashboardPoll();
}

export function onLeaveUserMode() {
  unmountQuoteBoard();
  stopDashboardPoll();
}
