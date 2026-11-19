/* Prüfplanung DGUV V3 – Smartphone-Oberfläche (ohne Framework). */
"use strict";

const ICONS = { open: "○", partial: "◐", appointment: "📅", clarify: "?", done: "✔" };
const ORDER = ["open", "partial", "clarify", "appointment", "done"];
const CLARIFY_QUICK = ["Raum verschlossen", "Mitarbeiter abwesend", "Gerät in Benutzung / nicht abschaltbar",
  "Zugang nur mit Begleitung", "Gerät defekt – Rücksprache"];
const POLL_MS = 15000;
const LS = { token: "pp.token", user: "pp.user", plan: "pp.plan", queue: "pp.queue", mine: "pp.mine" };

const state = {
  token: null, user: null, statuses: {}, plans: [], planId: null, plan: null, areas: [],
  selected: null, view: "plan", listStatus: "", online: true, sheetOpen: false,
};

// ---------------------------------------------------------------- Hilfen
const $ = (sel) => document.querySelector(sel);
function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "style") Object.assign(n.style, v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) n.append(c);
  return n;
}
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}
function lsGet(k, def = null) { try { const v = localStorage.getItem(k); return v === null ? def : JSON.parse(v); } catch { return def; } }
function lsSet(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch { /* privat */ } }
function fmtDate(iso) { if (!iso) return ""; const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}.${m}.${y}`; }
function fmtDateTime(iso) { if (!iso) return ""; return `${fmtDate(iso)} ${iso.slice(11, 16)}`; }
function todayIso(offset = 0) { const d = new Date(); d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function st(key) { return state.statuses[key] || { label: key, fill: "#eee", border: "#999" }; }
function statusText(a) {
  let t = st(a.status).label;
  if (a.status === "partial") t += ` (${a.progress} %)`;
  if (a.status === "appointment" && a.date) t += ` ${fmtDate(a.date)}`;
  return t;
}
function mayChange(a) { return state.user.role === "admin" || !a.inspector_id || a.inspector_id === state.user.id; }

// ---------------------------------------------------------------- API
class ApiErr extends Error { constructor(status, msg, body) { super(msg); this.status = status; this.body = body; } }

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method, cache: "no-store",
      headers: { "Content-Type": "application/json", ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    setOnline(false);
    throw new ApiErr(0, "Keine Verbindung zum Server");
  }
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login") { logout("Bitte erneut anmelden."); throw new ApiErr(401, data.error); }
  if (res.status === 503) { setOnline(false, true); throw new ApiErr(503, data.error || "Datenbank nicht erreichbar"); }
  setOnline(true);
  if (!res.ok) throw new ApiErr(res.status, data.error || `Fehler ${res.status}`, data);
  return data;
}

function setOnline(ok, dbError = false) {
  state.online = ok;
  const s = $("#sync");
  s.classList.toggle("offline", !ok && !dbError);
  s.classList.toggle("error", dbError);
  $("#sync-text").textContent = ok ? "" : (dbError ? "DB-Fehler" : "offline");
  renderQueueBanner();
}

// ---------------------------------------------------------------- Offline-Warteschlange
function queue() { return lsGet(LS.queue, []); }
function renderQueueBanner() {
  const q = queue();
  const b = $("#offline-banner");
  if (q.length) {
    b.hidden = false;
    b.textContent = `${q.length} Änderung${q.length > 1 ? "en" : ""} noch nicht übertragen – wird automatisch nachgeholt.`;
  } else if (!state.online) {
    b.hidden = false;
    b.textContent = "Keine Verbindung – angezeigt wird der letzte Stand.";
  } else b.hidden = true;
}
let flushing = false;
async function flushQueue() {
  if (flushing) return;                 // nie doppelt übertragen (online-Ereignis + Abfrage)
  const q = queue();
  if (!q.length) return;
  flushing = true;
  try { await flushItems(q); } finally { flushing = false; }
}
async function flushItems(q) {
  const failed = [];
  for (const item of q) {
    try {
      await api(`/api/areas/${item.id}/status`, { method: "POST", body: item.body });
    } catch (e) {
      if (e.status === 0 || e.status === 503) { failed.push(item); continue; }
      toast(`„${item.name}“ nicht übertragen: ${e.message}`);
    }
  }
  lsSet(LS.queue, failed);
  renderQueueBanner();
  if (failed.length < q.length) refresh();
}

// ---------------------------------------------------------------- Anmeldung
async function login(ev) {
  ev.preventDefault();
  const f = ev.target;
  $("#login-error").textContent = "";
  const btn = f.querySelector("button");
  btn.disabled = true;
  try {
    const r = await api("/api/login", { method: "POST", body: { username: f.username.value.trim(), password: f.password.value } });
    state.token = r.token; state.user = r.user;
    lsSet(LS.token, r.token); lsSet(LS.user, r.user);
    f.password.value = "";
    await start();
  } catch (e) {
    $("#login-error").textContent = e.message;
  } finally { btn.disabled = false; }
}

function logout(msg) {
  state.token = null; state.user = null;
  lsSet(LS.token, null); lsSet(LS.user, null);
  closeSheet();
  $("#main").hidden = true; $("#login").hidden = false;
  $("#login-error").textContent = msg || "";
}

async function start() {
  $("#login").hidden = true; $("#main").hidden = false;
  try {
    const me = await api("/api/me");
    state.user = me.user; lsSet(LS.user, me.user);
    for (const s of me.statuses) state.statuses[s.key] = s;
    lsSet("pp.statuses", me.statuses);
    $("#me-name").textContent = me.user.name;
    $("#me-role").textContent = me.user.role === "admin" ? "Administrator" : "Prüfer (DGUV V3)";
    $("#me-company").textContent = me.company || "";
    $("#version").textContent = `Server ${me.server_version}`;
  } catch (e) {
    if (e.status === 401) return;
    for (const s of lsGet("pp.statuses", [])) state.statuses[s.key] = s;
  }
  buildChips();
  state.planId = lsGet(LS.plan);
  await refresh(true);
  flushQueue();
}

// ---------------------------------------------------------------- Daten laden
let refreshing = false;
async function refresh(fit = false) {
  if (!state.token || refreshing) return;
  refreshing = true;
  try {
    const p = await api("/api/plans");
    state.plans = p.plans;
    lsSet("pp.cache.plans", p.plans);
    if (!state.plans.some((x) => x.id === state.planId)) state.planId = state.plans[0]?.id ?? null;
    if (state.planId) {
      const d = await api(`/api/plans/${state.planId}`);
      applyPlan(d, fit);
      lsSet(`pp.cache.plan.${state.planId}`, d);
    } else applyPlan(null, fit);
    if (state.view === "list") await loadList();
    if (state.view === "dates") await loadDates();
    updateBadges();
  } catch (e) {
    if (e.status === 0 || e.status === 503) {
      // letzter bekannter Stand
      state.plans = lsGet("pp.cache.plans", state.plans);
      const cached = state.planId && lsGet(`pp.cache.plan.${state.planId}`);
      if (cached && !state.plan) applyPlan(cached, fit);
    } else if (e.status !== 401) toast(e.message);
  } finally { refreshing = false; }
}

function applyPlan(d, fit) {
  const changed = !state.plan || !d || state.plan.id !== d.plan.id;
  state.plan = d ? d.plan : null;
  state.areas = d ? d.areas : [];
  // lokale (noch nicht übertragene) Änderungen über den Serverstand legen
  for (const item of queue()) {
    const a = state.areas.find((x) => x.id === item.id);
    if (a) Object.assign(a, item.local);
  }
  if (state.planId) lsSet(LS.plan, state.planId);
  $("#plan-title").textContent = state.plan ? state.plan.name : "Kein Grundriss";
  renderSummary();
  renderPlan(fit || changed);
  if (state.selected && state.sheetOpen) {
    const a = state.areas.find((x) => x.id === state.selected);
    if (a && !sheetBusy) renderAreaSheet(a);
  }
  if (changed) loadBackground();
}

async function loadBackground() {
  const plan = state.plan;
  if (bgUrl) { URL.revokeObjectURL(bgUrl); bgUrl = null; }
  if (!plan || !plan.has_background) { renderPlan(false); return; }
  try {
    const res = await fetch(`/api/plans/${plan.id}/background`, { headers: { Authorization: `Bearer ${state.token}` } });
    if (res.ok && state.plan && state.plan.id === plan.id) {
      bgUrl = URL.createObjectURL(await res.blob());
      renderPlan(false);
    }
  } catch { /* ohne Hintergrund weiter */ }
}

// ---------------------------------------------------------------- Zusammenfassung
function renderSummary() {
  const box = $("#plan-summary");
  box.replaceChildren();
  if (!state.plan) return;
  const counts = Object.fromEntries(ORDER.map((k) => [k, 0]));
  for (const a of state.areas) counts[a.status]++;
  const total = state.areas.length;
  const pct = total ? Math.round(state.areas.reduce((s, a) => s + a.progress, 0) / total) : 0;
  const overdue = state.areas.filter((a) => a.overdue).length;
  box.append(
    el("div", { class: "summary-row" },
      el("span", {}, el("strong", {}, `${pct} %`), ` erledigt · ${total} Bereiche`),
      el("span", { class: "muted" }, state.plan.location || "")),
    el("div", { class: "progress" }, el("span", { style: { width: `${pct}%` } })),
    el("div", { class: "counts" },
      ORDER.filter((k) => counts[k]).map((k) => el("span", { class: "count", style: { background: st(k).fill, borderColor: st(k).border } },
        `${ICONS[k]} ${st(k).label}: ${counts[k]}`)),
      overdue ? el("span", { class: "count", style: { borderColor: "#d0271d", color: "#d0271d" } }, `⚠ ${overdue} überfällig`) : null));
}

function updateBadges() {
  // „Meine“: noch nicht erledigte eigene Bereiche im aktuellen Grundriss
  const open = state.areas.filter((a) => a.inspector_id === state.user?.id && a.status !== "done").length;
  const bm = $("#badge-mine"); bm.hidden = !open; bm.textContent = open;
  const due = state.areas.filter((a) => a.inspector_id === state.user?.id && a.status === "appointment"
    && a.date && a.date <= todayIso()).length;
  const bd = $("#badge-dates"); bd.hidden = !due; bd.textContent = due;
}

// ---------------------------------------------------------------- Grundriss (SVG)
let vb = null;           // aktueller Ausschnitt {x,y,w,h} in Metern
let bgUrl = null;
const svgEl = () => $("#plan-svg");

function fitView() {
  if (!state.plan) return;
  const r = svgEl().getBoundingClientRect();
  if (!r.width || !r.height) return;
  // Ränder in Bildpunkten: oben Platz für den Schalter „nur meine“
  const pad = { l: 12, r: 12, t: 56, b: 12 };
  const aw = Math.max(50, r.width - pad.l - pad.r), ah = Math.max(50, r.height - pad.t - pad.b);
  const pw = state.plan.w, ph = state.plan.h;
  const s = Math.max(pw / aw, ph / ah);              // Meter je Bildpunkt
  // waagerecht mittig, senkrecht oben (unter dem Schalter) – so bleibt unten Platz zum Scrollen
  vb = { x: -pad.l * s - (aw * s - pw) / 2, y: -pad.t * s, w: r.width * s, h: r.height * s };
  applyVb();
}
function applyVb() { if (vb) svgEl().setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`); }

