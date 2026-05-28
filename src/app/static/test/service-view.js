// ai-stock /test — 服務端控制台
//
// 提供 demo/ops 操作 server-side 動作的友善 UI：推送行情、控制時鐘、
// 檢視服務狀態、手動觸發評估。所有 action 走既有的 /dev/* 端點。
//
// 匯出：
//   initServiceView()     — 綁定 DOM event 一次性
//   onEnterServiceMode()  — 切到服務模式時刷新狀態

import { attachSegmentedIndicator, sendRequest, USERS } from "/test-assets/app.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const svcState = {
  quoteSymbol: null, // { symbol, displayName }
  quoteSide: "ask",
};

let symbolSearchDebounce = null;
let stateRefreshTimer = null;
let repositionSideIndicator = () => {};

// ---------------------------------------------------------------------------
// Local DOM helpers
// ---------------------------------------------------------------------------

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Cross-tab signal: notify any open user-mode tab that the dashboard
// underlying data may have changed, so it can refetch immediately instead
// of waiting for its 5-second poll.
function notifyDashboardStale(source) {
  try {
    const bc = new BroadcastChannel("ai-stock-test:dashboard");
    bc.postMessage({ type: "stale", source, at: Date.now() });
    bc.close();
  } catch (_) {
    // BroadcastChannel unavailable (very old browser) — polling fallback covers it.
  }
}

