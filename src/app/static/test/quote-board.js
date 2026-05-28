// User-mode quote board.
//
// Responsibility split:
//   - watchlist storage: per-user list of symbol codes in localStorage
//   - render: tiles + add-tile + empty hint
//   - polling: 3s loop, AbortController, visibility-aware (Task 9)
//   - lifecycle: mount/unmount called by user-view.js (Task 10)

import { sendRequest, getSelectedUser } from "./app.js";

const POLL_MS = 3000;
const MAX_CONSECUTIVE_ERRORS = 3;
const BACKOFF_MS = 30_000;
const WATCHLIST_KEY = "ai-stock-user-watchlist";
const COLLAPSED_KEY = "ai-stock-user-quote-board-collapsed";

let pollTimer = null;
let inflightController = null;
let baselinePriceByCode = {};
let consecutiveErrors = 0;
let cooldownUntilMs = 0;
let onAddSymbolCallback = null;

// ---------------------------------------------------------------------------
// Watchlist storage
// ---------------------------------------------------------------------------

function readWatchlistMap() {
  try {
    return JSON.parse(localStorage.getItem(WATCHLIST_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeWatchlistMap(map) {
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(map));
}

function currentWatchlist() {
  const map = readWatchlistMap();
  const user = getSelectedUser();
  const list = map[user];
  return Array.isArray(list) ? list.slice() : [];
}

function persistWatchlist(list) {
  const map = readWatchlistMap();
  map[getSelectedUser()] = list;
  writeWatchlistMap(map);
}

export function addToWatchlist(code) {
  const list = currentWatchlist();
  if (list.includes(code)) return;
  list.push(code);
  persistWatchlist(list);
  baselinePriceByCode[code] = null;
  renderTiles({ optimistic: true });
}

export function removeFromWatchlist(code) {
  const list = currentWatchlist().filter((c) => c !== code);
  persistWatchlist(list);
  delete baselinePriceByCode[code];
  renderTiles();
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function tileHtml(row) {
  const main = row.lastPrice ?? row.askPrice ?? row.bidPrice;
  const baseline = baselinePriceByCode[row.symbol];
  let changeClass = "flat";
  let changeText = "─";
  if (main != null && baseline != null && baseline !== "0") {
    const cur = Number(main);
    const base = Number(baseline);
    const pct = ((cur - base) / base) * 100;
    if (Math.abs(pct) < 0.01) {
      changeClass = "flat";
      changeText = "─";
    } else if (pct > 0) {
      changeClass = "up";
      changeText = `↑${pct.toFixed(2)}%`;
    } else {
      changeClass = "down";
      changeText = `↓${Math.abs(pct).toFixed(2)}%`;
    }
  }
  if (main != null && baseline == null) {
    baselinePriceByCode[row.symbol] = String(main);
  }
  const priceDisplay = main != null ? Number(main).toFixed(2) : "—";
  const stale = row.stale ? " stale" : "";
  return `
    <article class="qb-tile${stale}" data-symbol="${escapeAttr(row.symbol)}" role="listitem">
      <button class="qb-tile-remove" type="button" data-action="remove" aria-label="移除 ${escapeAttr(row.symbol)}">×</button>
      <span class="qb-tile-code">${escapeText(row.symbol)}</span>
      <span class="qb-tile-name">${escapeText(row.displayName || "")}</span>
      <span class="qb-tile-price">${priceDisplay}</span>
      <span class="qb-tile-change ${changeClass}">${changeText}</span>
      ${row.stale ? `<span class="qb-tile-foot">等待行情</span>` : ""}
    </article>
  `;
}

function addTileHtml() {
  return `<button type="button" class="qb-add-tile" id="qb-add-btn">＋ 加入自選</button>`;
}

function emptyHintHtml() {
  return `<div class="qb-empty-hint">加入想關注的股票，看價格自動更新</div>`;
}

function renderTiles({ rows, optimistic } = {}) {
  const container = document.getElementById("qb-tiles");
  if (!container) return;
  const watchlist = currentWatchlist();
  if (rows == null) {
    rows = watchlist.map((code) => ({ symbol: code, stale: true, displayName: "" }));
  }
  const byCode = Object.fromEntries(rows.map((r) => [r.symbol, r]));
  const orderedRows = watchlist.map((code) => byCode[code] || { symbol: code, stale: true, displayName: "" });
  const tilesHtml = orderedRows.map(tileHtml).join("");
  const emptyState = watchlist.length === 0 ? emptyHintHtml() : "";
  container.innerHTML = emptyState + tilesHtml + addTileHtml();
  bindTileEvents(container);
  if (optimistic) {
    void pollNow();
  }
}

function bindTileEvents(container) {
  for (const btn of container.querySelectorAll(".qb-tile-remove")) {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const code = ev.currentTarget.closest(".qb-tile").dataset.symbol;
      removeFromWatchlist(code);
    });
  }
  const addBtn = container.querySelector("#qb-add-btn");
  if (addBtn && onAddSymbolCallback) {
    addBtn.addEventListener("click", () => onAddSymbolCallback());
  }
}

// ---------------------------------------------------------------------------
// Escape helpers
// ---------------------------------------------------------------------------

function escapeText(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeText(s).replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

export async function pollNow() {
  const board = document.getElementById("uv-quote-board");
  if (!board || board.hidden) return;
  if (board.dataset.collapsed === "true") return;
  if (document.visibilityState === "hidden") return;
  const now = Date.now();
  if (now < cooldownUntilMs) return;
  const list = currentWatchlist();
  if (list.length === 0) {
    renderTiles({ rows: [] });
    scheduleNextPoll();
    return;
  }

  if (inflightController) inflightController.abort();
  inflightController = new AbortController();
  const signal = inflightController.signal;

  let resp;
  try {
    resp = await sendRequest({
      method: "GET",
      path: "/quotes",
      query: { symbols: list.join(",") },
      signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") return;
    onPollError(err);
    scheduleNextPoll();
    return;
  }

  if (resp.status === 200 && resp.body?.data) {
    consecutiveErrors = 0;
    cooldownUntilMs = 0;
    renderTiles({ rows: resp.body.data });
  } else if (resp.status === 404 && resp.body?.error?.code === "UNKNOWN_SYMBOL") {
    const bad = resp.body.error?.details?.symbol;
    if (bad) {
      console.warn(`[quote-board] dropping unknown symbol ${bad} from watchlist`);
      removeFromWatchlist(bad);
    } else {
      // Backend regression guard: UNKNOWN_SYMBOL without details.symbol leaves
      // us unable to identify which entry to drop, so treat as generic error
      // (triggers backoff) instead of looping.
      onPollError(new Error("UNKNOWN_SYMBOL response missing details.symbol"));
    }
  } else {
    onPollError(new Error(`HTTP ${resp.status}`));
  }
  scheduleNextPoll();
}

function onPollError(err) {
  consecutiveErrors += 1;
  console.warn("[quote-board] poll failed:", err?.message || err);
  if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
    cooldownUntilMs = Date.now() + BACKOFF_MS;
    consecutiveErrors = 0;
    console.warn("[quote-board] backing off 30s after repeated failures");
  }
  const container = document.getElementById("qb-tiles");
  if (!container) return;
  for (const tile of container.querySelectorAll(".qb-tile")) {
    if (!tile.querySelector(".qb-tile-foot.error")) {
      const foot = document.createElement("span");
      foot.className = "qb-tile-foot error";
      foot.textContent = "⚠ 連線中斷";
      tile.appendChild(foot);
    }
  }
}

function scheduleNextPoll() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => void pollNow(), POLL_MS);
}

// ---------------------------------------------------------------------------
// Public lifecycle — wired by user-view.js in Task 10
// ---------------------------------------------------------------------------

export function setAddSymbolHandler(fn) {
  onAddSymbolCallback = fn;
}

export function mountQuoteBoard() {
  const board = document.getElementById("uv-quote-board");
  if (!board) return;
  board.hidden = false;
  const collapsed = localStorage.getItem(COLLAPSED_KEY) === "true";
  board.dataset.collapsed = collapsed ? "true" : "false";
  const toggleBtn = document.getElementById("qb-toggle-btn");
  if (toggleBtn) {
    toggleBtn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggleBtn.querySelector(".qb-toggle-label").textContent = collapsed ? "顯示" : "隱藏";
  }
  renderTiles();
  installBoardControls();
  bindVisibility();
  bindUserChange();
  // Defer first poll to next macrotask so renderTiles() DOM mutation doesn't
  // race with Playwright's actionability check on neighboring UI elements
  // (e.g. #user-mode-trigger). Subsequent polls are already async via setTimeout.
  setTimeout(() => void pollNow(), 0);
}

export function unmountQuoteBoard() {
  const board = document.getElementById("uv-quote-board");
  if (board) board.hidden = true;
  if (inflightController) {
    inflightController.abort();
    inflightController = null;
  }
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  baselinePriceByCode = {};
  consecutiveErrors = 0;
  cooldownUntilMs = 0;
}

// ---------------------------------------------------------------------------
// DOM control wiring (toggle / refresh buttons + page visibility)
// ---------------------------------------------------------------------------

function installBoardControls() {
  const toggleBtn = document.getElementById("qb-toggle-btn");
  const refreshBtn = document.getElementById("qb-refresh-btn");
  const board = document.getElementById("uv-quote-board");
  if (toggleBtn && !toggleBtn.dataset.bound) {
    toggleBtn.dataset.bound = "true";
    toggleBtn.addEventListener("click", () => {
      if (!board) return;
      const wasCollapsed = board.dataset.collapsed === "true";
      const next = !wasCollapsed;
      board.dataset.collapsed = next ? "true" : "false";
      localStorage.setItem(COLLAPSED_KEY, next ? "true" : "false");
      toggleBtn.setAttribute("aria-expanded", next ? "false" : "true");
      toggleBtn.querySelector(".qb-toggle-label").textContent = next ? "顯示" : "隱藏";
      if (next) {
        if (pollTimer) {
          clearTimeout(pollTimer);
          pollTimer = null;
        }
        if (inflightController) {
          inflightController.abort();
          inflightController = null;
        }
      } else {
        void pollNow();
      }
    });
  }
  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "true";
    refreshBtn.addEventListener("click", () => void pollNow());
  }
}

let visibilityBound = false;
function bindVisibility() {
  if (visibilityBound) return;
  visibilityBound = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void pollNow();
    }
  });
}

// ---------------------------------------------------------------------------
// User identity change — reset baselines, abort inflight, immediate re-poll
// ---------------------------------------------------------------------------

let userChangeBound = false;
function bindUserChange() {
  if (userChangeBound) return;
  userChangeBound = true;
  window.addEventListener("ai-stock:user-changed", () => {
    if (inflightController) {
      inflightController.abort();
      inflightController = null;
    }
    baselinePriceByCode = {};
    renderTiles();
    void pollNow();
  });
}
