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
  const prev = localStorage.getItem(SELECTED_USER_KEY);
  localStorage.setItem(SELECTED_USER_KEY, label);
  if (prev !== label) {
    window.dispatchEvent(new CustomEvent("ai-stock:user-changed", { detail: { from: prev, to: label } }));
  }
}

function currentUserUuid() {
  const label = getSelectedUser();
  return label === "default" ? null : USERS[label];
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

const endpointFilter = {
  text: "",
  method: "ALL",
  onlyAvailable: false,
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function highlightMatch(text, needle) {
  const safe = escapeHtml(text);
  if (!needle) return safe;
  const idx = text.toLowerCase().indexOf(needle.toLowerCase());
  if (idx < 0) return safe;
  const end = idx + needle.length;
  return (
    escapeHtml(text.slice(0, idx)) +
    `<mark class="endpoint-match">${escapeHtml(text.slice(idx, end))}</mark>` +
    escapeHtml(text.slice(end))
  );
}

function renderEndpointList() {
  const container = document.getElementById("endpoint-list");
  container.innerHTML = "";

  const needle = endpointFilter.text.trim();
  const total = ENDPOINTS.length;
  const groups = {};
  let matched = 0;

  for (const ep of ENDPOINTS) {
    if (endpointFilter.method !== "ALL" && ep.method !== endpointFilter.method) continue;
    if (endpointFilter.onlyAvailable && !ep.implemented) continue;
    if (needle) {
      const haystack = `${ep.method} ${ep.path} ${ep.label || ""}`.toLowerCase();
      if (!haystack.includes(needle.toLowerCase())) continue;
    }
    (groups[ep.group] ??= []).push(ep);
    matched += 1;
  }

  const countEl = document.getElementById("endpoint-count");
  if (countEl) countEl.textContent = `${matched} / ${total}`;

  if (matched === 0) {
    const empty = document.createElement("div");
    empty.className = "endpoint-list-empty";
    empty.innerHTML = `
      <div class="endpoint-list-empty-title">沒有符合的 endpoint</div>
      <div class="endpoint-list-empty-hint">試著放寬條件，或按 Esc 清除搜尋</div>
    `;
    container.appendChild(empty);
    return;
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
      div.dataset.endpointMethod = ep.method;
      div.dataset.endpointPath = ep.path;
      const flag = ep.implemented
        ? `<span class="endpoint-status">✓</span>`
        : `<span class="endpoint-status">○</span>`;
      div.innerHTML = `
        <span class="endpoint-method ${ep.method}">${ep.method}</span>
        <span class="endpoint-path">${highlightMatch(ep.path, needle)}</span>
        ${ep.ticket ? `<span class="endpoint-ticket">${escapeHtml(ep.ticket)}</span>` : ""}
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

const USER_DISPLAY_NAME = {
  default: "預設使用者",
  alice: "Alice",
  bob: "Bob",
  charlie: "Charlie",
};

const USER_PALETTE = [
  { bg: "rgba(0, 122, 255, 0.18)", color: "#0064D2" },
  { bg: "rgba(52, 199, 89, 0.22)", color: "#1F8A3F" },
  { bg: "rgba(255, 149, 0, 0.22)", color: "#B26200" },
  { bg: "rgba(175, 82, 222, 0.20)", color: "#7A2EA0" },
];

function userPaletteOf(label) {
  if (label === "default") {
    return { bg: "rgba(120, 120, 128, 0.16)", color: "var(--text-secondary)" };
  }
  const labels = Object.keys(USERS);
  const idx = Math.max(0, labels.indexOf(label));
  return USER_PALETTE[idx % USER_PALETTE.length];
}

export function attachPopover(trigger, menu, opts = {}) {
  const { align = "right", gap = 8, margin = 12 } = opts;
  // ancestors with backdrop-filter / transform / filter trap position:fixed children.
  // Reparent the menu to body so it can break out and overlay the page freely.
  if (menu.parentElement !== document.body) document.body.appendChild(menu);

  const position = () => {
    if (menu.hidden) return;
    const rect = trigger.getBoundingClientRect();
    const menuWidth = menu.offsetWidth || 240;
    let left = align === "right" ? rect.right - menuWidth : rect.left;
    if (left < margin) left = margin;
    if (left + menuWidth > window.innerWidth - margin) {
      left = window.innerWidth - menuWidth - margin;
    }
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(rect.bottom + gap)}px`;
  };

  const open = () => {
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    position();
    window.addEventListener("scroll", position, true);
    window.addEventListener("resize", position);
  };
  const close = () => {
    if (menu.hidden) return;
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    window.removeEventListener("scroll", position, true);
    window.removeEventListener("resize", position);
  };
  const toggle = () => (menu.hidden ? open() : close());

  trigger.addEventListener("click", (ev) => {
    ev.stopPropagation();
    toggle();
  });
  document.addEventListener("click", (ev) => {
    if (!trigger.contains(ev.target) && !menu.contains(ev.target)) close();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !menu.hidden) {
      ev.preventDefault();
      close();
      trigger.focus();
    }
  });

  return { open, close, toggle, position };
}

export function renderIdentityMenu(menu, onSelect) {
  menu.innerHTML = "";
  const all = [["default", null], ...Object.entries(USERS)];
  for (const [label, uuid] of all) {
    const pal = userPaletteOf(label);
    const item = document.createElement("button");
    item.type = "button";
    item.className = "identity-menu-item";
    item.dataset.userLabel = label;
    item.setAttribute("role", "option");
    item.innerHTML = `
      <span class="identity-menu-avatar" style="background:${pal.bg};color:${pal.color}">${(label[0] || "D").toUpperCase()}</span>
      <span class="identity-menu-text">
        <span class="identity-menu-name">${escapeHtml(USER_DISPLAY_NAME[label] || label)}</span>
        <span class="identity-menu-uuid">${uuid ? escapeHtml(uuid.slice(0, 8)) + "…" : "不送 X-Local-User-Id"}</span>
      </span>
      <span class="identity-menu-check" aria-hidden="true">
        <svg viewBox="0 0 16 16"><polyline points="3 8.5 6.5 12 13 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </span>
    `;
    item.addEventListener("click", () => onSelect(label));
    menu.appendChild(item);
  }
}

function attachIdentityMenuKeyboard(trigger, menu, popover) {
  trigger.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      popover.open();
      menu.querySelector(".identity-menu-item.active, .identity-menu-item")?.focus();
    }
  });
  menu.addEventListener("keydown", (ev) => {
    const items = Array.from(menu.querySelectorAll(".identity-menu-item"));
    const idx = items.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      items[(idx + 1) % items.length]?.focus();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      items[(idx - 1 + items.length) % items.length]?.focus();
    }
  });
}

