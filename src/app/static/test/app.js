// ai-stock /test page — single-page API tester.
//
// Pure vanilla JS (ES modules). No build step, no external libraries.
// Loads the endpoint catalog from endpoints.js, renders the left pane,
// builds requests, sends them via fetch, and shows the response.

import { ENDPOINTS } from "/test-assets/endpoints.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  selected: null, // endpoint object
  history: [], // last N responses
};

const MAX_HISTORY = 5;

const USER_STORAGE_KEY = "ai-stock-test-users";
const SELECTED_USER_KEY = "ai-stock-test-selected-user";

// ---------------------------------------------------------------------------
// User context (v1)
// ---------------------------------------------------------------------------
//
// `default` means "do not send X-Local-User-Id header" → backend falls back
// to the configured LOCAL_USER_ID. Other entries are arbitrary UUIDs minted
// per browser session and persisted in localStorage so the same alice/bob
// across tab reloads continues to own the same intents.

function loadUsers() {
  const raw = localStorage.getItem(USER_STORAGE_KEY);
  if (raw) {
    try {
      return JSON.parse(raw);
    } catch (_) {
      // fall through to default below
    }
  }
  const fresh = {
    alice: crypto.randomUUID(),
    bob: crypto.randomUUID(),
    charlie: crypto.randomUUID(),
  };
  localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(fresh));
  return fresh;
}

export const USERS = loadUsers();

export function getSelectedUser() {
  return localStorage.getItem(SELECTED_USER_KEY) || "default";
}

export function setSelectedUser(label) {
  localStorage.setItem(SELECTED_USER_KEY, label);
}

function currentUserUuid() {
  const label = getSelectedUser();
  return label === "default" ? null : USERS[label];
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderEndpointList(filter = "") {
  const container = document.getElementById("endpoint-list");
  container.innerHTML = "";

  const groups = {};
  for (const ep of ENDPOINTS) {
    if (filter) {
      const haystack = `${ep.method} ${ep.path} ${ep.label || ""}`.toLowerCase();
      if (!haystack.includes(filter.toLowerCase())) continue;
    }
    (groups[ep.group] ??= []).push(ep);
  }

  for (const [group, items] of Object.entries(groups)) {
    const section = document.createElement("section");
    section.className = "ep-group";

    const header = document.createElement("header");
    header.className = "ep-group-title";
    header.textContent = group;
    section.appendChild(header);

    const list = document.createElement("div");
    list.className = "ep-group-items";

    for (const ep of items) {
      const div = document.createElement("div");
      div.className = `endpoint-item ${ep.implemented ? "" : "disabled"}`;
      div.title = ep.implemented ? ep.label || "" : `${ep.label || ""} — 規劃中（${ep.ticket || ""}）`;
      // method+path keys used by selectEndpoint to highlight the correct row
      // (textContent.includes was matching any row whose path string contained
      // the clicked path, so GET /trade-intents lit up POST /trade-intents).
      div.dataset.endpointMethod = ep.method;
      div.dataset.endpointPath = ep.path;
      const flag = ep.implemented
        ? `<span class="endpoint-status">✓</span>`
        : `<span class="endpoint-status">○</span>`;
      div.innerHTML = `
        <span class="endpoint-method ${ep.method}">${ep.method}</span>
        <span class="endpoint-path">${ep.path}</span>
        ${ep.ticket ? `<span class="endpoint-ticket">${ep.ticket}</span>` : ""}
        ${flag}
      `;
      if (ep.implemented) {
        div.addEventListener("click", () => selectEndpoint(ep));
      }
      list.appendChild(div);
    }

    section.appendChild(list);
    container.appendChild(section);
  }
}

function renderUserSelect() {
  const sel = document.getElementById("user-select");
  sel.innerHTML = "";
  const defaultOpt = document.createElement("option");
  defaultOpt.value = "default";
  defaultOpt.textContent = "default (LOCAL_USER_ID)";
  sel.appendChild(defaultOpt);
  for (const [label, uuid] of Object.entries(USERS)) {
    const opt = document.createElement("option");
    opt.value = label;
    opt.textContent = `${label} (${uuid.slice(0, 8)}…)`;
    sel.appendChild(opt);
  }
  sel.value = getSelectedUser();
  sel.addEventListener("change", () => {
    setSelectedUser(sel.value);
  });
}

function selectEndpoint(ep) {
  state.selected = ep;

  document.querySelectorAll(".endpoint-item.active").forEach((el) => el.classList.remove("active"));
  const selector = `.endpoint-item[data-endpoint-method="${ep.method}"][data-endpoint-path="${CSS.escape(ep.path)}"]`;
  const match = document.querySelector(selector);
  if (match) match.classList.add("active");

  const methodEl = document.getElementById("req-method");
  methodEl.textContent = ep.method;
  methodEl.className = `method ${ep.method}`;
  const pathEl = document.getElementById("req-path");
  pathEl.textContent = ep.path;
  pathEl.classList.remove("path-placeholder");

  renderEndpointDescription(ep);
  renderPathParamFields(ep);
  renderQueryParamFields(ep);
  document.getElementById("req-headers").value = "";
  document.getElementById("req-body").value = "";
  document.getElementById("send-btn").disabled = false;

  const exampleSel = document.getElementById("example-select");
  exampleSel.innerHTML = '<option value="">Fill example…</option>';
  if (ep.examples) {
    for (const name of Object.keys(ep.examples)) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      exampleSel.appendChild(opt);
    }
  }
}