function truncate(text, maxChars) {
  return text.length <= maxChars ? text : text.slice(0, Math.max(1, maxChars - 1)) + "…";
}

function renderPlan(fit) {
  const s = svgEl();
  s.replaceChildren();
  $("#plan-empty").hidden = !!state.plan;
  if (!state.plan) {
    $("#plan-empty").textContent = state.plans.length ? "Grundriss wählen" :
      "Noch kein Grundriss vorhanden. Grundrisse werden am PC in ERP Desk angelegt (Prüfplanung → Vorbereitung).";
    return;
  }
  const P = state.plan;
  s.append(svg("rect", { x: 0, y: 0, width: P.w, height: P.h, fill: "#fff" }));
  if (bgUrl) s.append(svg("image", { href: bgUrl, x: 0, y: 0, width: P.w, preserveAspectRatio: "xMinYMin meet", opacity: 0.45 }));
  const grid = svg("g", { stroke: "#e3e7eb", "stroke-width": 0.02 });
  for (let x = 1; x < P.w; x++) grid.append(svg("line", { x1: x, y1: 0, x2: x, y2: P.h }));
  for (let y = 1; y < P.h; y++) grid.append(svg("line", { x1: 0, y1: y, x2: P.w, y2: y }));
  s.append(grid);
  s.append(svg("rect", { x: 0, y: 0, width: P.w, height: P.h, fill: "none", stroke: "#1d2d3e", "stroke-width": 0.08 }));
  const mine = $("#only-mine").checked;
  for (const a of state.areas) {
    const c = st(a.status);
    const cls = ["area", a.overdue ? "overdue" : "", a.id === state.selected ? "selected" : "",
      mine && a.inspector_id && a.inspector_id !== state.user.id ? "dim" : ""].join(" ");
    const g = svg("g", { class: cls, "data-id": a.id });
    g.append(svg("rect", { class: "box", x: a.x + 0.04, y: a.y + 0.04, width: a.w - 0.08, height: a.h - 0.08,
      fill: c.fill, stroke: c.border }));
    if (a.status === "partial") {
      g.append(svg("rect", { x: a.x + 0.15, y: a.y + a.h - 0.3, width: (a.w - 0.3) * a.progress / 100, height: 0.14, fill: c.border }));
    }
    const fs = Math.max(0.28, Math.min(0.75, a.w / 7, a.h / 3.6));
    const maxChars = Math.max(3, Math.floor((a.w - 0.3) / (fs * 0.56)));
    const t1 = svg("text", { x: a.x + 0.18, y: a.y + 0.15 + fs, "font-size": fs, "font-weight": 700 });
    t1.textContent = truncate(`${ICONS[a.status]} ${a.name}`, maxChars);
    g.append(t1);
    const lines = [];
    if (a.status !== "open") lines.push(statusText(a));
    if (a.inspector) lines.push(a.inspector);
    const fs2 = fs * 0.78;
    const room = Math.floor((a.h - fs - 0.4) / (fs2 * 1.2));
    lines.slice(0, Math.max(0, room)).forEach((line, i) => {
      const t = svg("text", { class: "sub", x: a.x + 0.18, y: a.y + 0.2 + fs + (i + 1) * fs2 * 1.25, "font-size": fs2 });
      t.textContent = truncate(line, Math.floor((a.w - 0.3) / (fs2 * 0.56)));
      g.append(t);
    });
    s.append(g);
  }
  if (fit || !vb) requestAnimationFrame(fitView); else applyVb();
}