function renderUserSwitcher() {
  const menu = document.getElementById("dev-user-menu");
  const trigger = document.getElementById("dev-user-trigger");
  const popover = attachPopover(trigger, menu, { align: "right" });
  renderIdentityMenu(menu, (label) => {
    setSelectedUser(label);
    syncDevUserSwitcher();
    popover.close();
  });
  attachIdentityMenuKeyboard(trigger, menu, popover);
  syncDevUserSwitcher();
}

export function syncDevUserSwitcher() {
  const label = getSelectedUser();
  const name = USER_DISPLAY_NAME[label] || label;
  const pal = userPaletteOf(label);
  updateUserAvatars();
  for (const avatarId of ["dev-user-avatar", "user-mode-avatar"]) {
    const av = document.getElementById(avatarId);
    if (av) {
      av.style.background = pal.bg;
      av.style.color = pal.color;
    }
  }
  const devLabel = document.getElementById("dev-user-label");
  if (devLabel) devLabel.textContent = name;
  const greeting = document.getElementById("user-greeting");
  if (greeting) greeting.textContent = name;
  for (const item of document.querySelectorAll(".identity-menu-item")) {
    const on = item.dataset.userLabel === label;
    item.classList.toggle("active", on);
    item.setAttribute("aria-selected", on ? "true" : "false");
  }
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

  renderExampleMenu(ep);
}

let examplePopover = null;