function renderEndpointDescription(ep) {
  const box = document.getElementById("endpoint-description");
  if (!ep.description) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.innerHTML = "";
  if (ep.label) {
    const lbl = document.createElement("span");
    lbl.className = "label";
    lbl.textContent = ep.label;
    box.appendChild(lbl);
  }
  box.appendChild(document.createTextNode(ep.description));
}

// Pull `{name}` placeholders from the path so even endpoints without an
// explicit `pathParamSpecs` get one field per template var.
function extractPathParamNames(path) {
  return Array.from(path.matchAll(/\{([^}]+)\}/g)).map((m) => m[1]);
}

function renderPathParamFields(ep) {
  const section = document.getElementById("path-params-section");
  const container = document.getElementById("path-params-fields");
  container.innerHTML = "";
  const names = extractPathParamNames(ep.path);
  if (names.length === 0) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  for (const name of names) {
    const spec = (ep.pathParamSpecs || {})[name] || {};
    const examplePathParams = ep.pathParams || {};
    const initial =
      spec.example !== undefined && !String(spec.example).startsWith("<")
        ? String(spec.example)
        : examplePathParams[name] && !String(examplePathParams[name]).startsWith("<")
        ? String(examplePathParams[name])
        : "";
    container.appendChild(buildParamField({
      name,
      description: spec.description || "Path 路徑參數",
      placeholder: String(spec.example ?? examplePathParams[name] ?? ""),
      initial,
      dataKey: "pathParam",
    }));
  }
}

function renderQueryParamFields(ep) {
  const section = document.getElementById("query-params-section");
  const container = document.getElementById("query-params-fields");
  container.innerHTML = "";
  const specs = ep.queryParamSpecs || {};
  const examples = ep.query || {};
  const names = Array.from(new Set([...Object.keys(specs), ...Object.keys(examples)]));
  if (names.length === 0) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  for (const name of names) {
    const spec = specs[name] || {};
    const initial = examples[name] !== undefined ? String(examples[name]) : "";
    container.appendChild(buildParamField({
      name,
      description: spec.description || "查詢參數",
      placeholder: spec.example !== undefined ? String(spec.example) : "",
      initial,
      dataKey: "queryParam",
      options: spec.options,
      type: spec.type,
    }));
  }
}