// Zoomen/Verschieben mit Fingern (Pointer Events) und Tippen auf Räume
const pointers = new Map();
let gesture = null;

function svgPoint(clientX, clientY) {
  const r = svgEl().getBoundingClientRect();
  const scale = Math.max(vb.w / r.width, vb.h / r.height);
  const offX = (r.width * scale - vb.w) / 2, offY = (r.height * scale - vb.h) / 2;
  return { x: vb.x - offX + (clientX - r.left) * scale, y: vb.y - offY + (clientY - r.top) * scale, scale };
}
function zoomAt(factor, cx, cy) {
  if (!vb || !state.plan) return;
  const maxW = (state.plan.w + 1) * 3, minW = 2;
  const newW = Math.min(maxW, Math.max(minW, vb.w / factor));
  factor = vb.w / newW;
  const p = svgPoint(cx, cy);
  vb = { x: p.x - (p.x - vb.x) / factor, y: p.y - (p.y - vb.y) / factor, w: vb.w / factor, h: vb.h / factor };
  applyVb();
}

function onPointerDown(e) {
  if (!vb) return;
  svgEl().setPointerCapture(e.pointerId);
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size === 1) gesture = { tap: true, t: Date.now(), x0: e.clientX, y0: e.clientY, last: { x: e.clientX, y: e.clientY }, target: e.target };
  else { gesture = { tap: false, pinch: pinchInfo() }; }
}
function pinchInfo() {
  const [a, b] = [...pointers.values()];
  return { d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
}
function onPointerMove(e) {
  if (!pointers.has(e.pointerId) || !gesture) return;
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (pointers.size >= 2 && gesture.pinch) {
    const now = pinchInfo();
    if (gesture.pinch.d > 0) zoomAt(now.d / gesture.pinch.d, now.cx, now.cy);
    const p = svgPoint(0, 0);
    vb.x -= (now.cx - gesture.pinch.cx) * p.scale; vb.y -= (now.cy - gesture.pinch.cy) * p.scale;
    applyVb();
    gesture.pinch = now;
  } else if (pointers.size === 1) {
    if (Math.hypot(e.clientX - gesture.x0, e.clientY - gesture.y0) > 8) gesture.tap = false;
    if (!gesture.tap && gesture.last) {
      const p = svgPoint(0, 0);
      vb.x -= (e.clientX - gesture.last.x) * p.scale; vb.y -= (e.clientY - gesture.last.y) * p.scale;
      applyVb();
    }
    gesture.last = { x: e.clientX, y: e.clientY };
  }
}
function onPointerUp(e) {
  if (!pointers.has(e.pointerId)) return;
  pointers.delete(e.pointerId);
  if (gesture && gesture.tap && pointers.size === 0 && Date.now() - gesture.t < 600) {
    const g = gesture.target.closest ? gesture.target.closest(".area") : null;
    if (g) openArea(Number(g.dataset.id));
  }
  if (pointers.size === 0) gesture = null;
  else if (pointers.size === 1) { const [p] = pointers.values(); gesture = { tap: false, last: { ...p }, x0: 0, y0: 0 }; }
}

// ---------------------------------------------------------------- Bottom-Sheet
let sheetBusy = false;
function openSheet(content) {
  const body = $("#sheet-body");
  body.replaceChildren(content);
  $("#sheet").hidden = false; $("#sheet-backdrop").hidden = false;
  state.sheetOpen = true;
}
function closeSheet() {
  $("#sheet").hidden = true; $("#sheet-backdrop").hidden = true;
  state.sheetOpen = false; sheetBusy = false;
  if (state.selected !== null) { state.selected = null; renderPlan(false); }
}

function findArea(id) {
  return state.areas.find((a) => a.id === id) || listCache.find((a) => a.id === id);
}

function openArea(id) {
  const a = findArea(id);
  if (!a) return;
  state.selected = id;
  if (a.plan_id === state.planId) renderPlan(false);
  sheetBusy = false;
  renderAreaSheet(a);
}

function renderAreaSheet(a) {
  const c = st(a.status);
  const facts = el("dl", { class: "facts" });
  const fact = (k, v) => v && facts.append(el("dt", {}, k), el("dd", {}, v));
  fact("Prüfer", a.inspector || "nicht zugewiesen");
  if (a.status === "appointment") fact("Termin", fmtDate(a.date) + (a.overdue ? " – überfällig" : ""));
  fact("Bemerkung", a.note);
  fact("Hinweise", a.notes);
  fact("Prüflinge", a.devices ? `ca. ${a.devices}` : "");
  fact("Geändert", a.status_by ? `${a.status_by}, ${fmtDateTime(a.status_at)}` : "");

  const allowed = mayChange(a);
  const act = (key, text, extra = "") => el("button", {
    type: "button", class: `act ${key} ${extra}`, disabled: !allowed,
    style: { background: st(key).fill, borderColor: st(key).border },
    onclick: () => (key === "done" || key === "open") ? quickStatus(a, key) : statusForm(a, key),
  }, text);

  const hist = el("details", { class: "hist", ontoggle: (ev) => ev.target.open && loadHistory(a, ev.target) },
    el("summary", {}, "Verlauf"), el("div", { class: "hist-body muted" }, "lädt …"));

  openSheet(el("div", {},
    el("h2", { id: "sheet-title" }, a.name),
    el("p", { class: "sub" }, `${a.plan}${a.w ? ` · ${a.w} × ${a.h} m` : ""}`),
    el("span", { class: "status-pill", style: { background: c.fill, borderColor: a.overdue ? "#d0271d" : c.border } },
      `${ICONS[a.status]} ${statusText(a)}`),
    facts,
    !allowed ? el("div", { class: "notice" }, `Dieser Bereich ist ${a.inspector} zugewiesen.`) : null,
    allowed && !a.inspector_id ? el("button", { type: "button", class: "btn btn-block", onclick: () => claim(a) }, "Bereich übernehmen") : null,
    el("div", { class: "actions" },
      act("done", "✔ Erledigt", "wide"),
      act("partial", "◐ Teilweise"),
      act("appointment", "📅 Termin"),
      act("clarify", "? Klärung"),
      act("open", "○ Offen")),
    hist));
}

async function loadHistory(a, details) {
  const box = details.querySelector(".hist-body");
  try {
    const r = await api(`/api/areas/${a.id}/history`);
    box.replaceChildren(...(r.history.length ? r.history.map((h) => el("div", { class: "hist-item" },
      el("div", {}, el("strong", {}, `${ICONS[h.status]} ${st(h.status).label}`),
        h.status === "partial" ? ` (${h.progress} %)` : "", h.date ? ` am ${fmtDate(h.date)}` : ""),
      el("div", { class: "muted" }, `${fmtDateTime(h.timestamp)} · ${h.username}`),
      h.note ? el("div", {}, h.note) : null)) : [el("div", {}, "Noch keine Änderungen.")]));
  } catch (e) { box.textContent = e.message; }
}

function statusForm(a, key) {
  sheetBusy = true;
  const c = st(key);
  const note = el("textarea", { rows: 3, placeholder: key === "clarify" ? "Was muss geklärt werden?" : "Bemerkung (optional)" });
  note.value = a.status === key ? (a.note || "") : "";
  const rows = [];
  let date, range;
  if (key === "appointment") {
    date = el("input", { type: "date", value: a.status === "appointment" && a.date ? a.date : todayIso(1), min: todayIso(-365) });
    rows.push(el("div", { class: "form-row" }, el("label", {}, "Termin am"), date,
      el("div", { class: "quick" },
        [["Morgen", 1], ["Übermorgen", 2], ["In 1 Woche", 7]].map(([t, d]) =>
          el("button", { type: "button", class: "chip", onclick: () => { date.value = todayIso(d); } }, t)))));
  }
  if (key === "partial") {
    const val = el("span", { class: "range-val" });
    range = el("input", { type: "range", min: 10, max: 90, step: 10, value: a.status === "partial" ? Math.min(90, Math.max(10, Math.round(a.progress / 10) * 10)) : 50 });
    const upd = () => { val.textContent = `${range.value} %`; };
    range.addEventListener("input", upd); upd();
    rows.push(el("div", { class: "form-row" }, el("label", {}, "Wie weit ist der Bereich geprüft?"),
      el("div", { class: "range-row" }, range, val)));
  }
  rows.push(el("div", { class: "form-row" }, el("label", {}, key === "clarify" ? "Grund *" : "Bemerkung"), note,
    key === "clarify" ? el("div", { class: "quick" }, CLARIFY_QUICK.map((q) =>
      el("button", { type: "button", class: "chip", onclick: () => { note.value = note.value ? `${note.value}; ${q}` : q; err.textContent = ""; } }, q))) : null));
  const err = el("p", { class: "error" });
  note.addEventListener("input", () => { err.textContent = ""; });
  const save = el("button", { type: "button", class: "btn btn-primary" }, "Speichern");
  save.addEventListener("click", async () => {
    if (key === "clarify" && !note.value.trim()) { err.textContent = "Bitte einen Grund angeben."; return; }
    if (key === "appointment" && !date.value) { err.textContent = "Bitte ein Datum wählen."; return; }
    save.disabled = true;
    const ok = await setStatus(a, { status: key, note: note.value, date: date ? date.value : null, progress: range ? Number(range.value) : null });
    save.disabled = false;
    if (ok) closeSheet();
  });
  openSheet(el("div", {},
    el("h2", { id: "sheet-title" }, a.name),
    el("p", { class: "sub" }, el("span", { class: "status-pill", style: { background: c.fill, borderColor: c.border } }, `${ICONS[key]} ${c.label}`)),
    ...rows, err,
    el("div", { class: "btn-row" },
      el("button", { type: "button", class: "btn", onclick: () => { sheetBusy = false; renderAreaSheet(findArea(a.id) || a); } }, "Zurück"),
      save)));
  if (key === "clarify") setTimeout(() => note.focus(), 50);
}

async function quickStatus(a, key) {
  const before = { status: a.status, note: a.note, date: a.date, progress: a.progress };
  const ok = await setStatus(a, { status: key, note: "", date: null, progress: null });
  if (!ok) return;
  closeSheet();
  if (before.status !== key) {
    toast(`„${a.name}“: ${st(key).label}`, "Rückgängig", async () => {
      const cur = findArea(a.id) || a;
      await setStatus(cur, { status: before.status, note: before.note || "", date: before.date,
        progress: before.status === "partial" ? before.progress : null }, true);
    });
  }
}

/** Status setzen; bei fehlender Verbindung lokal vormerken. */
async function setStatus(a, values, silent = false) {
  const body = { ...values, version: a.version };
  try {
    const r = await api(`/api/areas/${a.id}/status`, { method: "POST", body });
    replaceArea(r.area);
    if (!silent && values.status !== "done" && values.status !== "open") toast(`Gespeichert: ${statusText(r.area)}`);
    refresh();
    return true;
  } catch (e) {
    if (e.status === 0 || e.status === 503) {
      const local = { status: values.status, note: values.note || "", date: values.status === "appointment" ? values.date : null,
        progress: values.status === "done" ? 100 : values.status === "partial" ? values.progress : (values.status === "open" ? 0 : a.progress),
        status_by: state.user.username, inspector_id: a.inspector_id || state.user.id, inspector: a.inspector || state.user.name };
      const q = queue().filter((x) => x.id !== a.id);
      // Version bleibt die vom Server zuletzt bekannte – so werden echte Konflikte erkannt
      q.push({ id: a.id, name: a.name, body: { ...body, version: (queue().find((x) => x.id === a.id)?.body.version) ?? a.version }, local });
      lsSet(LS.queue, q);
      Object.assign(a, local);
      replaceArea(a);
      renderQueueBanner();
      toast("Offline gespeichert – wird übertragen, sobald Verbindung besteht.");
      return true;
    }
    if (e.status === 409) {
      toast("Ein anderer Prüfer hat den Bereich gerade geändert. Ansicht aktualisiert.");
      await refresh();
      const fresh = findArea(a.id);
      if (fresh && state.sheetOpen) { sheetBusy = false; renderAreaSheet(fresh); }
      return false;
    }
    toast(e.message);
    return false;
  }
}

async function claim(a) {
  try {
    const r = await api(`/api/areas/${a.id}/claim`, { method: "POST", body: { version: a.version } });
    replaceArea(r.area);
    renderAreaSheet(r.area);
    toast("Bereich übernommen");
  } catch (e) { toast(e.message); refresh(); }
}

function replaceArea(area) {
  for (const list of [state.areas, listCache]) {
    const i = list.findIndex((x) => x.id === area.id);
    if (i >= 0) list[i] = { ...list[i], ...area };
  }
  renderSummary(); renderPlan(false); updateBadges();
  if (state.view === "list") renderList();
  if (state.view === "dates") renderDates();
}

// ---------------------------------------------------------------- Grundriss wählen
function openPlanPicker() {
  if (!state.plans.length) { toast("Keine Grundrisse vorhanden."); return; }
  openSheet(el("div", {},
    el("h2", { id: "sheet-title" }, "Grundriss wählen"),
    el("div", {}, state.plans.map((p) => el("button", {
      type: "button", class: `plan-option ${p.id === state.planId ? "current" : ""}`,
      onclick: () => { state.planId = p.id; state.plan = null; closeSheet(); setView("plan"); refresh(true); },
    },
    el("div", {}, el("strong", {}, p.name), p.location ? el("span", { class: "muted" }, ` · ${p.location}`) : null),
    el("div", { class: "muted small" }, `${p.summary.total} Bereiche · ${p.summary.progress} % erledigt` +
      (p.summary.overdue ? ` · ⚠ ${p.summary.overdue} überfällig` : "")),
    el("div", { class: "progress" }, el("span", { style: { width: `${p.summary.progress}%` } })))))));
}

// ---------------------------------------------------------------- Listen
let listCache = [];
function buildChips() {
  const box = $("#status-chips");
  box.replaceChildren();
  const chip = (key, label) => el("button", { type: "button", class: `chip ${state.listStatus === key ? "on" : ""}`,
    onclick: () => { state.listStatus = key; buildChips(); renderList(); } }, label);
  box.append(chip("", "Alle"), chip("todo", "Noch zu tun"), ...ORDER.map((k) => chip(k, `${ICONS[k]} ${st(k).label}`)));
}

async function loadList() {
  const all = $("#list-all").checked;
  try {
    const r = await api(`/api/areas?${all ? "" : "inspector=me"}`);
    listCache = r.areas;
    lsSet("pp.cache.list", listCache);
  } catch (e) {
    if (e.status === 0 || e.status === 503) listCache = lsGet("pp.cache.list", listCache);
    else toast(e.message);
  }
  for (const item of queue()) { const a = listCache.find((x) => x.id === item.id); if (a) Object.assign(a, item.local); }
  renderList();
}

function card(a, sub) {
  const c = st(a.status);
  return el("li", { class: `card ${a.overdue ? "overdue" : ""}`, onclick: () => openArea(a.id) },
    el("span", { class: "stripe", style: { background: c.border } }),
    el("div", { class: "ci" },
      el("div", { class: "t" }, el("span", {}, a.name), el("span", { class: "muted small" }, `${ICONS[a.status]}`)),
      el("div", { class: "s" }, sub ?? `${statusText(a)}${a.overdue ? " – überfällig" : ""} · ${a.plan}`),
      a.note || a.notes ? el("div", { class: "n" }, a.note || a.notes) : null));
}

function renderList() {
  const q = $("#search").value.trim().toLowerCase();
  let items = listCache.filter((a) => !q || `${a.name} ${a.plan} ${a.note} ${a.notes}`.toLowerCase().includes(q));
  if (state.listStatus === "todo") items = items.filter((a) => a.status !== "done");
  else if (state.listStatus) items = items.filter((a) => a.status === state.listStatus);
  items.sort((x, y) => ORDER.indexOf(x.status) - ORDER.indexOf(y.status));
  const list = $("#area-list");
  list.replaceChildren();
  if (!items.length) { list.append(el("p", { class: "muted", style: { textAlign: "center" } }, "Keine Bereiche.")); return; }
  let group = null;
  for (const a of items) {
    if (a.status !== group) { group = a.status; list.append(el("li", { class: "group-h" }, st(group).label)); }
    list.append(card(a));
  }
}

async function loadDates() {
  const all = $("#dates-all").checked;
  try {
    const r = await api(`/api/areas?status=appointment${all ? "" : "&inspector=me"}`);
    listCache = r.areas;
  } catch (e) { if (e.status !== 0) toast(e.message); }
  renderDates();
}

function renderDates() {
  const items = listCache.filter((a) => a.status === "appointment").sort((x, y) => (x.date || "").localeCompare(y.date || ""));
  const list = $("#date-list");
  list.replaceChildren();
  if (!items.length) { list.append(el("p", { class: "muted", style: { textAlign: "center" } }, "Keine offenen Termine.")); return; }
  const today = todayIso(), tomorrow = todayIso(1);
  let group = null;
  for (const a of items) {
    const g = a.date < today ? "Überfällig" : a.date === today ? "Heute" : a.date === tomorrow ? "Morgen" : fmtDate(a.date);
    if (g !== group) { group = g; list.append(el("li", { class: "group-h" }, g)); }
    list.append(card(a, `${fmtDate(a.date)} · ${a.plan}${$("#dates-all").checked && a.inspector ? ` · ${a.inspector}` : ""}`));
  }
}

// ---------------------------------------------------------------- Navigation
function setView(view) {
  state.view = view;
  for (const v of ["plan", "list", "dates", "more"]) $(`#view-${v}`).hidden = v !== view;
  for (const b of document.querySelectorAll(".tabbar button")) b.classList.toggle("active", b.dataset.view === view);
  if (view === "list") loadList();
  if (view === "dates") loadDates();
  if (view === "plan") requestAnimationFrame(() => (vb ? applyVb() : fitView()));
}

let toastTimer = null;
function toast(text, action, onAction) {
  const t = $("#toast");
  $("#toast-text").textContent = text;
  const b = $("#toast-action");
  b.hidden = !action;
  b.textContent = action || "";
  b.onclick = action ? () => { t.hidden = true; onAction(); } : null;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 6000 : 3500);
}