function renderExampleMenu(ep) {
  const trigger = document.getElementById("example-trigger");
  const menu = document.getElementById("example-menu");
  if (!examplePopover) examplePopover = attachPopover(trigger, menu, { align: "right" });

  menu.innerHTML = "";
  const names = ep?.examples ? Object.keys(ep.examples) : [];
  trigger.hidden = names.length === 0;
  if (names.length === 0) {
    examplePopover.close();
    return;
  }

  for (const name of names) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "popover-menu-item example-menu-item";
    item.dataset.exampleName = name;
    item.setAttribute("role", "option");
    item.innerHTML = `
      <span class="popover-menu-text">
        <span class="popover-menu-name">${escapeHtml(name)}</span>
      </span>
    `;
    item.addEventListener("click", () => {
      if (!state.selected || !state.selected.examples) return;
      document.getElementById("req-body").value = JSON.stringify(state.selected.examples[name], null, 2);
      examplePopover.close();
    });
    menu.appendChild(item);
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

export async function sendRequest({ method, path, pathParams, query, headers, body, signal }) {
  const url = new URL(substitutePathParams(path, pathParams), window.location.origin);
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const init = {
    method,
    headers: buildHeaders(headers),
  };
  if (signal) init.signal = signal;
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

// ---------------------------------------------------------------------------
// Demo flows
// ---------------------------------------------------------------------------

function demoLog(line) {
  const pre = document.getElementById("demo-log");
  const ts = new Date().toLocaleTimeString();
  pre.textContent += `\n[${ts}] ${line}`;
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
    syncDevUserSwitcher();
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
  await Promise.all([pollServerHealth(), pollClockState()]);
  updateUserAvatars();
}

async function pollServerHealth() {
  const dot = document.getElementById("server-status-dot");
  const label = document.getElementById("server-status-label");
  if (!dot || !label) return;
  try {
    const resp = await fetch("/health", { headers: { "Content-Type": "application/json" } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const body = await resp.json();
    const dbOk = body?.data?.database === "ok";
    dot.dataset.status = dbOk ? "ok" : "degraded";
    label.textContent = dbOk ? "服務正常" : "DB 連線異常";
  } catch (_) {
    dot.dataset.status = "down";
    label.textContent = "無法連線";
  }
}

async function pollClockState() {
  const display = document.getElementById("clock-display");
  if (!display) return;
  try {
    const resp = await fetch("/dev/server-state", { headers: buildHeaders({}) });
    if (!resp.ok) {
      display.textContent = "系統時間";
      return;
    }
    const body = await resp.json();
    const clock = body?.data?.clock;
    if (!clock) {
      display.textContent = "系統時間";
      return;
    }
    const t = formatTaipeiTime(clock.currentTaipei);
    const session = clock.withinRegularSession ? "盤中" : "盤外";
    const frozen = clock.isFrozen ? "凍結 · " : "";
    display.textContent = `${frozen}${t} · ${session}`;
  } catch (_) {
    display.textContent = "系統時間";
  }
}

function formatTaipeiTime(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-TW", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Taipei",
    });
  } catch (_) {
    return "系統時間";
  }
}

function updateUserAvatars() {
  const label = getSelectedUser();
  const initial = label === "default" ? "D" : (label[0] || "?").toUpperCase();
  const devAvatar = document.getElementById("dev-user-avatar");
  const userAvatar = document.getElementById("user-mode-avatar");
  if (devAvatar) devAvatar.textContent = initial;
  if (userAvatar) userAvatar.textContent = initial;
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
  // Base URL 已從 topbar 移除（資訊密度精簡）；保留註解標示位置。
  renderUserSwitcher();
  renderEndpointList();
  initEndpointFilterControls();
  document.getElementById("send-btn").addEventListener("click", onSend);
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

export function attachSegmentedIndicator(group, opts = {}) {
  const { activeSelector = "button.active" } = opts;
  let indicator = group.querySelector(":scope > .seg-indicator");
  if (!indicator) {
    indicator = document.createElement("span");
    indicator.className = "seg-indicator";
    indicator.setAttribute("aria-hidden", "true");
    group.prepend(indicator);
  }
  const reposition = () => {
    const active = group.querySelector(activeSelector);
    if (!active) {
      indicator.style.opacity = "0";
      return;
    }
    indicator.style.opacity = "1";
    indicator.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
    indicator.style.width = `${active.offsetWidth}px`;
    indicator.style.height = `${active.offsetHeight}px`;
  };
  requestAnimationFrame(reposition);
  window.addEventListener("resize", reposition);
  return reposition;
}

function initEndpointFilterControls() {
  const input = document.getElementById("endpoint-filter");
  const clear = document.getElementById("endpoint-filter-clear");
  const chips = document.getElementById("endpoint-method-chips");
  const onlyBtn = document.getElementById("endpoint-only-available");

  const syncClear = () => {
    clear.hidden = !input.value;
  };

  input.addEventListener("input", () => {
    endpointFilter.text = input.value;
    syncClear();
    renderEndpointList();
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && input.value) {
      ev.preventDefault();
      input.value = "";
      endpointFilter.text = "";
      syncClear();
      renderEndpointList();
    }
  });
  clear.addEventListener("click", () => {
    input.value = "";
    endpointFilter.text = "";
    syncClear();
    renderEndpointList();
    input.focus();
  });

  const repositionMethodIndicator = attachSegmentedIndicator(chips);
  for (const btn of chips.querySelectorAll("button")) {
    btn.addEventListener("click", () => {
      endpointFilter.method = btn.dataset.method;
      for (const b of chips.querySelectorAll("button")) {
        const on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      }
      repositionMethodIndicator();
      renderEndpointList();
    });
  }

  onlyBtn.addEventListener("click", () => {
    endpointFilter.onlyAvailable = !endpointFilter.onlyAvailable;
    onlyBtn.classList.toggle("active", endpointFilter.onlyAvailable);
    onlyBtn.setAttribute("aria-pressed", endpointFilter.onlyAvailable ? "true" : "false");
    renderEndpointList();
  });
}

function initGlobalKeyboardShortcuts() {
  let userView = null;
  // user-view module loaded lazily; capture reference once available
  import("/test-assets/user-view.js").then((m) => {
    userView = m;
  });
  window.addEventListener("keydown", (ev) => {
    const tag = (ev.target?.tagName || "").toLowerCase();
    const inEditable = tag === "input" || tag === "textarea" || tag === "select" || ev.target?.isContentEditable;

    // "/" — focus endpoint filter (Dev mode), ignoring presses inside text fields
    if (ev.key === "/" && !inEditable && !ev.metaKey && !ev.ctrlKey && !ev.altKey) {
      const mode = document.getElementById("app").dataset.mode;
      if (mode === "dev") {
        const input = document.getElementById("endpoint-filter");
        if (input) {
          ev.preventDefault();
          input.focus();
          // place caret at end without selecting existing text
          const len = input.value.length;
          input.setSelectionRange(len, len);
          return;
        }
      }
    }
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

let repositionModeIndicator = () => {};

async function initUserViewMode() {
  // dynamic import keeps Dev-only sessions from paying the parse cost
  const [uv, sv] = await Promise.all([
    import("/test-assets/user-view.js"),
    import("/test-assets/service-view.js"),
  ]);
  uv.initUserView();
  sv.initServiceView();

  repositionModeIndicator = attachSegmentedIndicator(document.querySelector(".mode-toggle"));

  const savedMode = localStorage.getItem("ai-stock-test-mode") || "dev";
  applyMode(savedMode, uv, sv);

  for (const btn of document.querySelectorAll(".mode-toggle button")) {
    btn.addEventListener("click", () => applyMode(btn.dataset.mode, uv, sv));
  }
}

function applyMode(mode, uv, sv) {
  const app = document.getElementById("app");
  app.dataset.mode = mode;
  localStorage.setItem("ai-stock-test-mode", mode);
  for (const btn of document.querySelectorAll(".mode-toggle button")) {
    const isActive = btn.dataset.mode === mode;
    btn.classList.toggle("active", isActive);
    btn.setAttribute("aria-selected", String(isActive));
  }
  repositionModeIndicator();
  if (mode !== "user") uv.onLeaveUserMode?.();
  if (mode === "user") uv.onEnterUserMode();
  if (mode === "service") sv.onEnterServiceMode();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