function buildParamField({ name, description, placeholder, initial, dataKey, options, type }) {
  const field = document.createElement("div");
  field.className = "param-field";

  const labelBox = document.createElement("div");
  labelBox.className = "param-field-label";
  const nameEl = document.createElement("div");
  nameEl.className = "param-field-name";
  nameEl.textContent = name;
  labelBox.appendChild(nameEl);
  if (description) {
    const descEl = document.createElement("div");
    descEl.className = "param-field-desc";
    descEl.textContent = description;
    labelBox.appendChild(descEl);
  }

  const inputBox = document.createElement("div");
  inputBox.className = "param-field-input";

  let input;
  if (options && Array.isArray(options) && options.length > 0) {
    input = document.createElement("select");
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "(不送)";
    input.appendChild(empty);
    for (const opt of options) {
      const o = document.createElement("option");
      o.value = String(opt);
      o.textContent = String(opt);
      input.appendChild(o);
    }
    if (initial) input.value = initial;
  } else {
    input = document.createElement("input");
    input.type = type === "number" ? "number" : "text";
    input.placeholder = placeholder || "";
    input.value = initial || "";
  }
  input.dataset[dataKey] = name;
  inputBox.appendChild(input);

  field.appendChild(labelBox);
  field.appendChild(inputBox);
  return field;
}

function collectFieldValues(dataKey) {
  const out = {};
  for (const input of document.querySelectorAll(`[data-${dataKey === "pathParam" ? "path-param" : "query-param"}]`)) {
    const value = input.value.trim();
    if (value === "") continue;
    const name = input.dataset[dataKey];
    out[name] = value;
  }
  return out;
}