// ---------------------------------------------------------------- Start
function init() {
  $("#login-form").addEventListener("submit", login);
  $("#plan-picker").addEventListener("click", openPlanPicker);
  $("#sheet-backdrop").addEventListener("click", closeSheet);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && state.sheetOpen) closeSheet(); });
  for (const b of document.querySelectorAll(".tabbar button")) b.addEventListener("click", () => setView(b.dataset.view));
  $("#zoom-in").addEventListener("click", () => { const r = svgEl().getBoundingClientRect(); zoomAt(1.4, r.left + r.width / 2, r.top + r.height / 2); });
  $("#zoom-out").addEventListener("click", () => { const r = svgEl().getBoundingClientRect(); zoomAt(1 / 1.4, r.left + r.width / 2, r.top + r.height / 2); });
  $("#zoom-fit").addEventListener("click", fitView);
  $("#only-mine").checked = !!lsGet(LS.mine, false);
  $("#only-mine").addEventListener("change", (e) => { lsSet(LS.mine, e.target.checked); renderPlan(false); });
  $("#search").addEventListener("input", renderList);
  $("#list-all").addEventListener("change", loadList);
  $("#dates-all").addEventListener("change", loadDates);
  $("#logout-btn").addEventListener("click", () => logout());
  $("#refresh-btn").addEventListener("click", async () => { await flushQueue(); await refresh(); toast("Aktualisiert"); });
  const s = svgEl();
  s.addEventListener("pointerdown", onPointerDown);
  s.addEventListener("pointermove", onPointerMove);
  s.addEventListener("pointerup", onPointerUp);
  s.addEventListener("pointercancel", onPointerUp);
  s.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY); }, { passive: false });
  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {   // nur bei Drehen/Größenänderung, nicht bei Tastatur
    if (window.innerWidth !== lastWidth && state.view === "plan" && state.plan) fitView();
    lastWidth = window.innerWidth;
  });
  window.addEventListener("online", () => { flushQueue(); refresh(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { flushQueue(); refresh(); } });
  setInterval(() => { if (!document.hidden && state.token) { flushQueue(); if (!sheetBusy) refresh(); } }, POLL_MS);
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("sw.js").catch(() => {});

  state.token = lsGet(LS.token);
  state.user = lsGet(LS.user);
  if (state.token && state.user) start(); else logout();
}

init();