function setLog(id, text, kind) {
  const el = $(id);
  if (!text) {
    el.hidden = true;
    el.className = "svc-log";
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.className = "svc-log" + (kind ? ` ${kind}` : "");
  el.textContent = text;
}

function fmtClock(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-TW", {
      hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      timeZone: "Asia/Taipei",
    });
  } catch (_) {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Symbol search (lightweight, dropdown style)
// ---------------------------------------------------------------------------

async function searchQuoteSymbols(query) {
  const results = $("svc-symbol-results");
  if (!query) {
    results.innerHTML = "";
    results.classList.add("hidden");
    return;
  }
  const resp = await sendRequest({
    method: "GET",
    path: "/symbols",
    query: { q: query, limit: 8 },
  }).catch(() => null);

  if (!resp || resp.status !== 200) {
    results.innerHTML = "";
    results.classList.add("hidden");
    return;
  }

  const items = resp.body?.data || [];
  if (items.length === 0) {
    results.classList.remove("hidden");
    results.innerHTML = `
      <div class="symbol-results-state">
        <div class="symbol-results-state-title">找不到「${escapeHtml(query)}」</div>
      </div>
    `;
    return;
  }

  results.classList.remove("hidden");
  results.innerHTML = "";
  for (const sym of items) {
    const code = sym.symbol;
    const name = sym.displayName || code;
    const initial = code.charAt(0).toUpperCase();
    const item = document.createElement("div");
    item.className = "symbol-result-item";
    item.innerHTML = `
      <span class="symbol-result-avatar">${escapeHtml(initial)}</span>
      <div class="symbol-result-text">
        <span class="symbol-result-code">${escapeHtml(code)}</span>
        <span class="symbol-result-name">${escapeHtml(name)}</span>
      </div>
      ${sym.market ? `<span class="symbol-result-market">${escapeHtml(sym.market)}</span>` : ""}
    `;
    item.addEventListener("click", () => {
      svcState.quoteSymbol = { symbol: code, displayName: name };
      $("svc-quote-symbol").value = `${code} ${name}`;
      results.innerHTML = "";
      results.classList.add("hidden");
      updatePushButtonState();
    });
    results.appendChild(item);
  }
}

function updatePushButtonState() {
  const btn = $("svc-push-btn");
  const hasSymbol = !!svcState.quoteSymbol;
  const hasPrice = $("svc-quote-price").value.trim().length > 0;
  btn.disabled = !(hasSymbol && hasPrice);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function fetchNotificationsAs(userLabel) {
  // The /notifications endpoint is owner-scoped (X-Local-User-Id); the push-quote
  // dispatcher runs synchronously across ALL users' active intents, so to see who
  // got triggered we have to poll each test user separately.
  const headers = { "Content-Type": "application/json" };
  if (userLabel !== "default" && USERS[userLabel]) {
    headers["X-Local-User-Id"] = USERS[userLabel];
  }
  try {
    const resp = await fetch("/notifications?pageSize=10", { headers });
    if (!resp.ok) return [];
    const body = await resp.json();
    return body?.data || [];
  } catch (_) {
    return [];
  }
}

// Side semantics for the three strategies — used both for the static hint
// next to the side chips and the smart "0 triggered" diagnostic.
const STRATEGY_REQUIRED_SIDE = {
  buy_price_alert: "ask",
  limit_buy_order: "ask",
  sell_price_alert: "bid",
  limit_sell_order: "bid",
};

const STRATEGY_LABEL = {
  buy_price_alert: "買進到價提醒",
  sell_price_alert: "賣出到價提醒",
  limit_buy_order: "限價買單",
  limit_sell_order: "限價賣單",
};

const SIDE_HINT_TEXT = {
  ask: "<strong>賣價 ask</strong>：用於買進到價提醒 / 限價買單（條件 ask ≤ 目標價）",
  bid: "<strong>買價 bid</strong>：用於賣出到價提醒 / 限價賣單（條件 bid ≥ 目標價）",
  last: "<strong>成交 last</strong>：僅供觀察，目前 4 種策略都不靠 last 觸發",
};

function updateSideHint() {
  const hintEl = $("svc-side-hint");
  if (hintEl) hintEl.innerHTML = SIDE_HINT_TEXT[svcState.quoteSide] || "";
}

async function fetchIntentsAs(userLabel) {
  const headers = { "Content-Type": "application/json" };
  if (userLabel !== "default" && USERS[userLabel]) {
    headers["X-Local-User-Id"] = USERS[userLabel];
  }
  try {
    const resp = await fetch("/trade-intents?pageSize=50", { headers });
    if (!resp.ok) return [];
    const body = await resp.json();
    return body?.data || [];
  } catch (_) {
    return [];
  }
}

async function diagnoseNoTrigger(symbol, pushedSide, pushedPrice) {
  // Look at active intents on this symbol across test users and figure out
  // why none triggered. Most common: user pushed ask but a sell_price_alert
  // wants bid, or vice versa.
  const labels = ["default", ...Object.keys(USERS)];
  const allActive = [];
  await Promise.all(labels.map(async (label) => {
    const items = await fetchIntentsAs(label);
    for (const i of items) {
      if (i.symbol !== symbol) continue;
      if (i.status !== "active") continue;
      allActive.push({ owner: label, intent: i });
    }
  }));

  if (allActive.length === 0) return "";

  const wrongSide = allActive.filter(({ intent }) => {
    const needed = STRATEGY_REQUIRED_SIDE[intent.strategy];
    return needed && needed !== pushedSide && needed !== "last";
  });

  if (wrongSide.length === 0) {
    // All side-matched intents must have unmet price conditions.
    const lines = allActive.map(({ owner, intent }) => {
      const required = STRATEGY_REQUIRED_SIDE[intent.strategy];
      const cmp = required === "ask" ? "≤" : "≥";
      return `  ${owner} / ${STRATEGY_LABEL[intent.strategy] || intent.strategy} 目標 ${intent.targetPriceEffective}（需 ${required} ${cmp} ${intent.targetPriceEffective}）`;
    }).join("\n");
    return `\n\n相關 active 委託：\n${lines}\n→ 你推送的 ${pushedSide}=${pushedPrice} 不符合條件`;
  }

  const lines = wrongSide.map(({ owner, intent }) => {
    const required = STRATEGY_REQUIRED_SIDE[intent.strategy];
    const cmp = required === "ask" ? "≤" : "≥";
    return `  ${owner} / ${STRATEGY_LABEL[intent.strategy] || intent.strategy} 目標 ${intent.targetPriceEffective}（需 ${required} ${cmp} ${intent.targetPriceEffective}）`;
  }).join("\n");
  // Suggest the correct side based on the first mismatched intent
  const needed = STRATEGY_REQUIRED_SIDE[wrongSide[0].intent.strategy];
  const suggestPrice = wrongSide[0].intent.targetPriceEffective;
  return `\n\n💡 提示：你推的是 ${pushedSide}，但這個標的有委託要看 ${needed}\n${lines}\n→ 改推送 ${needed}=${suggestPrice} 試試`;
}

async function countTriggersSince(thresholdMs) {
  const labels = ["default", ...Object.keys(USERS)];
  const results = await Promise.all(labels.map((label) => fetchNotificationsAs(label).then((items) => {
    const recent = items.filter((n) => {
      const t = new Date(n.createdAt).getTime();
      return Number.isFinite(t) && t >= thresholdMs;
    });
    return [label, recent];
  })));
  const counts = {};
  let total = 0;
  for (const [label, recent] of results) {
    if (recent.length > 0) {
      counts[label] = recent.length;
      total += recent.length;
    }
  }
  return { counts, total };
}

async function pushQuote() {
  if (!svcState.quoteSymbol) return;
  const price = $("svc-quote-price").value.trim();
  if (!price) return;
  const body = { symbol: svcState.quoteSymbol.symbol };
  if (svcState.quoteSide === "ask") body.askPrice = price;
  if (svcState.quoteSide === "bid") body.bidPrice = price;
  if (svcState.quoteSide === "last") body.lastPrice = price;

  setLog("svc-push-log", "推送中…");
  const pushStartedMs = Date.now() - 500; // small safety window for clock skew

  // Pre-check session state — dispatcher only triggers `active` intents, and
  // intents are kept `scheduled` outside regular session. Telling the user
  // up front beats them wondering why nothing fired.
  const stateBefore = await sendRequest({ method: "GET", path: "/dev/server-state" }).catch(() => null);
  const withinSession = !!stateBefore?.body?.data?.clock?.withinRegularSession;

  const resp = await sendRequest({
    method: "POST",
    path: "/dev/push-quote",
    body,
  });
  if (!(resp.status >= 200 && resp.status < 300)) {
    setLog("svc-push-log", `失敗 ${resp.status}\n${JSON.stringify(resp.body?.error || resp.body, null, 2)}`, "err");
    return;
  }
  // Dispatcher runs synchronously inside push_quote, so notifications already
  // exist by the time the response returns. Aggregate them across test users.
  const { counts, total } = await countTriggersSince(pushStartedMs);
  const head = `已推送 ${svcState.quoteSymbol.symbol} ${svcState.quoteSide}=${price}`;
  if (total === 0) {
    let body = `${head}\n沒有委託被觸發`;
    if (!withinSession) {
      body += `\n\n⚠️ 目前盤外時間\n` +
              `dispatcher 只會觸發 active 的委託，而盤外建立的委託會是 scheduled。\n` +
              `Demo 流程：\n  1) 用「時鐘控制」凍結到盤中（例：09:30）\n  2) 切到「使用者」模式新增委託（這時會是 active）\n  3) 回到服務端推送行情`;
    } else {
      const diagnosis = await diagnoseNoTrigger(svcState.quoteSymbol.symbol, svcState.quoteSide, price);
      body += diagnosis || `\n\n提示：active 委託的條件可能未滿足，或所有相關委託已被觸發過。`;
    }
    setLog("svc-push-log", body, withinSession ? "ok" : "err");
  } else {
    const lines = Object.entries(counts).map(([u, n]) => `  ${u}: ${n} 筆`).join("\n");
    setLog("svc-push-log", `${head}\n觸發 ${total} 筆通知\n${lines}`, "ok");
  }
  notifyDashboardStale("push-quote");
  refreshServerState();
}

async function freezeClockAt(localValue) {
  if (!localValue) {
    setLog("svc-clock-log", "請先選擇日期時間", "err");
    return;
  }
  // localValue is "YYYY-MM-DDTHH:mm" in local time; append seconds + Taipei offset
  const iso = `${localValue}:00+08:00`;
  await callSetClock({ fakeNow: iso }, `凍結於 ${localValue}`);
}

async function advanceClock(seconds) {
  await callSetClock({ advanceSeconds: Number(seconds) }, `推進 ${seconds} 秒`);
}

async function resetClock() {
  await callSetClock({}, "重置為實際時間");
}

async function callSetClock(body, label) {
  setLog("svc-clock-log", `${label}…`);
  const resp = await sendRequest({ method: "POST", path: "/dev/set-clock", body });
  if (resp.status >= 200 && resp.status < 300) {
    setLog("svc-clock-log", `${label} ✓`, "ok");
    notifyDashboardStale("set-clock");
    refreshServerState();
  } else {
    setLog("svc-clock-log", `失敗 ${resp.status}\n${JSON.stringify(resp.body?.error || resp.body, null, 2)}`, "err");
  }
}

async function evaluateQuotes() {
  const symbol = $("svc-eval-symbol").value.trim();
  const body = symbol ? { symbols: [symbol] } : {};
  setLog("svc-eval-log", "評估中…");
  const resp = await sendRequest({ method: "POST", path: "/dev/evaluate-quotes", body });
  if (resp.status >= 200 && resp.status < 300) {
    const evaluated = resp.body?.data?.evaluatedIntents ?? "—";
    const triggered = resp.body?.data?.triggeredIntentIds?.length ?? 0;
    setLog("svc-eval-log", `評估完成\n標的：${symbol || "全部"}\n評估委託：${evaluated}\n觸發：${triggered} 筆`, "ok");
    if (triggered > 0) notifyDashboardStale("evaluate-quotes");
  } else {
    setLog("svc-eval-log", `失敗 ${resp.status}\n${JSON.stringify(resp.body?.error || resp.body, null, 2)}`, "err");
  }
}

// ---------------------------------------------------------------------------
// TWAP worker buttons — manual demo trigger
// ---------------------------------------------------------------------------

async function runTwapWorker(path, label) {
  setLog("svc-twap-log", `${label}…`);
  const resp = await sendRequest({ method: "POST", path });
  if (resp.status >= 200 && resp.status < 300) {
    const processed = resp.body?.data?.processedCount ?? 0;
    setLog("svc-twap-log", `${label} ✓\n處理切片：${processed} 筆`, processed > 0 ? "ok" : "");
    if (processed > 0) notifyDashboardStale(path);
  } else {
    setLog("svc-twap-log", `${label} 失敗 ${resp.status}\n${JSON.stringify(resp.body?.error || resp.body, null, 2)}`, "err");
  }
}

// ---------------------------------------------------------------------------
// Broker demo current price — single-symbol live lookup
// ---------------------------------------------------------------------------

async function fetchCurrentPrice(symbol) {
  setLog("svc-current-price-log", `查詢 ${symbol}…`);
  const resp = await sendRequest({
    method: "GET",
    path: `/quotes/current-price/${encodeURIComponent(symbol)}`,
  });
  if (resp.status >= 200 && resp.status < 300) {
    const d = resp.body?.data || {};
    const lines = [
      `標的：${d.symbol || symbol}`,
      `成交：${d.currentPrice ?? "—"}`,
      `買價：${d.bidPrice ?? "—"}`,
      `賣價：${d.askPrice ?? "—"}`,
      `行情時間：${d.quoteTime ? fmtClock(d.quoteTime) : "—"}`,
      `來源：${d.source || "—"}`,
    ];
    setLog("svc-current-price-log", lines.join("\n"), "ok");
  } else {
    setLog(
      "svc-current-price-log",
      `失敗 ${resp.status}\n${JSON.stringify(resp.body?.error || resp.body, null, 2)}`,
      "err",
    );
  }
}

// ---------------------------------------------------------------------------
// Server state display
// ---------------------------------------------------------------------------

async function refreshServerState() {
  const grid = $("svc-state-grid");
  const resp = await sendRequest({ method: "GET", path: "/dev/server-state" });
  if (resp.status !== 200) {
    grid.innerHTML = `<div class="svc-loading">讀取失敗（HTTP ${resp.status}）</div>`;
    return;
  }
  const data = resp.body?.data || {};
  const clock = data.clock || {};
  const quote = data.quote || {};

  // Update clock display in the clock card
  const status = $("svc-clock-status");
  const time = $("svc-clock-time");
  if (clock.isFrozen) {
    status.textContent = "凍結";
    status.className = "svc-clock-status frozen";
  } else {
    status.textContent = "實時";
    status.className = "svc-clock-status live";
  }
  time.textContent = fmtClock(clock.currentTaipei);

  // Populate server state grid
  const items = [
    ["環境", data.appEnv || "—"],
    ["LOCAL_MODE", data.localMode ? "ON" : "OFF"],
    ["時鐘狀態", clock.isFrozen ? "凍結中" : "實時"],
    ["當下時間 (TPE)", fmtClock(clock.currentTaipei)],
    ["盤中", clock.withinRegularSession ? "是" : "否"],
    ["行情來源", quote.provider || "—"],
    ["訂閱中", Array.isArray(quote.subscribedSymbols) ? quote.subscribedSymbols.join(", ") || "（無）" : "—"],
  ];
  grid.innerHTML = "";
  for (const [label, value] of items) {
    const item = document.createElement("div");
    item.className = "svc-state-item";
    item.innerHTML = `
      <div class="label">${escapeHtml(label)}</div>
      <div class="value ${value === "—" || value === "OFF" ? "dim" : ""}">${escapeHtml(String(value))}</div>
    `;
    grid.appendChild(item);
  }
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

export function initServiceView() {
  // Symbol search
  $("svc-quote-symbol").addEventListener("input", (ev) => {
    svcState.quoteSymbol = null;
    updatePushButtonState();
    clearTimeout(symbolSearchDebounce);
    const q = ev.target.value.trim();
    symbolSearchDebounce = setTimeout(() => searchQuoteSymbols(q), 220);
  });

  // Side chips
  const sideChips = $("svc-side-chips");
  repositionSideIndicator = attachSegmentedIndicator(sideChips);
  for (const btn of sideChips.querySelectorAll("button")) {
    btn.setAttribute("aria-selected", btn.classList.contains("active") ? "true" : "false");
    btn.addEventListener("click", () => {
      svcState.quoteSide = btn.dataset.side;
      for (const b of sideChips.querySelectorAll("button")) {
        const active = b === btn;
        b.classList.toggle("active", active);
        b.setAttribute("aria-selected", active ? "true" : "false");
      }
      repositionSideIndicator();
      updateSideHint();
    });
  }
  updateSideHint();

  // Push quote
  $("svc-quote-price").addEventListener("input", updatePushButtonState);
  $("svc-push-btn").addEventListener("click", pushQuote);

  // Clock controls
  $("svc-freeze-btn").addEventListener("click", () => freezeClockAt($("svc-clock-input").value));
  $("svc-clock-reset").addEventListener("click", resetClock);
  for (const btn of document.querySelectorAll(".svc-clock-quick [data-advance]")) {
    btn.addEventListener("click", () => advanceClock(btn.dataset.advance));
  }

  // Eval
  $("svc-eval-btn").addEventListener("click", evaluateQuotes);

  // TWAP worker
  $("svc-twap-due-btn").addEventListener("click", () =>
    runTwapWorker("/dev/twap/process-due-slices", "處理到期切片"),
  );
  $("svc-twap-followup-btn").addEventListener("click", () =>
    runTwapWorker("/dev/twap/process-price-followups", "處理價格追蹤"),
  );

  // Broker demo current price
  for (const btn of document.querySelectorAll(".svc-twap-symbols [data-symbol]")) {
    btn.addEventListener("click", () => fetchCurrentPrice(btn.dataset.symbol));
  }

  // Refresh button in topbar
  $("svc-refresh-btn").addEventListener("click", refreshServerState);
}

export function onEnterServiceMode() {
  refreshServerState();
  repositionSideIndicator();
  // auto-refresh every 5s while in service mode
  clearInterval(stateRefreshTimer);
  stateRefreshTimer = setInterval(() => {
    if (document.getElementById("app").dataset.mode === "service") {
      refreshServerState();
    } else {
      clearInterval(stateRefreshTimer);
    }
  }, 5000);
}