function renderResponse(status, durationMs, requestId, body) {
  const statusEl = document.getElementById("resp-status");
  statusEl.textContent = status;
  statusEl.className = `status s${Math.floor(status / 100)}xx`;
  document.getElementById("resp-duration").textContent = `${durationMs}ms`;
  document.getElementById("resp-req-id").textContent = requestId ? `req: ${requestId}` : "";
  document.getElementById("response-body").textContent = typeof body === "string" ? body : JSON.stringify(body, null, 2);

  const banner = document.getElementById("response-error-banner");
  if (status >= 400 && body && body.error) {
    banner.textContent = `${body.error.code}: ${body.error.message}`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function renderHistory() {
  const list = document.getElementById("history-list");
  list.innerHTML = "";
  for (const item of state.history) {
    const li = document.createElement("li");
    li.className = "history-item";
    li.innerHTML = `
      <span class="hi-status s${Math.floor(item.status / 100)}xx">${item.status}</span>
      <span class="hi-method">${item.method}</span>
      <span class="hi-path">${item.path}</span>
    `;
    li.addEventListener("click", () => {
      renderResponse(item.status, item.duration, item.requestId, item.body);
    });
    list.appendChild(li);
  }
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------

function safeParseJson(str, fallback) {
  if (!str) return fallback;
  try {
    return JSON.parse(str);
  } catch (e) {
    throw new Error(`invalid JSON: ${e.message}`);
  }
}

function substitutePathParams(path, params) {
  let out = path;
  for (const [k, v] of Object.entries(params || {})) {
    out = out.replace(`{${k}}`, encodeURIComponent(v));
  }
  return out;
}

function buildHeaders(extra) {
  const headers = { "Content-Type": "application/json", ...extra };
  const uuid = currentUserUuid();
  if (uuid) headers["X-Local-User-Id"] = uuid;
  return headers;
}

export async function sendRequest({ method, path, pathParams, query, headers, body }) {
  const url = new URL(substitutePathParams(path, pathParams), window.location.origin);
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const init = {
    method,
    headers: buildHeaders(headers),
  };
  if (method !== "GET" && method !== "HEAD" && body !== undefined) {
    init.body = typeof body === "string" ? body : JSON.stringify(body);
  }
  const t0 = performance.now();
  const resp = await fetch(url.toString(), init);
  const duration = Math.round(performance.now() - t0);
  const requestId = resp.headers.get("x-request-id") || resp.headers.get("X-Request-Id");
  let parsed;
  const text = await resp.text();
  try {
    parsed = JSON.parse(text);
  } catch (_) {
    parsed = text;
  }
  const record = { status: resp.status, duration, requestId, body: parsed, method, path };
  return record;
}

function pushHistory(record) {
  state.history.unshift(record);
  if (state.history.length > MAX_HISTORY) state.history.length = MAX_HISTORY;
  renderHistory();
}

async function onSend() {
  if (!state.selected) return;
  const ep = state.selected;
  let headers, body;
  const pathParams = collectFieldValues("pathParam");
  const query = collectFieldValues("queryParam");
  try {
    headers = safeParseJson(document.getElementById("req-headers").value, {});
    const bodyText = document.getElementById("req-body").value.trim();
    body = bodyText ? safeParseJson(bodyText) : undefined;
  } catch (e) {
    renderResponse(0, 0, null, `client error: ${e.message}`);
    return;
  }
  const btn = document.getElementById("send-btn");
  btn.disabled = true;
  try {
    // remember the request shape so Copy-as-cURL can reproduce it
    state.lastRequest = { method: ep.method, path: ep.path, pathParams, query, headers, body };
    const record = await sendRequest(state.lastRequest);
    renderResponse(record.status, record.duration, record.requestId, record.body);
    pushHistory(record);
    enableResponseActions();
  } catch (e) {
    renderResponse(0, 0, null, `network error: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

function enableResponseActions() {
  document.getElementById("copy-curl-btn").disabled = false;
  document.getElementById("copy-body-btn").disabled = false;
}

function buildCurl(req) {
  if (!req) return "";
  const url = new URL(substitutePathParams(req.path, req.pathParams || {}), window.location.origin);
  for (const [k, v] of Object.entries(req.query || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const headers = { "Content-Type": "application/json", ...(req.headers || {}) };
  const uuid = currentUserUuid();
  if (uuid) headers["X-Local-User-Id"] = uuid;

  const parts = [`curl -X ${req.method}`, `'${url.toString()}'`];
  for (const [k, v] of Object.entries(headers)) {
    parts.push(`\\\n  -H '${k}: ${String(v).replace(/'/g, "\\'")}'`);
  }
  if (req.method !== "GET" && req.method !== "HEAD" && req.body !== undefined) {
    const bodyStr = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
    parts.push(`\\\n  -d '${bodyStr.replace(/'/g, "\\'")}'`);
  }
  return parts.join(" ");
}

async function copyToClipboard(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    const originalText = btn.textContent;
    btn.textContent = "已複製";
    btn.classList.add("copied");
    setTimeout(() => {
      btn.textContent = originalText;
      btn.classList.remove("copied");
    }, 1500);
  } catch (e) {
    console.warn("clipboard write failed:", e);
    alert("複製失敗，請手動 select");
  }
}

function onExampleSelected(ev) {
  if (!state.selected || !state.selected.examples) return;
  const name = ev.target.value;
  if (!name) return;
  document.getElementById("req-body").value = JSON.stringify(state.selected.examples[name], null, 2);
  ev.target.value = "";
}

// ---------------------------------------------------------------------------
// Demo flows
// ---------------------------------------------------------------------------

function demoLog(line, klass = "") {
  const pre = document.getElementById("demo-log");
  const ts = new Date().toLocaleTimeString();
  pre.textContent += `\n[${ts}] ${line}`;
  if (klass) {
    // Simple class-on-last-line trick: wrap the line in a span via innerHTML.
    // Plain pre keeps formatting; styling only on selected lines.
  }
  pre.scrollTop = pre.scrollHeight;
}

function resetDemoLog() {
  document.getElementById("demo-log").textContent = "(demo running…)";
}

async function demoBuyPriceAlert() {
  resetDemoLog();
  demoLog("step 1: create buy_price_alert intent 2330 @600");
  const create = await sendRequest({
    method: "POST",
    path: "/trade-intents",
    body: { strategy: "buy_price_alert", symbol: "2330", quantityLots: 1, targetPrice: "600" },
  });
  pushHistory(create);
  if (create.status !== 201) {
    demoLog(`  → ${create.status} ${JSON.stringify(create.body)}`, "err");
    return;
  }
  const intentId = create.body.data.id;
  demoLog(`  → 201 active intent ${intentId}`, "ok");

  demoLog("step 2: push quote ask=599 (will trigger)");
  const push = await sendRequest({
    method: "POST",
    path: "/dev/push-quote",
    body: { symbol: "2330", askPrice: "599" },
  });
  pushHistory(push);
  demoLog(`  → ${push.status}`, push.status === 200 ? "ok" : "err");

  demoLog("step 3: poll notifications");
  const notif = await sendRequest({ method: "GET", path: "/notifications", query: { unreadOnly: true, pageSize: 10 } });
  pushHistory(notif);
  const count = notif.body?.data?.length ?? 0;
  demoLog(`  → ${notif.status} ${count} unread notification(s)`, count > 0 ? "ok" : "warn");
}

async function demoLimitBuyOrder() {
  resetDemoLog();
  demoLog("step 1: create limit_buy_order intent 2330 @600 qty 2");
  const create = await sendRequest({
    method: "POST",
    path: "/trade-intents",
    body: {
      strategy: "limit_buy_order",
      symbol: "2330",
      quantityLots: 2,
      targetPrice: "600",
      transactionMode: "partial_fill_allowed",
    },
  });
  pushHistory(create);
  if (create.status !== 201) {
    demoLog(`  → ${create.status} ${JSON.stringify(create.body)}`, "err");
    return;
  }
  demoLog(`  → 201 intent ${create.body.data.id}, filledQuantityLots=${create.body.data.filledQuantityLots}`, "ok");

  demoLog("step 2: push quote ask=599");
  const push = await sendRequest({
    method: "POST",
    path: "/dev/push-quote",
    body: { symbol: "2330", askPrice: "599" },
  });
  pushHistory(push);
  demoLog(`  → ${push.status}`, push.status === 200 ? "ok" : "err");

  demoLog("step 3: poll notifications");
  const notif = await sendRequest({ method: "GET", path: "/notifications", query: { unreadOnly: true, pageSize: 10 } });
  pushHistory(notif);
  const lim = notif.body?.data?.find((n) => n.type === "limit_order_triggered");
  if (lim) {
    demoLog(`  → 200 limit_order_triggered: "${lim.renderedTitle}"`, "ok");
  } else {
    demoLog(`  → 200 but no limit_order_triggered notification found`, "warn");
  }
}

async function demoMultiUser() {
  resetDemoLog();
  const initial = getSelectedUser();
  try {
    setSelectedUser("alice");
    demoLog("user=alice: create intent 2330 @600");
    const create = await sendRequest({
      method: "POST",
      path: "/trade-intents",
      body: { strategy: "buy_price_alert", symbol: "2330", quantityLots: 1, targetPrice: "600" },
    });
    pushHistory(create);
    if (create.status !== 201) {
      demoLog(`  → ${create.status} ${JSON.stringify(create.body)}`, "err");
      return;
    }
    demoLog(`  → 201 alice intent ${create.body.data.id}`, "ok");

    setSelectedUser("bob");
    demoLog("user=bob: list intents (should not see alice's)");
    const bobList = await sendRequest({ method: "GET", path: "/trade-intents" });
    pushHistory(bobList);
    const bobCount = bobList.body?.data?.length ?? 0;
    demoLog(`  → ${bobList.status} bob sees ${bobCount} intents`, bobCount === 0 ? "ok" : "err");

    setSelectedUser("alice");
    demoLog("user=alice: list intents (should see the new one)");
    const aliceList = await sendRequest({ method: "GET", path: "/trade-intents" });
    pushHistory(aliceList);
    const found = (aliceList.body?.data || []).some((i) => i.id === create.body.data.id);
    demoLog(`  → ${aliceList.status} alice ${found ? "sees" : "does NOT see"} the intent`, found ? "ok" : "err");
  } finally {
    setSelectedUser(initial);
    document.getElementById("user-select").value = initial;
  }
}

const DEMOS = {
  buy_price_alert: demoBuyPriceAlert,
  limit_buy_order: demoLimitBuyOrder,
  multi_user: demoMultiUser,
};

// ---------------------------------------------------------------------------
// Server state (v3)
// ---------------------------------------------------------------------------

async function refreshServerState() {
  try {
    const resp = await fetch("/dev/server-state", { headers: buildHeaders({}) });
    if (!resp.ok) {
      document.getElementById("clock-display").textContent = "system (state n/a)";
      return;
    }
    const body = await resp.json();
    const clock = body?.data?.clock;
    const display = clock
      ? `${clock.currentTaipei}${clock.isFrozen ? " 🧊" : ""}${clock.withinRegularSession ? " 盤中" : " 盤外"}`
      : "system";
    document.getElementById("clock-display").textContent = display;
  } catch (_) {
    document.getElementById("clock-display").textContent = "system";
  }
}

// ---------------------------------------------------------------------------
// OpenAPI sanity check (v4)
// ---------------------------------------------------------------------------

async function openApiSanityCheck() {
  try {
    const resp = await fetch("/openapi.json");
    if (!resp.ok) return;
    const spec = await resp.json();
    const known = new Set();
    for (const [path, ops] of Object.entries(spec.paths || {})) {
      for (const m of Object.keys(ops)) known.add(`${m.toUpperCase()} ${path}`);
    }
    for (const ep of ENDPOINTS) {
      if (!ep.implemented) continue;
      // Skip path-parameter endpoints; openapi uses `{intent_id}` matching ours.
      const key = `${ep.method} ${ep.path}`;
      if (!known.has(key)) {
        console.warn(`[endpoints.js] ${key} marked implemented but missing from /openapi.json`);
      }
    }
  } catch (e) {
    console.warn("[endpoints.js] /openapi.json sanity check failed:", e.message);
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function init() {
  document.getElementById("base-url").textContent = window.location.origin;
  renderUserSelect();
  renderEndpointList();
  document.getElementById("endpoint-filter").addEventListener("input", (e) => {
    renderEndpointList(e.target.value);
  });
  document.getElementById("send-btn").addEventListener("click", onSend);
  document.getElementById("example-select").addEventListener("change", onExampleSelected);
  document.getElementById("refresh-state-btn").addEventListener("click", refreshServerState);
  document.getElementById("reset-all-btn").addEventListener("click", async () => {
    state.history = [];
    renderHistory();
    document.getElementById("demo-log").textContent = "(reset)";
    try {
      await fetch("/dev/set-clock", { method: "POST", headers: buildHeaders({}), body: "{}" });
    } catch (_) {}
    refreshServerState();
  });
  for (const btn of document.querySelectorAll("#demo-buttons button")) {
    btn.addEventListener("click", () => {
      const fn = DEMOS[btn.dataset.demo];
      if (fn) fn();
    });
  }
  refreshServerState();
  openApiSanityCheck();
  initUserViewMode();
  initResponseActions();
  initGlobalKeyboardShortcuts();
}

function initResponseActions() {
  document.getElementById("copy-body-btn").addEventListener("click", (ev) => {
    const body = document.getElementById("response-body").textContent;
    copyToClipboard(body, ev.target);
  });
  document.getElementById("copy-curl-btn").addEventListener("click", (ev) => {
    const curl = buildCurl(state.lastRequest);
    if (!curl) return;
    copyToClipboard(curl, ev.target);
  });
}

function initGlobalKeyboardShortcuts() {
  let userView = null;
  // user-view module loaded lazily; capture reference once available
  import("/test-assets/user-view.js").then((m) => {
    userView = m;
  });
  window.addEventListener("keydown", (ev) => {
    // Esc — close any open modal (dev pane has no modal; user view has sheets)
    if (ev.key === "Escape") {
      if (userView?.closeAllModals?.()) ev.preventDefault();
      return;
    }
    // Cmd/Ctrl + Enter — send request in Dev mode
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
      const mode = document.getElementById("app").dataset.mode;
      if (mode === "dev" && state.selected && !document.getElementById("send-btn").disabled) {
        ev.preventDefault();
        onSend();
      }
    }
  });
}

// ---------------------------------------------------------------------------
// User-view mode integration
// ---------------------------------------------------------------------------

async function initUserViewMode() {
  // dynamic import keeps Dev-only sessions from paying the parse cost
  const uv = await import("/test-assets/user-view.js");
  uv.initUserView();

  const savedMode = localStorage.getItem("ai-stock-test-mode") || "dev";
  applyMode(savedMode, uv);

  for (const btn of document.querySelectorAll(".mode-toggle button")) {
    btn.addEventListener("click", () => applyMode(btn.dataset.mode, uv));
  }
}

function applyMode(mode, uv) {
  const app = document.getElementById("app");
  app.dataset.mode = mode;
  localStorage.setItem("ai-stock-test-mode", mode);
  for (const btn of document.querySelectorAll(".mode-toggle button")) {
    const isActive = btn.dataset.mode === mode;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
  }
  if (mode === "user") uv.onEnterUserMode();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
