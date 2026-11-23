/* Premium Deluxe Motorsport – Web-Oberfläche des ERP.
   Daten kommen von /api/state, Änderungen gehen über /api/action/<name>.
   Alle Geschäftsregeln prüft der Server; hier wird nur angezeigt und erfasst. */
"use strict";

/* ------------------------------------------------------------------ Hilfen */
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });   // Geld: 120,000.00
const intf = new Intl.NumberFormat("de-DE");
const money = v => { const n = Number(v) || 0; return (n < 0 ? "-$" : "$") + eur.format(Math.abs(n)); };
const num = v => eur.format(Number(v) || 0);
const round2 = v => Math.round(v * 100) / 100;
const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const today = () => iso(new Date());
const days = (a, b = today()) => Math.round((new Date(b) - new Date(a)) / 864e5);
const de = d => d ? d.split("-").reverse().join(".") : "";
class Fehler extends Error {}

function parseMoney(v) {
  if (v === null || v === undefined || v === "") return 0;
  if (typeof v === "number") return v;
  let t = String(v).replace(/\$|€|EUR|\s/g, "");
  if (t === "") return 0;
  // Amerikanisch (120,000.00 / 120,000) und deutsch (80.000 / 1.234,56) werden erkannt
  if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
  else if (t.includes(",") && t.includes(".")) t = t.lastIndexOf(",") > t.lastIndexOf(".") ? t.replace(/\./g, "").replace(",", ".") : t.replace(/,/g, "");
  else if (/^-?\d{1,3}(,\d{3})+$/.test(t)) t = t.replace(/,/g, "");
  else if (t.includes(",")) t = t.replace(",", ".");
  const n = Number(t);
  if (!isFinite(n)) throw new Fehler("Ungültiger Betrag: " + v);
  return round2(n);
}

/* ------------------------------------------------------------------ Server */
let S = null;

async function request(path, body) {
  const opt = body === undefined ? { credentials: "same-origin" }
    : { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let res;
  try { res = await fetch(path, opt); }
  catch (e) { throw new Fehler("Keine Verbindung zum Server. Bitte Internetverbindung prüfen."); }
  let data = {};
  try { data = await res.json(); } catch (e) { /* leere Antwort */ }
  if (res.status === 401 && path !== "/api/login") { showLogin(); throw new Fehler("Bitte erneut anmelden."); }
  if (!res.ok) throw new Fehler(data.error || `Fehler ${res.status}`);
  return data;
}
let loadedVersion = null;
async function reload() {
  S = await request("/api/state");
  if (loadedVersion && S.version !== loadedVersion) { location.reload(); return; }   // Update eingespielt -> neue Oberfläche laden
  loadedVersion = S.version;
}
async function api(name, body) { const r = await request("/api/action/" + name, body); await reload(); return r; }

/* ------------------------------------------------------------------ Begriffe */
const STATUS = { lager: "Lager", ausstellung: "Ausstellung", reserviert: "Reserviert", verkauft: "Verkauft", ausgebucht: "Ausgebucht", inzahlung: "Inzahlungnahme offen" };
const PURCHASE_STATUS = { angekauft: "Angekauft", inzahlungnahme: "Inzahlungnahme", verrechnet: "Verrechnet", storniert: "Storniert" };
const AVAILABLE = ["lager", "ausstellung", "reserviert"];
const ORDER_STATUS = { bestellt: "Bestellt", eingetroffen: "Eingetroffen", ausgeliefert: "Ausgeliefert", storniert: "Storniert" };
const OFFER_STATUS = { offen: "Offen", abgelehnt: "Abgelehnt", abgelaufen: "Abgelaufen", bestellt: "Bestellt" };
const SALE_STATUS = { ok: "Abgeschlossen", storno: "Storniert" };
const PAY = { bank: "Überweisung", bar: "Bar" };
const CATS = ["Kompakt", "Limousine", "Coupé", "Cabrio", "Sportwagen", "Supersportwagen", "SUV", "Geländewagen", "Muscle Car", "Klassiker", "Van", "Transporter", "Motorrad", "Sonstige"];

const byId = (list, id) => (S[list] || []).find(x => x.id === id);
const custName = id => byId("customers", id)?.name ?? "";
const vName = v => (v.make + " " + v.model).trim();
const vLabel = v => v ? v.nr + " " + vName(v) + (v.plate || v.vin ? " (" + (v.plate || v.vin) + ")" : "") : "";


function history(cid) {
  const out = [];
  S.sales.filter(s => s.customerId === cid).forEach(s => {
    out.push([s.date, "Kauf" + (s.status === "storno" ? " (storniert)" : ""), s.nr, vLabel(byId("vehicles", s.vehicleId)), s.price]);
    if (s.tradeId) out.push([s.date, "Inzahlungnahme", s.nr, vLabel(byId("vehicles", s.tradeId)), s.tradeValue]);
  });
  S.purchases.filter(p => p.customerId === cid).forEach(p => out.push([p.date, (["inzahlungnahme", "verrechnet"].includes(p.status) ? "Inzahlungnahme" : "Ankauf von Kunde") + ` (${PURCHASE_STATUS[p.status]})`, p.nr, vLabel(byId("vehicles", p.vehicleId)), p.price]));
  S.offers.filter(o => o.customerId === cid).forEach(o => out.push([o.date, `Angebot (${OFFER_STATUS[o.status]})`, o.nr, offerSubject(o), o.price]));
  S.orders.filter(o => o.customerId === cid).forEach(o => { const m = byId("models", o.modelId); out.push([o.date, `Bestellung (${ORDER_STATUS[o.status]})`, o.nr, (m ? m.make + " " + m.model : "") + (o.color ? ", " + o.color : ""), o.vk]); });
  S.vehicles.filter(v => v.resFor === cid && v.status === "reserviert").forEach(v => out.push([v.resUntil || today(), "Reservierung", v.nr, vLabel(v), v.vk]));
  return out.sort((a, b) => b[0].localeCompare(a[0]));
}

function kpis() {
  const t = today(), ms = t.slice(0, 8) + "01", ys = t.slice(0, 5) + "01-01";
  const stock = S.vehicles.filter(v => AVAILABLE.includes(v.status));
  const done = S.sales.filter(s => s.status === "ok");
  const sum = (l, f) => l.reduce((a, x) => a + f(x), 0);
  const month = done.filter(s => s.date >= ms), year = done.filter(s => s.date >= ys);
  const open = S.orders.filter(o => o.status === "bestellt");
  return { stock: stock.length, used: stock.filter(v => v.cond === "gebraucht").length, reserved: stock.filter(v => v.status === "reserviert").length,
    stockEk: sum(stock, v => v.ek), stockVk: sum(stock, v => v.vk), showroom: stock.filter(v => v.showSince).length,
    open: open.length, late: open.filter(o => o.expected && o.expected < t).length, deposits: sum(open, o => o.deposit),
    mCount: month.length, mSum: sum(month, s => s.price), mMargin: sum(month, s => s.price - s.ek),
    yCount: year.length, ySum: sum(year, s => s.price), yMargin: sum(year, s => s.price - s.ek), models: S.models.filter(m => m.active).length };
}
function monthly() {
  const out = [], d = new Date(); d.setDate(1);
  for (let i = 11; i >= 0; i--) {
    const x = new Date(d.getFullYear(), d.getMonth() - i, 1), key = x.getFullYear() + "-" + String(x.getMonth() + 1).padStart(2, "0");
    out.push([String(x.getMonth() + 1).padStart(2, "0") + "/" + String(x.getFullYear()).slice(2),
      S.sales.filter(s => s.status === "ok" && s.date.startsWith(key)).reduce((a, s) => a + s.price, 0)]);
  }
  return out;
}

/* ------------------------------------------------------------------ Ansicht */
const VIEWS = [
  ["cockpit", "Cockpit", "Autohaus"], ["bestand", "Fahrzeugbestand", "Autohaus"], ["ausstellung", "Ausstellung", "Autohaus"],
  ["angebote", "Angebote", "Autohaus"], ["bestellungen", "Bestellungen", "Autohaus"], ["verkauf", "Verkauf", "Autohaus"], ["ankauf", "Gebrauchtwagen-Ankauf", "Autohaus"],
  ["kunden", "Kunden", "Stammdaten"], ["katalog", "Fahrzeugkatalog", "Stammdaten"], ["einstellungen", "Firmendaten", "System"], ["benutzer", "Benutzer", "System"]];
const UI = { view: "cockpit", sel: {}, q: {}, filter: { bestand: "verfuegbar", cond: "", openOnly: true, year: String(new Date().getFullYear()) } };
const allowed = v => S.me.views.includes(v);

function counts() {
  return { bestand: S.vehicles.filter(v => AVAILABLE.includes(v.status)).length, ausstellung: S.vehicles.filter(v => v.showSince && AVAILABLE.includes(v.status)).length,
    angebote: S.offers.filter(o => o.status === "offen").length,
    bestellungen: S.orders.filter(o => ["bestellt", "eingetroffen"].includes(o.status)).length, kunden: S.customers.length, katalog: S.models.length };
}
function setBrand(topId, subId) {
  const words = (S?.settings.company || "Premium Deluxe Motorsport").trim().split(/\s+/);
  $(topId).textContent = (words.length > 1 ? words.slice(0, -1).join(" ") : words[0]).toUpperCase();
  $(subId).textContent = words.length > 1 ? words.at(-1).toUpperCase() : "";
}
function renderNav() {
  const c = counts(); let grp = "", h = "";
  for (const [k, label, g] of VIEWS) {
    if (!allowed(k)) continue;
    if (g !== grp) { h += `<h6>${g}</h6>`; grp = g; }
    h += `<a href="#${k}" class="${UI.view === k ? "on" : ""}">${label}${c[k] !== undefined ? `<em>${c[k]}</em>` : ""}</a>`;
  }
  $("#nav").innerHTML = h;
  setBrand("#brandTop", "#brandSub");
  document.title = S.settings.company + " · ERP";
  const logoUrl = "/logo?v=" + encodeURIComponent(S.settings.logoVersion);
  document.querySelectorAll("img.loginLogo, img.sideLogo").forEach(i => { if (!i.src.endsWith(logoUrl)) i.src = logoUrl; });
  const fav = $("#favicon"); if (fav && !fav.href.endsWith(logoUrl)) fav.href = logoUrl;
  $("#who").innerHTML = `<span>Angemeldet als</span><b>${esc(S.me.fullName || S.me.username)}</b><span>${esc(S.me.roleName)}</span>
    <div class="links"><button data-a="pw">Passwort ändern</button><button data-a="logout">Abmelden</button></div>
    <span class="ver" title="Programmversion">Version ${esc(S.version)}</span>`;
}
function render() {
  if (!S) return;
  renderNav();
  const mob = `<div class="mobileWho">${esc(S.me.fullName || S.me.username)} · <button data-a="pw">Passwort</button><button data-a="logout">Abmelden</button></div>`;
  const views = { cockpit, bestand, ausstellung, angebote, bestellungen, verkauf, ankauf, kunden, katalog, einstellungen, benutzer };
  $("#main").innerHTML = mob + views[UI.view]();
  const q = $("#q"); if (q) q.value = UI.q[UI.view] || "";
  fitList();
}
function fitList() {
  const w = $("#main .tablewrap.mainList"); if (!w) return;
  if (innerWidth <= 760) { w.style.maxHeight = ""; return; }
  w.style.maxHeight = Math.max(220, innerHeight - w.getBoundingClientRect().top - 20) + "px";
  const excess = document.documentElement.scrollHeight - innerHeight;          // Fußzeile/Abstände nachmessen
  if (excess > 0) w.style.maxHeight = Math.max(220, parseFloat(w.style.maxHeight) - excess) + "px";
}
addEventListener("resize", fitList);
function head(eyebrow, title, sub) { return `<div class="head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ""}</div></div>`; }
function pill(key, label) { return `<span class="pill ${key}">${esc(label)}</span>`; }
function match(text) { const q = (UI.q[UI.view] || "").toLowerCase(); return !q || text.toLowerCase().includes(q); }
/* acts: optionale Funktion id -> Knöpfe für die Aktionsspalte am Zeilenende; main: Hauptliste der Ansicht (feste Kopfzeile, eigener Bildlauf) */
function tbl(cols, rows, emptyText, acts, main = !!acts) {
  const th = cols.map(c => `<th class="${c[1] ? "n" : ""}">${c[0]}</th>`).join("") + (acts ? `<th class="act"><span class="sr">Aktionen</span></th>` : "");
  const sel = UI.sel[UI.view];
  const body = rows.length ? rows.map(r => `<tr data-id="${esc(r.id)}" class="${r.id === sel ? "sel" : ""}">${r.cells.map((v, i) => `<td class="${cols[i][1] ? "n" : ""}${cols[i][2] ? " dim" : ""}">${v}</td>`).join("")}${acts ? `<td class="act">${acts(r.id)}</td>` : ""}</tr>`).join("")
    : `<tr><td colspan="${cols.length + (acts ? 1 : 0)}" class="empty">${emptyText}</td></tr>`;
  return `<div class="tablewrap${main ? " mainList" : ""}"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`;
}
/* Knöpfe in der Tabellenzeile; ein Klick wählt zugleich die Zeile aus */
const rb = (action, label, cls = "") => `<button class="btn sm ${cls}" data-a="${action}">${label}</button>`;
const rbAdmin = () => isAdmin() ? rb("purge", "Endgültig", "danger adminDel") : "";
const searchBox = ph => `<input id="q" class="search" type="search" placeholder="${ph}" aria-label="Suchen">`;
const btn = (action, label, cls = "") => `<button class="btn ${cls}" data-a="${action}">${label}</button>`;
const isAdmin = () => S.me.role === "admin";
const adminDel = () => isAdmin() ? `<button class="btn danger adminDel" data-a="purge" title="Nur für Administratoren: Eintrag ohne Rücksicht auf den Status komplett entfernen">Endgültig löschen</button>` : "";

/* Cockpit */
function cockpit() {
  const k = kpis();
  const tile = (t, v, s) => `<div class="tile"><small>${t}</small><strong>${v}</strong><span>${s || "&nbsp;"}</span></div>`;
  const pct = (a, b) => b ? "Marge " + (a / b * 100).toFixed(1).replace(".", ",") + " %" : "";
  const aging = S.vehicles.filter(v => AVAILABLE.includes(v.status)).sort((a, b) => a.inDate.localeCompare(b.inDate)).slice(0, 8);
  const exp = S.orders.filter(o => o.status === "bestellt").sort((a, b) => (a.expected || "9").localeCompare(b.expected || "9"));
  return head("Übersicht", esc(S.settings.company), "Bestand, Ausstellung, Bestellungen und Verkäufe auf einen Blick") +
    `<div class="tiles">${tile("Fahrzeuge im Bestand", k.stock, `davon ${k.used} gebraucht · ${k.reserved} reserviert`)}${tile("Bestandswert (EK)", money(k.stockEk), "Listenwert " + money(k.stockVk))}
    ${tile("In der Ausstellung", k.showroom, k.models + " Modelle im Katalog")}${tile("Offene Bestellungen", k.open, `<span class="${k.late ? "late" : ""}">${k.late} überfällig</span> · Anzahlungen ${money(k.deposits)}`)}
    ${tile("Verkäufe lfd. Monat", money(k.mSum), k.mCount + " Fahrzeug(e)")}${tile("Rohertrag lfd. Monat", money(k.mMargin), pct(k.mMargin, k.mSum))}
    ${tile("Verkäufe lfd. Jahr", money(k.ySum), k.yCount + " Fahrzeug(e)")}${tile("Rohertrag lfd. Jahr", money(k.yMargin), pct(k.yMargin, k.ySum))}</div>
    <div class="chart"><h3 class="eyebrow">Fahrzeugverkäufe je Monat</h3>${chart(monthly())}</div>
    <div class="two"><div><h3 class="eyebrow">Längste Standzeit</h3>${tbl([["Fahrzeug"], ["Status"], ["Tage", 1], ["VK", 1]],
      aging.map(v => ({ id: v.id, cells: [esc(vLabel(v)), pill(v.status, STATUS[v.status]), days(v.inDate), num(v.vk)] })), "Kein Fahrzeug im Bestand")}</div>
    <div><h3 class="eyebrow">Erwartete Fahrzeuge</h3>${tbl([["Bestellung"], ["Modell"], ["Für"], ["Liefertermin", 1]],
      exp.map(o => { const m = byId("models", o.modelId); return { id: o.id, cells: [o.nr, esc(m ? m.make + " " + m.model : ""), esc(o.customerId ? custName(o.customerId) : "Lager"), `<span class="${o.expected && o.expected < today() ? "late" : ""}">${de(o.expected) || "–"}</span>`] }; }), "Keine offenen Bestellungen")}</div></div>`;
}
function chart(data) {
  const W = 960, H = 230, L = 64, B = 28, T = 16, max = Math.max(...data.map(d => d[1]), 1);
  const step = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / step) * step;
  const slot = (W - L - 10) / data.length, bw = slot * .56;
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = top * i / 4, y = H - B - (H - B - T) * i / 4;
    g += `<line x1="${L}" x2="${W - 10}" y1="${y}" y2="${y}" stroke="var(--border)" stroke-width="1"/><text x="${L - 8}" y="${y + 4}" text-anchor="end" fill="var(--muted)" font-size="11">${v >= 1e6 ? (v / 1e6).toLocaleString("de-DE") + " Mio" : intf.format(Math.round(v / 1000)) + "k"}</text>`;
  }
  data.forEach(([label, v], i) => {
    const x = L + i * slot + (slot - bw) / 2, h = (H - B - T) * v / top, last = i === data.length - 1;
    g += `<rect x="${x}" y="${H - B - h}" width="${bw}" height="${Math.max(h, v ? 1 : 0)}" fill="${last ? "var(--gold-light)" : "url(#gg)"}" rx="1"><title>${label}: ${money(v)}</title></rect>`;
    g += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" fill="${last ? "var(--gold-light)" : "var(--muted)"}" font-size="11">${label}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Verkäufe je Monat"><defs><linearGradient id="gg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--gold)"/><stop offset="1" stop-color="var(--gold-dark)"/></linearGradient></defs>${g}</svg>`;
}

/* Fahrzeugbestand */
const FILTERS = { verfuegbar: ["Verfügbar (Lager, Ausstellung, Reserviert)", AVAILABLE], lager: ["Lager", ["lager"]], ausstellung: ["Ausstellung", ["ausstellung"]],
  reserviert: ["Reserviert", ["reserviert"]], verkauft: ["Verkauft", ["verkauft"]], ausgebucht: ["Ausgebucht", ["ausgebucht"]], alle: ["Alle Fahrzeuge", null] };
function bestand() {
  const f = FILTERS[UI.filter.bestand][1];
  const list = S.vehicles.filter(v => (!f || f.includes(v.status)) && (!UI.filter.cond || v.cond === UI.filter.cond) &&
    match([v.nr, v.make, v.model, v.plate, v.vin, v.color, v.cat, v.loc].join(" "))).sort((a, b) => b.nr.localeCompare(a.nr));
  const ek = list.reduce((a, v) => a + v.ek, 0), vk = list.reduce((a, v) => a + v.vk, 0);
  return head("Autohaus", "Fahrzeugbestand", "Jedes Fahrzeug einzeln mit Preisen, Marge und Standzeit") +
    `<div class="bar">${searchBox("Fahrzeug, Kennzeichen, FIN …")}
    <select id="fStatus" aria-label="Status">${Object.entries(FILTERS).map(([k, [l]]) => `<option value="${k}" ${UI.filter.bestand === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    <select id="fCond" aria-label="Zustand"><option value="">Neu & gebraucht</option><option value="neu" ${UI.filter.cond === "neu" ? "selected" : ""}>Neu</option><option value="gebraucht" ${UI.filter.cond === "gebraucht" ? "selected" : ""}>Gebraucht</option></select>
    <span class="grow"></span>${btn("vNew", "Neues Fahrzeug", "primary")}${btn("vShow", "Ausstellen")}${btn("vRes", "Reservieren")}${btn("vUnres", "Reservierung aufheben")}
    ${allowed("verkauf") ? btn("vSell", "Verkaufen") : ""}</div>` +
    tbl([["Nr."], ["Fahrzeug"], ["Klasse", 0, 1], ["Zustand"], ["Farbe", 0, 1], ["Kennzeichen"], ["FIN", 0, 1], ["km", 1], ["EK", 1], ["VK", 1], ["Marge", 1], ["Tage", 1], ["Status"], ["Stellplatz / Reserviert für"]],
      list.map(v => ({ id: v.id, cells: [v.nr, esc(vName(v)), esc(v.cat), v.cond === "neu" ? "Neu" : "Gebraucht", esc(v.color), esc(v.plate), esc(v.vin), intf.format(v.km), num(v.ek), num(v.vk), num(v.vk - v.ek),
        AVAILABLE.includes(v.status) ? days(v.inDate) : "–", pill(v.status, STATUS[v.status]), esc(v.status === "reserviert" ? custName(v.resFor) : v.loc)] })),
      S.vehicles.length ? "Keine Fahrzeuge für diesen Filter" : "Noch keine Fahrzeuge. Lege eins an oder buche den Eingang einer Bestellung.",
      id => rb("vEdit", "Bearbeiten") + (["lager", "ausstellung"].includes(byId("vehicles", id).status) ? rb("vRemove", "Ausbuchen", "danger") : "") + rbAdmin()) +
    `<div class="foot">${list.length} Fahrzeug(e) · Einkaufswert ${money(ek)} · Listenwert ${money(vk)}</div>`;
}

/* Ausstellung */
function ausstellung() {
  const list = S.vehicles.filter(v => v.showSince && AVAILABLE.includes(v.status) && match([v.nr, v.make, v.model, v.loc, v.color].join(" "))).sort((a, b) => (a.loc || "~").localeCompare(b.loc || "~"));
  return head("Autohaus", "Ausstellung", "Showroom nach Stellplatz. Ein Stellplatz kann nur mit einem Fahrzeug belegt sein.") +
    `<div class="bar">${searchBox("Stellplatz, Fahrzeug …")}<span class="grow"></span>${btn("sShow", "Fahrzeug ausstellen", "primary")}
    ${btn("vRes", "Reservieren")}${allowed("verkauf") ? btn("vSell", "Verkaufen") : ""}</div>` +
    tbl([["Stellplatz"], ["Nr."], ["Fahrzeug"], ["Klasse", 0, 1], ["Farbe", 0, 1], ["VK", 1], ["Seit", 1], ["Tage", 1], ["Status"], ["Reserviert für"]],
      list.map(v => ({ id: v.id, cells: [esc(v.loc || "–"), v.nr, esc(vName(v)), esc(v.cat), esc(v.color), num(v.vk), de(v.showSince), days(v.showSince), pill(v.status, STATUS[v.status]), esc(custName(v.resFor))] })),
      "Noch kein Fahrzeug ausgestellt", id => rb("sMove", "Stellplatz") + rb("sOut", "Herausnehmen") + rbAdmin()) + `<div class="foot">${list.length} Fahrzeug(e) in der Ausstellung</div>`;
}

/* Bestellungen */
/* Status-Auswahl in der Tabellenzeile: zeigt nur erlaubte Wechsel */
function statusSelect(kind, id, cur, labels, allowedNext) {
  const opts = [cur, ...allowedNext.filter(x => x !== cur)];
  return `<select class="rowStatus st-${cur}" data-kind="${kind}" data-id="${esc(id)}" aria-label="Status ändern" ${opts.length < 2 ? "disabled" : ""}>` +
    opts.map(k => `<option value="${k}" ${k === cur ? "selected" : ""}>${labels[k]}</option>`).join("") + `</select>`;
}
function offerNext(o) { return o.status === "bestellt" ? [] : ["offen", "abgelehnt", "abgelaufen", "bestellt"]; }
function orderNext(o) {
  const v = o.vehicleId && byId("vehicles", o.vehicleId);
  switch (o.status) {
    case "bestellt": return ["eingetroffen", "storniert"];
    case "eingetroffen": return [...(v && v.source === "order" ? ["bestellt"] : []), ...(o.customerId && allowed("verkauf") ? ["ausgeliefert"] : []), "storniert"];
    case "ausgeliefert": return allowed("verkauf") ? ["eingetroffen"] : [];
    case "storniert": return v ? (["lager", "ausstellung"].includes(v.status) ? ["eingetroffen"] : []) : ["bestellt"];
  }
  return [];
}
function saleNext(s) { return s.status === "ok" ? ["storno"] : []; }
const offerSubject = o => { const v = o.vehicleId && byId("vehicles", o.vehicleId), m = o.modelId && byId("models", o.modelId);
  return v ? vLabel(v) : m ? `${m.model} – ${m.make}` : ""; };
const orderOfOffer = o => o.orderId ? byId("orders", o.orderId) : null;

/* Angebote */
function angebote() {
  const f = UI.filter.offer || "aktiv";
  const list = S.offers.filter(o => (f === "alle" || (f === "aktiv" ? o.status === "offen" : o.status === f)) &&
    match([o.nr, custName(o.customerId), offerSubject(o), o.color].join(" "))).sort((a, b) => b.nr.localeCompare(a.nr));
  const sum = list.filter(o => o.status === "offen").reduce((a, o) => a + o.price, 0);
  return head("Autohaus", "Angebote", "Schritt 1 von 3: Angebot an den Kunden. Zahlt oder zahlt er an, wird über den Status „Bestellt“ daraus eine Bestellung.") +
    `<div class="bar">${searchBox("Angebot, Kunde, Fahrzeug …")}<select id="fOffer" aria-label="Filter">${[["aktiv", "Offene Angebote"], ["bestellt", "Bestellt"], ["abgelehnt", "Abgelehnt"], ["abgelaufen", "Abgelaufen"], ["alle", "Alle Angebote"]].map(([k, l]) => `<option value="${k}" ${f === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    <span class="grow"></span>${btn("aNew", "Neues Angebot", "primary")}${btn("aDoc", "Angebot ansehen")}</div>` +
    tbl([["Nr."], ["Datum"], ["Kunde"], ["Fahrzeug / Modell"], ["Farbe", 0, 1], ["Preis", 1], ["Gültig bis"], ["Status"], ["Bestellung", 0, 1]],
      list.map(o => { const late = o.status === "offen" && o.validUntil && o.validUntil < today(), ord = orderOfOffer(o);
        return { id: o.id, cells: [o.nr, de(o.date), esc(custName(o.customerId)), esc(offerSubject(o)), esc(o.color), num(o.price),
          `<span class="${late ? "late" : ""}">${de(o.validUntil) || "–"}${late ? " · abgelaufen?" : ""}</span>`,
          statusSelect("offer", o.id, o.status, OFFER_STATUS, offerNext(o)), ord ? ord.nr : ""] }; }),
      S.offers.length ? "Keine Angebote für diesen Filter" : "Noch keine Angebote. Lege mit „Neues Angebot“ das erste an.",
      id => (byId("offers", id).status !== "bestellt" ? rb("aEdit", "Bearbeiten") : "") + rb("aDel", "Löschen", "danger") + rbAdmin()) +
    `<div class="foot">${list.length} Angebot(e)${sum ? ` · offenes Volumen ${money(sum)}` : ""}</div>`;
}

/* Bestellungen */
function bestellungen() {
  const list = S.orders.filter(o => { const m = byId("models", o.modelId) || {}; return (!UI.filter.openOnly || ["bestellt", "eingetroffen"].includes(o.status)) &&
    match([o.nr, m.make, m.model, custName(o.customerId), o.color].join(" ")); }).sort((a, b) => b.nr.localeCompare(a.nr));
  const offerNr = id => S.offers.find(x => x.orderId === id)?.nr || "";
  return head("Autohaus", "Bestellungen", "Schritt 2 von 3: Bestellung mit Anzahlung. Status „Eingetroffen“ bucht das Fahrzeug ein, „Ausgeliefert“ erstellt mit der Übergabe den Verkauf.") +
    `<div class="bar">${searchBox("Bestellung, Modell, Kunde …")}<label style="display:flex;gap:6px;align-items:center"><input id="fOpen" type="checkbox" ${UI.filter.openOnly ? "checked" : ""}> nur offene</label>
    <span class="grow"></span>${btn("oNew", "Neue Bestellung", "primary")}${btn("oDoc", "Bestellbestätigung")}</div>` +
    tbl([["Nr."], ["Datum"], ["Modell"], ["Farbe", 0, 1], ["Für Kunde"], ["VK", 1], ["Anzahlung", 1], ["Offen", 1], ["Liefertermin"], ["Status"], ["Fahrzeug", 0, 1], ["Angebot", 0, 1]],
      list.map(o => { const m = byId("models", o.modelId), late = o.status === "bestellt" && o.expected && o.expected < today();
        return { id: o.id, cells: [o.nr, de(o.date), esc(m ? `${m.model} – ${m.make}` : ""), esc(o.color), esc(o.customerId ? custName(o.customerId) : "– Lager –"), num(o.vk), num(o.deposit), num(o.vk - o.deposit),
          `<span class="${late ? "late" : ""}">${de(o.expected) || "–"}${late ? " · überfällig" : ""}</span>`,
          statusSelect("order", o.id, o.status, ORDER_STATUS, orderNext(o)), o.vehicleId ? byId("vehicles", o.vehicleId)?.nr || "" : "", offerNr(o.id)] }; }),
      "Keine Bestellungen", id => (byId("orders", id).status === "bestellt" ? rb("oEdit", "Bearbeiten") : "") + rb("oDel", "Löschen", "danger") + rbAdmin());
}

/* Verkauf */
function verkauf() {
  const y = UI.filter.year;
  const list = S.sales.filter(s => (!y || s.date.startsWith(y)) && match([s.nr, custName(s.customerId), vLabel(byId("vehicles", s.vehicleId)), s.seller].join(" "))).sort((a, b) => b.nr.localeCompare(a.nr));
  const ok = list.filter(s => s.status === "ok"), tot = ok.reduce((a, s) => a + s.price, 0), mg = ok.reduce((a, s) => a + s.price - s.ek, 0);
  const years = [...new Set(S.sales.map(s => s.date.slice(0, 4)).concat(String(new Date().getFullYear())))].sort().reverse();
  const bySeller = {}; ok.forEach(s => { const b = bySeller[s.seller] ||= [0, 0, 0]; b[0]++; b[1] += s.price; b[2] += s.price - s.ek; });
  return head("Autohaus", "Verkauf", "Schritt 3 von 3: Übergabe mit Restzahlung. Entsteht aus einer ausgelieferten Bestellung oder direkt über „Neuer Verkauf“.") +
    `<div class="bar">${searchBox("Vertrag, Käufer, Fahrzeug …")}<select id="fYear" aria-label="Jahr"><option value="">Alle Jahre</option>${years.map(x => `<option ${x === y ? "selected" : ""}>${x}</option>`).join("")}</select>
    <span class="grow"></span>${btn("vSell", "Neuer Verkauf", "primary")}</div>` +
    tbl([["Nr."], ["Datum"], ["Fahrzeug"], ["Käufer"], ["Kaufpreis", 1], ["Anzahlung", 1], ["Inzahlungn.", 1], ["Restzahlung", 1], ["EK", 1], ["Rohertrag", 1], ["Zahlung", 0, 1], ["Verkäufer", 0, 1], ["Status"], ["Bestellung", 0, 1]],
      list.map(s => ({ id: s.id, cells: [s.nr, de(s.date), esc(vLabel(byId("vehicles", s.vehicleId))), esc(custName(s.customerId)), num(s.price), s.deposit ? num(s.deposit) : "–", s.tradeValue ? num(s.tradeValue) : "–", num(s.price - s.tradeValue - s.deposit), num(s.ek),
        num(s.price - s.ek), PAY[s.pay], esc(s.seller), statusSelect("sale", s.id, s.status, SALE_STATUS, saleNext(s)), s.orderId ? byId("orders", s.orderId)?.nr || "" : ""] })), "Keine Verkäufe im gewählten Zeitraum", id => rb("kDoc", "Vertrag") + rb("kDel", "Löschen", "danger") + rbAdmin()) +
    `<div class="foot">${ok.length} Verkauf/Verkäufe · Umsatz ${money(tot)} · Rohertrag ${money(mg)}</div>
    <div style="margin-top:18px"><h3 class="eyebrow">Nach Verkäufer</h3>${tbl([["Verkäufer"], ["Anzahl", 1], ["Umsatz", 1], ["Rohertrag", 1]],
      Object.entries(bySeller).sort((a, b) => b[1][1] - a[1][1]).map(([n, [c, t, m]]) => ({ id: "s-" + n, cells: [esc(n), c, num(t), num(m)] })), "Keine Daten")}</div>`;
}

/* Ankauf */
function purchaseNext(p) {
  const v = byId("vehicles", p.vehicleId);
  if (p.status === "inzahlungnahme") return ["angekauft", "storniert"];
  if (p.status === "angekauft") return v && ["lager", "ausstellung"].includes(v.status) ? ["storniert"] : [];
  return [];
}
function ankauf() {
  const f = UI.filter.purchase || "";
  const list = S.purchases.filter(p => { const v = byId("vehicles", p.vehicleId) || {}; return (!f || p.status === f) && match([p.nr, custName(p.customerId), v.make, v.model, v.plate, v.vin].join(" ")); }).sort((a, b) => b.nr.localeCompare(a.nr));
  const open = S.purchases.filter(p => p.status === "inzahlungnahme");
  return head("Autohaus", "Gebrauchtwagen-Ankauf", "Ankauf: Fahrzeug wird sofort bezahlt und steht im Bestand. Inzahlungnahme: Fahrzeug wird vorgemerkt und bei der Übergabe eines verkauften Fahrzeugs verrechnet.") +
    `<div class="bar">${searchBox("Ankauf, Verkäufer, Kennzeichen …")}<select id="fPurchase" aria-label="Status"><option value="">Alle</option>${Object.entries(PURCHASE_STATUS).map(([k, l]) => `<option value="${k}" ${f === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    <span class="grow"></span>${btn("pNew", "Neuer Ankauf", "primary")}</div>` +
    tbl([["Nr."], ["Datum"], ["Fahrzeug"], ["Kennzeichen"], ["km", 1], ["Kunde"], ["Preis / Anrechnung", 1], ["Geplanter VK", 1], ["Status"], ["Fahrzeugstatus"], ["Verrechnet mit", 0, 1]],
      list.map(p => { const v = byId("vehicles", p.vehicleId), sale = p.saleId && byId("sales", p.saleId);
        return { id: p.id, cells: [p.nr, de(p.date), esc(v.nr + " " + vName(v)), esc(v.plate), intf.format(v.km), esc(custName(p.customerId)), num(p.price), num(v.vk),
          statusSelect("purchase", p.id, p.status, PURCHASE_STATUS, purchaseNext(p)), pill(v.status, STATUS[v.status]), sale ? sale.nr : ""] }; }),
      S.purchases.length ? "Keine Einträge für diesen Filter" : "Noch keine Ankäufe", id => rb("pDoc", "Vertrag") + rbAdmin()) +
    `<div class="foot">${list.length} Eintrag/Einträge${open.length ? ` · ${open.length} offene Inzahlungnahme(n) über ${money(open.reduce((a, p) => a + p.price, 0))}` : ""}</div>`;
}

/* Kunden */
function kunden() {
  const list = S.customers.filter(c => match([c.nr, c.name, c.phone, c.email].join(" "))).sort((a, b) => a.name.localeCompare(b.name));
  return head("Stammdaten", "Kunden", "Kundenkartei mit vollständiger Fahrzeughistorie") +
    `<div class="bar">${searchBox("Name, Telefon …")}<span class="grow"></span>${btn("cNew", "Neuer Kunde", "primary")}</div>` +
    tbl([["Nr."], ["Name"], ["Telefon"], ["E-Mail", 0, 1], ["Käufe", 1], ["Umsatz", 1], ["Notizen", 0, 1]],
      list.map(c => { const s = S.sales.filter(x => x.customerId === c.id && x.status === "ok"); return { id: c.id, cells: [c.nr, esc(c.name), esc(c.phone), esc(c.email), s.length, num(s.reduce((a, x) => a + x.price, 0)), esc(c.notes)] }; }),
      "Noch keine Kunden", id => rb("cEdit", "Bearbeiten") + rb("cHist", "Historie") + rb("cDel", "Löschen", "danger") + rbAdmin());
}

/* Katalog */
const CAT_FILTER = { make: "", cat: "", ekMin: "", ekMax: "", vkMin: "", vkMax: "", marginMin: "", status: "alle", stock: "" };
UI.cf = { ...CAT_FILTER };
UI.cfOpen = false;
function fnum(v) { if (v === "" || v === undefined) return null; try { return parseMoney(v); } catch (e) { return null; } }
function catFilterCount() { return Object.entries(CAT_FILTER).filter(([k, v]) => String(UI.cf[k] ?? "") !== String(v)).length; }

function katalog() {
  const inStock = {}; S.vehicles.filter(v => AVAILABLE.includes(v.status)).forEach(v => inStock[v.modelId] = (inStock[v.modelId] || 0) + 1);
  const f = UI.cf, ekMin = fnum(f.ekMin), ekMax = fnum(f.ekMax), vkMin = fnum(f.vkMin), vkMax = fnum(f.vkMax), mMin = fnum(f.marginMin);
  const pct = m => m.vk ? (m.vk - m.ek) / m.vk * 100 : 0;
  const list = S.models.filter(m => match([m.nr, m.make, m.model, m.cat].join(" "))
    && (!f.make || m.make === f.make) && (!f.cat || m.cat === f.cat)
    && (ekMin === null || m.ek >= ekMin) && (ekMax === null || m.ek <= ekMax)
    && (vkMin === null || m.vk >= vkMin) && (vkMax === null || m.vk <= vkMax)
    && (mMin === null || pct(m) >= mMin)
    && (f.status === "alle" || (f.status === "aktiv") === m.active)
    && (!f.stock || (f.stock === "ja") === !!inStock[m.id]))
    .sort((a, b) => (a.model + a.make).localeCompare(b.model + b.make));
  const canOrder = allowed("bestellungen");
  const makes = [...new Set(S.models.map(m => m.make))].sort((a, b) => a.localeCompare(b));
  const cats = [...new Set(S.models.map(m => m.cat).filter(Boolean).concat(CATS))].sort((a, b) => a.localeCompare(b));
  const n = catFilterCount();
  const sel = (id, label, options, val) => `<label>${label}<select id="${id}" data-cf>${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(val) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  const inp = (id, label, val, hint) => `<label>${label}<input id="${id}" data-cf inputmode="decimal" value="${esc(val)}" placeholder="${hint}"></label>`;
  const panel = UI.cfOpen ? `<div class="filters">
      ${sel("cf_make", "Hersteller", [["", "Alle Hersteller"], ...makes.map(x => [x, x])], f.make)}
      ${sel("cf_cat", "Klasse", [["", "Alle Klassen"], ...cats.map(x => [x, x])], f.cat)}
      ${sel("cf_status", "Status", [["alle", "Aktive und inaktive"], ["aktiv", "Nur aktive"], ["inaktiv", "Nur inaktive"]], f.status)}
      ${sel("cf_stock", "Im Bestand", [["", "Egal"], ["ja", "Mindestens 1 im Bestand"], ["nein", "Nicht im Bestand"]], f.stock)}
      <fieldset class="range"><legend>Einkaufspreis (EK)</legend>${inp("cf_ekMin", "von", f.ekMin, "0")}${inp("cf_ekMax", "bis", f.ekMax, "unbegrenzt")}</fieldset>
      <fieldset class="range"><legend>Verkaufspreis (VK)</legend>${inp("cf_vkMin", "von", f.vkMin, "0")}${inp("cf_vkMax", "bis", f.vkMax, "unbegrenzt")}</fieldset>
      ${inp("cf_marginMin", "Marge mindestens (%)", f.marginMin, "z. B. 20")}
      <div class="fend">${btn("cfReset", "Filter zurücksetzen")}</div></div>` : "";
  return head("Stammdaten", "Fahrzeugkatalog", "Modelle mit Standard-Einkaufs- und Verkaufspreis. Grundlage für Bestellungen und neue Fahrzeuge.") +
    `<div class="bar">${searchBox("Modell, Hersteller, Klasse …")}<button class="btn ${n ? "on" : ""}" data-a="cfToggle" aria-expanded="${UI.cfOpen}">Filter${n ? ` · ${n} aktiv` : ""} ${UI.cfOpen ? "▴" : "▾"}</button>
    <span class="grow"></span>${btn("mNew", "Neues Modell", "primary")}${btn("mImport", "CSV-Import")}</div>${panel}` +
    tbl([["Nr."], ["Modell"], ["Hersteller"], ["Klasse", 0, 1], ["EK", 1], ["VK", 1], ["Marge", 1], ["Marge %", 1], ["Im Bestand", 1], ["Aktiv"]],
      list.map(m => ({ id: m.id, cells: [m.nr, esc(m.model), esc(m.make), esc(m.cat), num(m.ek), num(m.vk), num(m.vk - m.ek), m.vk ? pct(m).toFixed(1).replace(".", ",") + " %" : "–", inStock[m.id] || 0, m.active ? "ja" : "nein"] })),
      S.models.length ? "Kein Modell passt zu den Filtern." : "Noch keine Modelle. Lege ein Modell an oder importiere deine Tabelle als CSV-Datei.",
      id => (canOrder && byId("models", id).active ? rb("mOrderRow", "Bestellen", "gold") : "") + rb("mEdit", "Bearbeiten") + rb("mDel", "Löschen", "danger") + rbAdmin()) +
    `<div class="foot">${list.length} von ${S.models.length} Modell(en)${list.length ? ` · EK ${money(Math.min(...list.map(m => m.ek)))} bis ${money(Math.max(...list.map(m => m.ek)))}` : ""}</div>`;
}

/* Firmendaten */
function einstellungen() {
  return head("System", "Firmendaten", "Der Firmenname erscheint im Schriftzug und auf den Verträgen") +
    `<form class="settings" id="setForm"><div class="card form">
      <label class="full">Firmenname<input id="setCompany" value="${esc(S.settings.company)}"></label>
      <div class="sec"><button class="btn primary" type="submit">Speichern</button></div></div></form>
    <div class="settings"><div class="card logoCard">
      <b>Logo</b>
      <p>Erscheint im Anmeldefenster, in der Seitenleiste und als Symbol im Browser-Tab. Am besten ein PNG mit transparentem Hintergrund,
      quadratisch und mindestens 512 × 512 Pixel (höchstens 4 MB). Das Bild wird unverändert in voller Auflösung verwendet.</p>
      <div class="logoRow"><img class="logoPreview" src="/logo?v=${encodeURIComponent(S.settings.logoVersion)}" alt="Aktuelles Logo">
        <div class="logoBtns"><label class="btn primary fileBtn">Logo hochladen …<input type="file" id="logoFile" accept="image/png,image/jpeg,image/webp" hidden></label>
        ${S.settings.customLogo ? btn("resetLogo", "Standardlogo wiederherstellen") : ""}</div></div></div></div>
    <div class="settings"><div class="card danger-zone">
      <b>Gefahrenbereich: Datenbank bereinigen</b>
      <p>Entfernt alle Fahrzeuge, Kunden, Angebote, Bestellungen, Verkäufe, Ankäufe und Buchungen, zum Beispiel nach einer Testphase oder zum Neustart.
      Benutzer, Firmendaten und Kontenplan bleiben erhalten. Das lässt sich nicht rückgängig machen.</p>
      <div style="margin-top:12px">${btn("wipe", "Datenbank bereinigen …", "danger")}</div></div></div>`;
}

/* Benutzer */
function benutzer() {
  const list = (S.users || []).filter(u => match([u.username, u.fullName, S.roles[u.role]].join(" ")));
  return head("System", "Benutzer", "Jeder Mitarbeiter bekommt einen eigenen Zugang. Die Rolle bestimmt, welche Bereiche sichtbar sind.") +
    `<div class="bar">${searchBox("Benutzer …")}<span class="grow"></span>${btn("uNew", "Neuer Benutzer", "primary")}</div>` +
    tbl([["Benutzername"], ["Name"], ["Rolle"], ["Aktiv"]], list.map(u => ({ id: u.id, cells: [esc(u.username), esc(u.fullName), esc(S.roles[u.role] || u.role), u.active ? "ja" : pill("storno", "deaktiviert")] })), "Keine Benutzer", id => rb("uEdit", "Bearbeiten") + rbAdmin()) +
    `<p class="foot">Rollen: Administrator sieht alles · Vertrieb: Verkauf, Bestand, Kunden, Bestellungen · Einkauf: Katalog, Bestellungen, Ankauf · Lager: Bestand und Ausstellung · Buchhaltung: Verkäufe, Ankäufe, Kunden.</p>`;
}

/* ------------------------------------------------------------------ Dialoge */
function modal(html, wide) {
  $("#layer").innerHTML = `<div class="overlay" id="ov"><div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div></div>`;
  const f = $("#layer").querySelector("input:not([type=checkbox]), select, textarea, button"); f && f.focus();
}
function close() { $("#layer").innerHTML = ""; }
function toast(t) {
  const el = document.createElement("div"); el.className = "toast"; el.setAttribute("role", "status"); el.textContent = t;
  document.body.appendChild(el); setTimeout(() => el.remove(), 3500);
}
function opts(list, val, empty) { return (empty !== undefined ? `<option value="">${empty}</option>` : "") + list.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(val ?? "") ? "selected" : ""}>${esc(l)}</option>`).join(""); }
const modelOpts = () => S.models.filter(m => m.active).sort((a, b) => (a.model + a.make).localeCompare(b.model + b.make, "de")).map(m => [m.id, `${m.model} – ${m.make} · VK ${money(m.vk)}`]);
const custOpts = () => S.customers.filter(c => c.active !== false).sort((a, b) => a.name.localeCompare(b.name)).map(c => [c.id, c.name]);

/* fields = [key, label, type, extra]; type: text|money|int|date|select|check|area|sec|calc|password */
/* modelFill: welche Formularfelder bei Auswahl eines Katalogmodells befüllt werden, z. B. { make: "make", ek: "ek" }
   (Formularfeld -> Feld des Katalogmodells). fillNow: auch beim Öffnen sofort befüllen (neue Datensätze). */
function form({ title, sub, fields, values = {}, submit = "Speichern", onSubmit, wide, onInput, noCancel, modelFill, fillNow, danger }) {
  const f = fields.map(([k, label, type, extra = {}]) => {
    const v = values[k] ?? "", id = "f_" + k, cls = extra.full ? "full" : "";
    if (type === "sec") return `<div class="sec eyebrow">${label}</div>`;
    if (type === "calc") return `<div class="calc" id="calc"></div>`;
    if (type === "check") return `<label class="check ${cls || "full"}"><input type="checkbox" id="${id}" ${v ? "checked" : ""}>${label}</label>`;
    if (type === "select") return `<label class="${cls}">${label}<select id="${id}">${opts(extra.options, v, extra.empty)}</select></label>`;
    if (type === "area") return `<label class="full">${label}<textarea id="${id}" rows="2">${esc(v)}</textarea></label>`;
    const t = type === "date" ? "date" : type === "password" ? "password" : "text";
    const im = type === "money" || type === "int" ? ' inputmode="decimal"' : "";
    const ac = type === "password" ? ` autocomplete="${extra.ac || "new-password"}"` : "";
    const val = type === "money" && v !== "" ? num(v) : v;
    return `<label class="${cls}">${label}<input id="${id}" type="${t}"${im}${ac} value="${esc(val)}" placeholder="${esc(extra.hint || "")}"></label>`;
  }).join("");
  modal(`<h2>${title}</h2>${sub ? `<p class="sub">${sub}</p>` : ""}<form id="mf" novalidate><div class="form">${f}</div><p class="err" id="err" role="alert"></p>
    <div class="actions">${noCancel ? "" : `<button type="button" class="btn" data-a="close">Abbrechen</button>`}<button type="submit" class="btn ${danger ? "dangerBtn" : "primary"}">${submit}</button></div></form>`, wide);
  const read = () => Object.fromEntries(fields.filter(x => !["sec", "calc"].includes(x[2])).map(([k, , type]) => { const el = $("#f_" + k); return [k, type === "check" ? el.checked : el.value]; }));
  $("#mf").addEventListener("submit", async e => {
    e.preventDefault();
    const box = $("#mf"); box.classList.add("busy");
    try { const msg = await onSubmit(read()); close(); render(); if (msg) toast(msg); }
    catch (err) { $("#err").textContent = err instanceof Fehler ? err.message : "Unerwarteter Fehler: " + err.message; if (!(err instanceof Fehler)) console.error(err); }
    finally { box.classList.remove("busy"); }
  });
  if (modelFill && $("#f_modelId")) {
    const fill = () => {
      const m = byId("models", $("#f_modelId").value); if (!m) return;
      for (const [field, src] of Object.entries(modelFill)) {
        const el = $("#f_" + field); if (!el) continue;
        const val = m[src];
        if (el.tagName === "SELECT") {
          if (val && ![...el.options].some(o => o.value === val)) el.add(new Option(val, val));
          el.value = val || "";
        } else el.value = typeof val === "number" ? num(val) : (val ?? "");
      }
      $("#f_modelId").dispatchEvent(new Event("input", { bubbles: true }));   // Vorschau aktualisieren
    };
    $("#f_modelId").addEventListener("change", fill);
    if (fillNow) fill();
  }
  if (onInput) { const run = () => { try { onInput(read()); } catch (e) { /* Vorschau ignoriert ungültige Eingaben */ } }; $("#mf").addEventListener("input", run); run(); }
}
function ask(title, text, okLabel, fn, danger) {
  modal(`<h2>${title}</h2><p class="sub">${text}</p><p class="err" id="err" role="alert"></p><div class="actions"><button class="btn" data-a="close">Abbrechen</button><button class="btn ${danger ? "dangerBtn" : "primary"}" id="okb">${okLabel}</button></div>`);
  $("#okb").onclick = async () => { $("#okb").disabled = true; try { const m = await fn(); close(); render(); if (m) toast(m); } catch (e) { $("#err").textContent = e.message; $("#okb").disabled = false; } };
}
function info(title, html, wide) { modal(`<h2>${title}</h2>${html}<div class="actions"><button class="btn primary" data-a="close">Schließen</button></div>`, wide); }

function selected(kind, what) {
  const id = UI.sel[UI.view];
  const obj = id && byId(kind, id);
  if (!obj) { toast(`Bitte zuerst ${what} in der Tabelle auswählen.`); return null; }
  return obj;
}
const done = r => r.message;

const vehicleFields = (isNew, locked) => [
  ["modelId", "Katalogmodell", "select", { options: modelOpts(), empty: "– ohne Katalog –", full: true }],
  ["model", "Modell", "text", { hint: "leer = aus Katalog" }], ["make", "Hersteller", "text", { hint: "leer = aus Katalog" }],
  ["cat", "Klasse", "select", { options: CATS.map(c => [c, c]), empty: "–" }], ["cond", "Zustand", "select", { options: [["neu", "Neu"], ["gebraucht", "Gebraucht"]] }],
  ["color", "Farbe"], ["plate", "Kennzeichen"], ["vin", "Fahrgestellnummer (FIN)"], ["year", "Baujahr", "int"], ["km", "Kilometerstand", "int"],
  ["inDate", "Eingangsdatum", "date"], ["ek", "Einkaufspreis" + (locked ? " · fest" : ""), "money", { hint: isNew ? "leer = Katalogpreis" : "" }],
  ["vk", "Verkaufspreis", "money", { hint: isNew ? "leer = Katalogpreis" : "" }], ["loc", "Standort / Stellplatz"],
  ["notes", "Notizen", "area"]];

function vehicleDialog(v) {
  form({ title: v ? `Fahrzeug ${v.nr} bearbeiten` : "Neues Fahrzeug", sub: v ? esc(vName(v)) : "Wähle ein Katalogmodell: Modell, Hersteller, Klasse und Preise werden übernommen und können angepasst werden.",
    modelFill: { make: "make", model: "model", cat: "cat", ek: "ek", vk: "vk" },
    fields: vehicleFields(!v, v && ["verkauft", "ausgebucht"].includes(v.status)),
    values: v ? { ...v } : { cond: "neu", year: new Date().getFullYear(), inDate: today() },
    onSubmit: async d => { const r = await api("saveVehicle", { ...d, id: v?.id }); UI.sel.bestand = r.id; return r.message; } });
}
function showroomDialog(v) {
  const list = S.vehicles.filter(x => AVAILABLE.includes(x.status)).map(x => [x.id, vLabel(x) + " · " + STATUS[x.status]]);
  if (!list.length) return toast("Es ist kein Fahrzeug im Bestand.");
  form({ title: "Fahrzeug in die Ausstellung", fields: [["vid", "Fahrzeug", "select", { options: list, full: true }], ["loc", "Stellplatz", "text", { hint: "z. B. Halle 1 · Platz 3", full: true }]],
    values: { vid: v?.id, loc: v?.loc || "" }, submit: "Ausstellen", onSubmit: async d => { const r = await api("showroom", { id: d.vid, loc: d.loc }); UI.sel.ausstellung = d.vid; return r.message; } });
}
function reserveDialog(v) {
  if (!S.customers.length) return toast("Bitte zuerst einen Kunden anlegen.");
  form({ title: "Fahrzeug reservieren", sub: esc(vLabel(v)), fields: [["customerId", "Kunde", "select", { options: custOpts() }], ["until", "Reserviert bis", "date"]],
    submit: "Reservieren", onSubmit: async d => done(await api("reserve", { id: v.id, ...d })) });
}
function saleDialog(v) {
  const list = S.vehicles.filter(x => AVAILABLE.includes(x.status)).map(x => [x.id, `${vLabel(x)} · ${STATUS[x.status]} · VK ${money(x.vk)}`]);
  if (!list.length) return toast("Es ist kein verkaufbares Fahrzeug im Bestand.");
  if (!S.customers.length) return toast("Bitte zuerst den Käufer unter Kunden anlegen.");
  form({ title: "Fahrzeug verkaufen", sub: "Der Kaufvertrag wird sofort erstellt. Bei Inzahlungnahme kommt das Altfahrzeug automatisch in den Bestand.", wide: true,
    fields: [["vehicleId", "Fahrzeug", "select", { options: list, full: true }], ["customerId", "Käufer", "select", { options: custOpts() }], ["date", "Verkaufsdatum", "date"],
      ["price", "Verkaufspreis", "money", { hint: "leer = Listenpreis" }], ["pay", "Zahlungsart", "select", { options: Object.entries(PAY) }],
      ["tradePurchaseId", "Inzahlungnahme", "select", { options: tradeOpts(v?.resFor), empty: tradeOpts().length ? "– keine –" : "– keine offene Inzahlungnahme erfasst –", full: true }],
      ["c", "", "calc"], ["notes", "Vereinbarungen", "area"]],
    values: { vehicleId: v?.id, customerId: v?.resFor || "", date: today(), pay: "bank" }, submit: "Verkauf abschließen",
    onInput: d => {
      const x = byId("vehicles", d.vehicleId); if (!x) return;
      const price = d.price === "" ? x.vk : parseMoney(d.price), tv = byId("purchases", d.tradePurchaseId)?.price || 0;
      $("#calc").innerHTML = `Kaufpreis <b>${money(price)}</b> · EK ${money(x.ek)} · Rohertrag <b>${money(price - x.ek)}</b>${tv ? ` · abzgl. Inzahlungnahme ${money(tv)}` : ""} · Zahlbetrag <b>${money(price - tv)}</b>`;
    },
    onSubmit: async d => {
      const r = await api("sell", { vehicleId: d.vehicleId, customerId: d.customerId, date: d.date, price: d.price, pay: d.pay, notes: d.notes, tradePurchaseId: d.tradePurchaseId });
      UI.sel.verkauf = r.id; if (UI.view !== "verkauf") location.hash = "verkauf";
      return r.message;
    } });
}
function orderDialog(o, preset = {}) {
  if (!S.models.length) return toast("Bitte zuerst Modelle im Fahrzeugkatalog anlegen.");
  form({ title: o ? `Bestellung ${o.nr} bearbeiten` : "Neue Fahrzeugbestellung",
    fields: [["modelId", "Modell (Katalog)", "select", { options: modelOpts(), full: true }], ["color", "Farbe / Ausstattung"], ["customerId", "Für Kunde", "select", { options: custOpts(), empty: "– Lagerfahrzeug –" }],
      ["ek", "Einkaufspreis", "money", { hint: "leer = Katalogpreis" }], ["vk", "Vereinbarter Verkaufspreis", "money", { hint: "leer = Katalogpreis" }],
      ["deposit", "Anzahlung des Kunden", "money"], ["date", "Bestelldatum", "date"], ["expected", "Voraussichtliche Lieferung", "date"], ["notes", "Notizen", "area"]],
    modelFill: { ek: "ek", vk: "vk" }, fillNow: !o,
    values: o ? { ...o } : { date: today(), ...preset }, onSubmit: async d => { const r = await api("saveOrder", { ...d, id: o?.id }); UI.sel.bestellungen = r.id; return r.message; } });
}
function offerDialog(o) {
  if (!S.customers.length) return toast("Bitte zuerst den Kunden unter Kunden anlegen.");
  const vehicles = S.vehicles.filter(x => AVAILABLE.includes(x.status) || x.id === o?.vehicleId).map(x => [x.id, `${vLabel(x)} · VK ${money(x.vk)}`]);
  if (!S.models.length && !vehicles.length) return toast("Bitte zuerst Modelle im Fahrzeugkatalog oder Fahrzeuge im Bestand anlegen.");
  const validDefault = (() => { const d = new Date(); d.setDate(d.getDate() + 14); return iso(d); })();
  form({ title: o ? `Angebot ${o.nr} bearbeiten` : "Neues Angebot",
    sub: "Entweder ein Fahrzeug aus dem Bestand anbieten oder ein Modell aus dem Katalog, das bestellt wird.",
    fields: [["customerId", "Kunde", "select", { options: custOpts(), full: true }],
      ["vehicleId", "Fahrzeug aus dem Bestand", "select", { options: vehicles, empty: "– keins, Katalogmodell bestellen –", full: true }],
      ["modelId", "oder Katalogmodell", "select", { options: modelOpts(), empty: "–", full: true }],
      ["color", "Farbe / Ausstattung"], ["price", "Angebotspreis", "money", { hint: "leer = Listenpreis" }],
      ["date", "Angebotsdatum", "date"], ["validUntil", "Gültig bis", "date"], ["notes", "Notizen", "area"]],
    values: o ? { ...o } : { date: today(), validUntil: validDefault }, modelFill: { price: "vk" },
    onSubmit: async d => { const r = await api("saveOffer", { ...d, id: o?.id }); UI.sel.angebote = r.id; return r.message; } });
  $("#f_vehicleId").addEventListener("change", e => {
    const v = byId("vehicles", e.target.value); if (!v) return;
    $("#f_modelId").value = ""; $("#f_price").value = num(v.vk); if (v.color) $("#f_color").value = v.color;
  });
  $("#f_modelId").addEventListener("change", e => { if (e.target.value) $("#f_vehicleId").value = ""; });
}
function convertOfferDialog(o) {
  form({ title: `Angebot ${o.nr} → Bestellung`, sub: `${esc(custName(o.customerId))} · ${esc(offerSubject(o))} · ${money(o.price)}<br>Trage ein, was der Kunde bereits bezahlt hat. Bei Vollzahlung den vollen Preis eintragen.`,
    fields: [["deposit", "Bezahlt / Anzahlung", "money", { hint: "0 = noch nichts bezahlt" }], ["date", "Bestelldatum", "date"],
      ...(o.vehicleId ? [] : [["expected", "Voraussichtliche Lieferung", "date"]])],
    values: { date: today() }, submit: "Bestellung anlegen",
    onSubmit: async d => { const r = await api("offerStatus", { id: o.id, status: "bestellt", ...d }); UI.sel.bestellungen = r.id; location.hash = "bestellungen"; return r.message; } });
}
function receiveDialog(o) {
  form({ title: "Fahrzeugeingang " + o.nr, sub: "Das Fahrzeug wird in den Bestand gebucht" + (o.customerId ? " und für " + esc(custName(o.customerId)) + " reserviert." : "."),
    fields: [["vin", "Fahrgestellnummer (FIN)"], ["plate", "Kennzeichen"], ["year", "Baujahr", "int"], ["km", "Kilometerstand", "int"], ["date", "Eingangsdatum", "date"]],
    values: { year: new Date().getFullYear(), date: today() }, submit: "Eingang buchen",
    onSubmit: async d => done(await api("orderStatus", { id: o.id, status: "eingetroffen", ...d })) });
}
function deliverDialog(o) {
  const v = byId("vehicles", o.vehicleId);
  form({ title: `Übergabe ${o.nr} → Verkauf`, wide: true,
    sub: `${esc(custName(o.customerId))} · ${esc(vLabel(v))}<br>Mit der Übergabe und der Restzahlung wird der Kaufvertrag erstellt.`,
    fields: [["price", "Verkaufspreis", "money"], ["date", "Übergabedatum", "date"], ["pay", "Restzahlung per", "select", { options: Object.entries(PAY) }],
      ["tradePurchaseId", "Inzahlungnahme", "select", { options: tradeOpts(o.customerId), empty: tradeOpts().length ? "– keine –" : "– keine offene Inzahlungnahme erfasst –", full: true }],
      ["c", "", "calc"], ["notes", "Vereinbarungen", "area"]],
    values: { price: o.vk, date: today(), pay: "bank" }, submit: "Übergabe abschließen",
    onInput: d => {
      const price = d.price === "" ? o.vk : parseMoney(d.price), tv = byId("purchases", d.tradePurchaseId)?.price || 0;
      $("#calc").innerHTML = `Verkaufspreis <b>${money(price)}</b> · Anzahlung ${money(o.deposit)}${tv ? ` · abzgl. Inzahlungnahme ${money(tv)}` : ""} · <b>Restzahlung ${money(price - o.deposit - tv)}</b> · Rohertrag ${money(price - (v?.ek || 0))}`;
    },
    onSubmit: async d => {
      const r = await api("orderStatus", { id: o.id, status: "ausgeliefert", price: d.price, date: d.date, pay: d.pay, notes: d.notes, tradePurchaseId: d.tradePurchaseId });
      UI.sel.verkauf = r.id; location.hash = "verkauf"; return r.message;
    } });
}
/* Logo hochladen (Administratoren) – die Datei wird unverändert übertragen */
function uploadLogo(file) {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) return toast("Bitte ein Bild im Format PNG, JPG oder WebP wählen.");
  if (file.size > 4 * 1024 * 1024) return toast("Das Bild ist zu groß (höchstens 4 MB).");
  const r = new FileReader();
  r.onload = () => act(() => api("uploadLogo", { data: r.result }));
  r.onerror = () => toast("Die Datei konnte nicht gelesen werden.");
  r.readAsDataURL(file);
}

/* Administratoren: Eintrag endgültig löschen (ohne Statusprüfung, ohne Rückbuchung) */
const PURGE_KIND = { bestand: ["vehicle", "vehicles", "ein Fahrzeug"], ausstellung: ["vehicle", "vehicles", "ein Fahrzeug"], angebote: ["offer", "offers", "ein Angebot"],
  bestellungen: ["order", "orders", "eine Bestellung"], verkauf: ["sale", "sales", "einen Verkauf"], ankauf: ["purchase", "purchases", "einen Ankauf"],
  kunden: ["customer", "customers", "einen Kunden"], katalog: ["model", "models", "ein Modell"], benutzer: ["user", "users", "einen Benutzer"] };
function purgeEffects(kind, x) {
  const n = (list, f) => S[list].filter(f).length, out = [];
  const add = (cnt, one, many, what) => cnt && out.push(`${cnt} ${cnt === 1 ? one : many} ${what}`);
  if (kind === "vehicle") {
    add(n("sales", s => s.vehicleId === x.id), "Verkauf", "Verkäufe", "dieses Fahrzeugs werden mitgelöscht");
    add(n("purchases", p => p.vehicleId === x.id), "Ankauf", "Ankäufe", "dieses Fahrzeugs werden mitgelöscht");
    add(n("orders", o => o.vehicleId === x.id) + n("offers", o => o.vehicleId === x.id), "Bestellung/Angebot", "Bestellungen/Angebote", "verlieren den Fahrzeugbezug");
  } else if (kind === "customer") {
    add(n("sales", s => s.customerId === x.id), "Verkauf", "Verkäufe", "werden mitgelöscht");
    add(n("purchases", p => p.customerId === x.id), "Ankauf", "Ankäufe", "werden mitgelöscht");
    add(n("offers", o => o.customerId === x.id), "Angebot", "Angebote", "werden mitgelöscht");
    add(n("orders", o => o.customerId === x.id), "Bestellung", "Bestellungen", "werden zu Lagerbestellungen");
    add(n("vehicles", v => v.resFor === x.id), "Reservierung", "Reservierungen", "werden aufgehoben");
  } else if (kind === "model") {
    add(n("orders", o => o.modelId === x.id), "Bestellung", "Bestellungen", "dieses Modells werden mitgelöscht");
    add(n("vehicles", v => v.modelId === x.id), "Fahrzeug", "Fahrzeuge", "bleiben erhalten, verlieren aber den Katalogbezug");
  }
  return out;
}
function adminPurge() {
  const [kind, list, what] = PURGE_KIND[UI.view] || [];
  if (!kind) return;
  const x = selected(list, what); if (!x) return;
  const label = x.nr || x.username || "";
  const name = kind === "customer" ? ` (${esc(x.name)})` : kind === "vehicle" ? ` (${esc(vName(x))})` : kind === "model" ? ` (${esc(x.model)} – ${esc(x.make)})` : "";
  const fx = purgeEffects(kind, x);
  ask("Endgültig löschen", `<b>${esc(label)}${name}</b> wird vollständig aus der Datenbank entfernt, unabhängig vom Status. Es wird nichts zurückgebucht und kein Status angepasst.` +
    (fx.length ? `<br><br>Außerdem:<br>• ${fx.join("<br>• ")}` : "") + `<br><br><b>Das kann nicht rückgängig gemacht werden.</b>`, "Endgültig löschen",
    async () => { const r = await api("purge", { kind, id: x.id }); delete UI.sel[UI.view]; return r.message; }, true);
}
function wipeDialog() {
  const c = { v: S.vehicles.length, k: S.customers.length, s: S.sales.length, o: S.orders.length, a: S.offers.length, p: S.purchases.length };
  form({ title: "Datenbank bereinigen",
    sub: `Folgendes wird <b>endgültig gelöscht</b>: ${c.v} Fahrzeuge, ${c.k} Kunden, ${c.a} Angebote, ${c.o} Bestellungen, ${c.s} Verkäufe, ${c.p} Ankäufe sowie alle Buchungen und das Änderungsprotokoll. ` +
      `Benutzer, Firmendaten und Kontenplan bleiben erhalten. Die Belegnummern beginnen wieder von vorn.<br><br><b>Das kann nicht rückgängig gemacht werden.</b>`,
    fields: [["keepCatalog", `Fahrzeugkatalog behalten (${S.models.length} Modelle)`, "check"],
      ["confirmation", "Zur Bestätigung LÖSCHEN eintippen", "text", { hint: "LÖSCHEN", full: true }],
      ["password", "Dein Passwort", "password", { ac: "current-password", full: true }]],
    values: { keepCatalog: true }, submit: "Endgültig bereinigen", danger: true,
    onSubmit: async d => { const r = await api("wipe", d); UI.sel = {}; return r.message; } });
}
async function changeStatus(kind, id, to) {
  render();                                   // Auswahl zurücksetzen, bis der Wechsel bestätigt ist
  if (kind === "offer") {
    const o = byId("offers", id);
    if (to === "bestellt") return convertOfferDialog(o);
    return act(() => api("offerStatus", { id, status: to }));
  }
  if (kind === "purchase") {
    const p = byId("purchases", id);
    if (to === "angekauft") return form({ title: `${p.nr} doch ankaufen`, sub: `Die Inzahlungnahme wird zum normalen Ankauf: ${money(p.price)} werden an ${esc(custName(p.customerId))} ausgezahlt und das Fahrzeug kommt in den Bestand.`,
      fields: [["pay", "Auszahlung per", "select", { options: Object.entries(PAY) }]], values: { pay: p.pay || "bank" }, submit: "Ankauf buchen",
      onSubmit: async d => done(await api("purchaseStatus", { id, status: to, pay: d.pay })) });
    return ask(p.status === "inzahlungnahme" ? "Inzahlungnahme stornieren" : "Ankauf stornieren", `${p.nr} wirklich stornieren? Das Fahrzeug wird ausgebucht.`, "Stornieren",
      async () => done(await api("purchaseStatus", { id, status: to })));
  }
  if (kind === "sale") {
    const s = byId("sales", id);
    return ask("Verkauf stornieren", `${s.nr} wirklich stornieren? Die Buchung wird storniert. Das Fahrzeug geht zurück in den Bestand bzw. ist wieder für den Besteller reserviert.`, "Stornieren",
      async () => done(await api("saleStatus", { id, status: to })));
  }
  const o = byId("orders", id), from = o.status;
  if (to === "eingetroffen" && from === "bestellt") return receiveDialog(o);
  if (to === "ausgeliefert") return deliverDialog(o);
  const texts = {
    "eingetroffen>bestellt": ["Fahrzeugeingang zurücknehmen", `Das Fahrzeug von ${o.nr} wird wieder aus dem Bestand genommen und die Bestellung ist wieder offen.`, "Zurücknehmen"],
    "ausgeliefert>eingetroffen": ["Übergabe rückgängig machen", `Der Verkauf zu ${o.nr} wird storniert. Das Fahrzeug bleibt für den Kunden reserviert.`, "Verkauf stornieren"],
    "storniert": ["Bestellung stornieren", `${o.nr} wirklich stornieren?` + (from === "eingetroffen" ? " Das Fahrzeug bleibt als Lagerfahrzeug im Bestand." : ""), "Stornieren"],
  };
  const t = texts[`${from}>${to}`] || texts[to];
  if (t) return ask(t[0], t[1], t[2], async () => done(await api("orderStatus", { id, status: to })));
  return act(() => api("orderStatus", { id, status: to }));
}

/* Offene Inzahlungnahmen für die Auswahl beim Verkauf – die des Käufers zuerst */
function tradeOpts(customerId) {
  return S.purchases.filter(p => p.status === "inzahlungnahme")
    .sort((a, b) => (b.customerId === customerId) - (a.customerId === customerId) || a.nr.localeCompare(b.nr))
    .map(p => { const v = byId("vehicles", p.vehicleId); return [p.id, `${p.nr} · ${v ? vName(v) + (v.plate ? " (" + v.plate + ")" : "") : ""} · ${custName(p.customerId)} · ${money(p.price)}`]; });
}
function purchaseDialog() {
  if (!S.customers.length) return toast("Bitte zuerst den Verkäufer unter Kunden anlegen.");
  form({ title: "Gebrauchtwagen ankaufen / Inzahlungnahme", wide: true,
    sub: "„Angekauft“: Das Fahrzeug wird sofort bezahlt und steht im Bestand. „Inzahlungnahme“: Das Fahrzeug wird vorgemerkt und beim Verkauf eines Fahrzeugs an diesen Kunden verrechnet.",
    fields: [["status", "Status", "select", { options: [["angekauft", "Angekauft – sofort bezahlt"], ["inzahlungnahme", "Inzahlungnahme – wird mit einem Verkauf verrechnet"]], full: true }],
      ["customerId", "Kunde", "select", { options: custOpts() }], ["modelId", "Katalogmodell (optional)", "select", { options: modelOpts(), empty: "– ohne Katalog –" }],
      ["model", "Modell", "text", { hint: "leer = aus Katalog" }], ["make", "Hersteller", "text", { hint: "leer = aus Katalog" }], ["cat", "Klasse", "select", { options: CATS.map(c => [c, c]), empty: "–" }],
      ["color", "Farbe"], ["plate", "Kennzeichen"], ["vin", "Fahrgestellnummer (FIN)"], ["year", "Baujahr", "int"], ["km", "Kilometerstand", "int"],
      ["price", "Ankaufpreis / Anrechnungswert", "money"], ["vk", "Geplanter Verkaufspreis", "money"], ["pay", "Zahlungsart (nur bei Ankauf)", "select", { options: Object.entries(PAY) }], ["date", "Datum", "date"],
      ["pnotes", "Vereinbarungen / bekannte Mängel", "area"]],
    modelFill: { make: "make", model: "model", cat: "cat" },
    values: { date: today(), pay: "bank", status: "angekauft" }, submit: "Speichern",
    onSubmit: async d => { const r = await api("purchase", d); UI.sel.ankauf = r.id; return r.message; } });
}
function customerDialog(c) {
  form({ title: c ? "Kunde bearbeiten" : "Neuer Kunde", fields: [["name", "Name", "text", { full: true }], ["phone", "Telefon"], ["email", "E-Mail"], ["notes", "Notizen", "area"],
    ...(c ? [["active", "Aktiv", "check"]] : [])],
    values: c || {}, onSubmit: async d => { const r = await api("saveCustomer", { ...d, id: c?.id }); UI.sel.kunden = r.id; return r.message; } });
}
function modelDialog(m) {
  form({ title: m ? "Modell bearbeiten" : "Neues Modell", fields: [["model", "Modell"], ["make", "Hersteller / Marke"], ["cat", "Klasse", "select", { options: CATS.map(c => [c, c]), empty: "–" }],
    ["ek", "Einkaufspreis", "money"], ["vk", "Verkaufspreis", "money"], ["active", "Aktiv", "check"]],
    values: m || { active: true }, onSubmit: async d => { const r = await api("saveModel", { ...d, id: m?.id }); UI.sel.katalog = r.id; return r.message; } });
}
function userDialog(u) {
  form({ title: u ? `Benutzer ${esc(u.username)} bearbeiten` : "Neuer Benutzer",
    fields: [["username", "Benutzername"], ["fullName", "Name (erscheint als Verkäufer)"], ["role", "Rolle", "select", { options: Object.entries(S.roles) }],
      ["password", u ? "Neues Passwort (leer = unverändert)" : "Passwort", "password"], ["active", "Aktiv", "check"]],
    values: u || { role: "sales", active: true }, onSubmit: async d => { const r = await api("saveUser", { ...d, id: u?.id }); UI.sel.benutzer = r.id; return r.message; } });
}
function passwordDialog(forced) {
  form({ title: forced ? "Neues Passwort festlegen" : "Passwort ändern", noCancel: forced,
    sub: forced ? "Du verwendest noch das Standardpasswort. Lege jetzt ein eigenes Passwort fest." : "",
    fields: [["old", "Aktuelles Passwort", "password", { ac: "current-password" }], ["new", "Neues Passwort", "password"], ["new2", "Neues Passwort wiederholen", "password"]],
    values: forced ? { old: "admin" } : {}, submit: "Passwort speichern",
    onSubmit: async d => {
      if (d.new !== d.new2) throw new Fehler("Die neuen Passwörter stimmen nicht überein.");
      const r = await request("/api/password", { old: d.old, new: d.new }); await reload(); return r.message;
    } });
}
function importDialog() {
  modal(`<h2>CSV-Import Fahrzeugkatalog</h2><p class="sub">In Google Sheets: Datei → Herunterladen → CSV. Erkannte Spalten: Hersteller/Marke, Modell/Fahrzeug, Klasse/Kategorie, EK/Einkaufspreis, VK/Verkaufspreis. Vorhandene Modelle werden aktualisiert. Kundendaten werden nicht importiert.</p>
    <div class="form"><label class="full">CSV-Datei<input type="file" id="csvFile" accept=".csv,.txt,text/csv"></label>
    <label class="full">oder Inhalt einfügen<textarea id="csvText" rows="6" placeholder="Hersteller;Modell;Klasse;EK;VK"></textarea></label></div>
    <p class="err" id="err" role="alert"></p><div class="actions"><button class="btn" data-a="close">Abbrechen</button><button class="btn primary" id="doImp">Importieren</button></div>`);
  $("#csvFile").onchange = e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => $("#csvText").value = r.result; r.readAsText(f, "utf-8"); };
  $("#doImp").onclick = async () => { $("#doImp").disabled = true; try { const r = await api("importModels", { text: $("#csvText").value }); close(); render(); toast(r.message); } catch (e) { $("#err").textContent = e.message; $("#doImp").disabled = false; } };
}

/* Verträge (Ansicht) */
function paperHead(title, number, date, partner, role) {
  return `<div class="paper"><div class="ph"><div><b>${esc(S.settings.company.toUpperCase())}</b><small>Fahrzeughandel · Showroom</small></div><div style="text-align:right"><b style="font-family:var(--font-body);letter-spacing:0">${title}</b><small>Nr. ${number} · ${de(date)}</small></div></div>
    <p><small style="color:var(--ink-muted)">${role}</small><br><b>${esc(partner.name)}</b><br>Kundennr. ${esc(partner.nr)}${partner.phone ? " · Tel. " + esc(partner.phone) : ""}</p>`;
}
function vTable(v) {
  const rows = [["Fahrzeug", vName(v)], ["Klasse", v.cat], ["Zustand", v.cond === "neu" ? "Neu" : "Gebraucht"], ["Fahrgestellnummer", v.vin], ["Kennzeichen", v.plate], ["Farbe", v.color],
    ["Baujahr", v.year || ""], ["Kilometerstand", intf.format(v.km) + " km"], ["Interne Nr.", v.nr]].filter(r => r[1] !== "" && r[1] !== undefined);
  return `<table>${rows.map(r => `<tr><td>${r[0]}</td><td>${esc(r[1])}</td></tr>`).join("")}</table>`;
}
function sumTable(rows) { return `<table class="sum">${rows.map(([l, v, b]) => `<tr class="${b ? "b" : ""}"><td style="text-align:right;width:auto;color:inherit">${l}</td><td style="text-align:right;width:28%">${money(v)}</td></tr>`).join("")}</table>`; }
function contractSale(s) {
  const v = byId("vehicles", s.vehicleId), c = byId("customers", s.customerId), tv = s.tradeId && byId("vehicles", s.tradeId);
  const rows = [["Kaufpreis", s.price, 1]];
  if (tv) rows.push(["abzgl. Inzahlungnahme " + tv.nr, s.tradeValue]);
  if (s.deposit) rows.push(["abzgl. geleistete Anzahlung", s.deposit]);
  rows.push([`Restzahlung bei Übergabe (${PAY[s.pay]})`, s.price - s.tradeValue - s.deposit, 1]);
  info("Kaufvertrag " + s.nr, paperHead("Kaufvertrag", s.nr, s.date, c, "Käufer") + (s.status === "storno" ? `<div class="stamp">STORNIERT</div>` : "") +
    `<h4>Kaufgegenstand</h4>${vTable(v)}${tv ? `<h4>In Zahlung genommenes Fahrzeug</h4>${vTable(tv)}` : ""}<h4>Preis</h4>${sumTable(rows)}` +
    (s.notes ? `<p>Vereinbarungen: ${esc(s.notes)}</p>` : "") + `<p>Verkäufer: ${esc(s.seller)}</p><div class="sign"><div>Unterschrift Käufer</div><div>Unterschrift Verkäufer</div></div></div>` +
    `<p class="foot">Zum Drucken: Strg + P (am Mac: Cmd + P).</p>`, true);
}
function contractPurchase(p) {
  const v = byId("vehicles", p.vehicleId), c = byId("customers", p.customerId);
  const trade = ["inzahlungnahme", "verrechnet"].includes(p.status), title = trade ? "Inzahlungnahme" : "Ankaufvertrag", sale = p.saleId && byId("sales", p.saleId);
  info(title + " " + p.nr, paperHead(title, p.nr, p.date, c, "Verkäufer") + (p.status === "storniert" ? `<div class="stamp">STORNIERT</div>` : "") +
    `<h4>Fahrzeug</h4>${vTable(v)}<h4>Preis</h4>${sumTable([[trade ? "Anrechnungswert" : `Ankaufpreis (${PAY[p.pay]})`, p.price, 1]])}${trade ? `<p>${sale ? `Verrechnet mit Kaufvertrag ${sale.nr}.` : "Der Anrechnungswert wird mit dem Kaufpreis des neuen Fahrzeugs verrechnet."}</p>` : ""}${p.notes ? `<p>Vereinbarungen / Mängel: ${esc(p.notes)}</p>` : ""}
    <p style="font-size:11px;color:var(--ink-muted)">Der Verkäufer versichert, dass das Fahrzeug sein Eigentum ist, frei von Rechten Dritter ist und keine weiteren als die angegebenen Mängel bekannt sind.</p>
    <div class="sign"><div>Unterschrift Verkäufer</div><div>Unterschrift Ankäufer (${esc(p.buyer)})</div></div></div>`, true);
}
function contractOffer(o) {
  const c = byId("customers", o.customerId), v = o.vehicleId && byId("vehicles", o.vehicleId), m = o.modelId && byId("models", o.modelId);
  info("Angebot " + o.nr, paperHead("Angebot", o.nr, o.date, c, "Kunde") +
    `<h4>Fahrzeug</h4>${v ? vTable(v) : `<table><tr><td>Modell</td><td>${esc(m ? m.model + " – " + m.make : "")}</td></tr>${m?.cat ? `<tr><td>Klasse</td><td>${esc(m.cat)}</td></tr>` : ""}${o.color ? `<tr><td>Farbe</td><td>${esc(o.color)}</td></tr>` : ""}</table>`}
    <h4>Preis</h4>${sumTable([["Angebotspreis", o.price, 1]])}${o.validUntil ? `<p>Dieses Angebot ist gültig bis ${de(o.validUntil)}.</p>` : ""}${o.notes ? `<p>${esc(o.notes)}</p>` : ""}
    <div class="sign"><div>Unterschrift Kunde</div><div>Unterschrift Autohaus</div></div></div>`, true);
}
function contractOrder(o) {
  const m = byId("models", o.modelId), c = o.customerId ? byId("customers", o.customerId) : { name: "Lagerbestellung", nr: "–" };
  const rows = o.customerId ? [["Vereinbarter Kaufpreis", o.vk, 1], ...(o.deposit ? [["geleistete Anzahlung", o.deposit], ["Restbetrag bei Übergabe", o.vk - o.deposit, 1]] : [])] : [["Einkaufspreis", o.ek, 1]];
  info("Bestellung " + o.nr, paperHead("Fahrzeugbestellung", o.nr, o.date, c, "Besteller") +
    `<h4>Fahrzeug</h4><table><tr><td>Modell</td><td>${esc(m.make + " " + m.model)}</td></tr><tr><td>Klasse</td><td>${esc(m.cat)}</td></tr>${o.color ? `<tr><td>Farbe</td><td>${esc(o.color)}</td></tr>` : ""}${o.expected ? `<tr><td>Voraussichtliche Lieferung</td><td>${de(o.expected)}</td></tr>` : ""}</table>
    <h4>Preis</h4>${sumTable(rows)}${o.customerId ? `<div class="sign"><div>Unterschrift Besteller</div><div>Unterschrift Autohaus</div></div>` : ""}</div>`, true);
}

/* ------------------------------------------------------------------ Aktionen */
const veh = () => selected("vehicles", "ein Fahrzeug");
const ACTIONS = {
  close,
  logout: async () => { try { await request("/api/logout", {}); } catch (e) { /* trotzdem abmelden */ } S = null; showLogin(); },
  pw: () => passwordDialog(false),
  vNew: () => vehicleDialog(null),
  vEdit: () => { const v = veh(); v && vehicleDialog(v); },
  vShow: () => { const v = veh(); v && showroomDialog(v); },
  vRes: () => { const v = veh(); v && reserveDialog(v); },
  vUnres: () => { const v = veh(); if (v) act(() => api("unreserve", { id: v.id })); },
  vSell: () => { const id = UI.sel[UI.view]; const v = id && ["bestand", "ausstellung"].includes(UI.view) ? byId("vehicles", id) : null; saleDialog(v); },
  vRemove: () => { const v = veh(); v && form({ title: "Fahrzeug ausbuchen", sub: esc(vLabel(v)) + " wird ohne Verkauf aus dem Bestand genommen.", fields: [["reason", "Grund", "text", { hint: "z. B. Totalschaden, Eigenbedarf", full: true }]], submit: "Ausbuchen", onSubmit: async d => done(await api("removeVehicle", { id: v.id, reason: d.reason })) }); },
  sShow: () => showroomDialog(null),
  sMove: () => { const v = veh(); v && showroomDialog(v); },
  sOut: () => { const v = veh(); v && form({ title: "Aus der Ausstellung nehmen", sub: esc(vLabel(v)), fields: [["loc", "Neuer Standort", "text", { hint: "z. B. Lager hinten", full: true }]], submit: "Herausnehmen", onSubmit: async d => done(await api("showroomOut", { id: v.id, loc: d.loc })) }); },
  oNew: () => orderDialog(null),
  oEdit: () => { const o = selected("orders", "eine Bestellung"); o && orderDialog(o); },
  oRecv: () => { const o = selected("orders", "eine Bestellung"); o && form({ title: "Fahrzeugeingang " + o.nr, sub: "Das Fahrzeug wird angelegt" + (o.customerId ? " und für " + esc(custName(o.customerId)) + " reserviert." : "."),
    fields: [["vin", "Fahrgestellnummer (FIN)"], ["plate", "Kennzeichen"], ["year", "Baujahr", "int"], ["km", "Kilometerstand", "int"], ["date", "Eingangsdatum", "date"]], values: { year: new Date().getFullYear(), date: today() },
    submit: "Eingang buchen", onSubmit: async d => done(await api("receiveOrder", { id: o.id, ...d })) }); },
  oCancel: () => { const o = selected("orders", "eine Bestellung"); o && ask("Bestellung stornieren", `${o.nr} wirklich stornieren?`, "Stornieren", async () => done(await api("cancelOrder", { id: o.id }))); },
  oDoc: () => { const o = selected("orders", "eine Bestellung"); o && contractOrder(o); },
  kDoc: () => { const s = selected("sales", "einen Verkauf"); s && contractSale(s); },
  kCancel: () => { const s = selected("sales", "einen Verkauf"); s && ask("Verkauf stornieren", `${s.nr} wirklich stornieren? Das Fahrzeug geht zurück in den Bestand.`, "Stornieren", async () => done(await api("cancelSale", { id: s.id }))); },
  pNew: purchaseDialog,
  pDoc: () => { const p = selected("purchases", "einen Ankauf"); p && contractPurchase(p); },
  pCancel: () => { const p = selected("purchases", "einen Ankauf"); p && ask("Ankauf stornieren", `${p.nr} wirklich stornieren?`, "Stornieren", async () => done(await api("cancelPurchase", { id: p.id }))); },
  cNew: () => customerDialog(null),
  cEdit: () => { const c = selected("customers", "einen Kunden"); c && customerDialog(c); },
  cHist: () => { const c = selected("customers", "einen Kunden"); if (!c) return; const h = history(c.id);
    info("Fahrzeughistorie · " + esc(c.name), `<div class="tablewrap hist"><table><thead><tr><th>Datum</th><th>Vorgang</th><th>Beleg</th><th>Fahrzeug</th><th class="n">Betrag</th></tr></thead><tbody>${h.length ? h.map(r => `<tr><td>${de(r[0])}</td><td>${esc(r[1])}</td><td>${r[2]}</td><td>${esc(r[3])}</td><td class="n">${num(r[4])}</td></tr>`).join("") : `<tr><td colspan="5" class="empty">Noch keine Fahrzeugvorgänge.</td></tr>`}</tbody></table></div>`, true); },
  mNew: () => modelDialog(null),
  mEdit: () => { const m = selected("models", "ein Modell"); m && modelDialog(m); },
  mOrderRow: () => { const m = selected("models", "ein Modell"); m && orderDialog(null, { modelId: m.id }); },
  mDel: () => { const m = selected("models", "ein Modell"); m && ask("Modell löschen", `${esc(m.model)} – ${esc(m.make)} löschen? Wird das Modell noch verwendet, wird es nur deaktiviert.`, "Löschen", async () => done(await api("deleteModel", { id: m.id }))); },
  cDel: () => { const c = selected("customers", "einen Kunden"); c && ask("Kunde löschen", `${esc(c.name)} löschen? Hat der Kunde schon Vorgänge, wird er nur deaktiviert.`, "Löschen", async () => done(await api("deleteCustomer", { id: c.id }))); },
  cfToggle: () => { UI.cfOpen = !UI.cfOpen; render(); if (UI.cfOpen) $("#cf_make")?.focus(); },
  cfReset: () => { UI.cf = { ...CAT_FILTER }; render(); },
  mImport: importDialog,
  aNew: () => offerDialog(null),
  aEdit: () => { const o = selected("offers", "ein Angebot"); if (!o) return; if (o.status === "bestellt") return toast("Das Angebot ist bereits bestellt und kann nicht mehr geändert werden."); offerDialog(o); },
  aDoc: () => { const o = selected("offers", "ein Angebot"); o && contractOffer(o); },
  aDel: () => { const o = selected("offers", "ein Angebot"); o && ask("Angebot löschen", `Angebot ${o.nr} für ${esc(custName(o.customerId))} endgültig löschen?` + (o.orderId ? " Die daraus entstandene Bestellung bleibt erhalten." : ""), "Löschen", async () => done(await api("deleteOffer", { id: o.id }))); },
  oDel: () => { const o = selected("orders", "eine Bestellung"); o && ask("Bestellung löschen", `Bestellung ${o.nr} endgültig löschen?` + (o.status === "eingetroffen" ? " Ein bestelltes Fahrzeug wird wieder aus dem Bestand genommen, ein Bestandsfahrzeug wird freigegeben." : "") + (S.offers.some(x => x.orderId === o.id) ? " Das zugehörige Angebot wird wieder auf „Offen“ gesetzt." : ""), "Löschen", async () => done(await api("deleteOrder", { id: o.id }))); },
  kDel: () => { const s = selected("sales", "einen Verkauf"); s && ask("Verkauf löschen", `Verkauf ${s.nr} endgültig löschen?` + (s.status === "ok" ? " Der Verkauf wird dabei storniert: Buchung wird zurückgenommen, das Fahrzeug geht zurück in den Bestand." : ""), "Löschen", async () => done(await api("deleteSale", { id: s.id }))); },
  purge: () => adminPurge(),
  resetLogo: () => ask("Standardlogo wiederherstellen", "Das hochgeladene Logo wird entfernt und das mitgelieferte Logo wieder verwendet.", "Wiederherstellen",
    async () => done(await api("resetLogo", {}))),
  wipe: () => wipeDialog(),
  uNew: () => userDialog(null),
  uEdit: () => { const u = selected("users", "einen Benutzer"); u && userDialog(u); },
};
const DOUBLE = { bestand: "vEdit", ausstellung: "sMove", angebote: "aEdit", bestellungen: "oEdit", verkauf: "kDoc", ankauf: "pDoc", kunden: "cHist", katalog: "mEdit", benutzer: "uEdit" };
async function act(fn) { try { const r = await fn(); render(); r?.message && toast(r.message); } catch (e) { toast(e.message); } }

document.addEventListener("click", e => {
  const a = e.target.closest("[data-a]");
  if (a) {
    e.preventDefault();
    const row = a.closest("#main tbody tr[data-id]");
    if (row) { UI.sel[UI.view] = row.dataset.id; document.querySelectorAll("#main tbody tr.sel").forEach(x => x.classList.remove("sel")); row.classList.add("sel"); }
    ACTIONS[a.dataset.a]?.(a); return;
  }
  if (e.target.id === "ov" && $("#ov [data-a=close]")) { close(); return; }   // Pflicht-Dialoge bleiben offen
  const tr = e.target.closest("#main tbody tr[data-id]");
  if (tr) { UI.sel[UI.view] = tr.dataset.id; document.querySelectorAll("#main tbody tr.sel").forEach(x => x.classList.remove("sel")); tr.classList.add("sel"); }
});
document.addEventListener("dblclick", e => { if (!e.target.closest("button, select") && e.target.closest("#main tbody tr[data-id]") && DOUBLE[UI.view]) ACTIONS[DOUBLE[UI.view]](); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#ov") && $("#ov [data-a=close]")) close(); });
document.addEventListener("input", e => {
  if (e.target.id === "q") { UI.q[UI.view] = e.target.value; const pos = e.target.selectionStart; render(); const q = $("#q"); q.focus(); q.setSelectionRange(pos, pos); }
  if (e.target.matches("[data-cf]")) {           // Katalogfilter: sofort anwenden, Fokus behalten
    const id = e.target.id, isText = e.target.tagName === "INPUT", pos = isText ? e.target.selectionStart : null;
    UI.cf[id.slice(3)] = e.target.value; render();
    const el = document.getElementById(id); if (el) { el.focus(); if (isText) el.setSelectionRange(pos, pos); }
  }
});
document.addEventListener("change", e => {
  const t = e.target;
  if (t.id === "fStatus") { UI.filter.bestand = t.value; render(); }
  if (t.id === "fCond") { UI.filter.cond = t.value; render(); }
  if (t.id === "fOpen") { UI.filter.openOnly = t.checked; render(); }
  if (t.id === "fYear") { UI.filter.year = t.value; render(); }
  if (t.id === "fOffer") { UI.filter.offer = t.value; render(); }
  if (t.id === "fPurchase") { UI.filter.purchase = t.value; render(); }
  if (t.classList.contains("rowStatus")) changeStatus(t.dataset.kind, t.dataset.id, t.value);
  if (t.id === "logoFile" && t.files[0]) uploadLogo(t.files[0]);
});
document.addEventListener("submit", async e => {
  if (e.target.id !== "setForm") return;
  e.preventDefault();
  try { const r = await api("settings", { company: $("#setCompany").value }); render(); toast(r.message); }
  catch (err) { toast(err.message); }
});

/* ------------------------------------------------------------------ Anmeldung und Start */
function showLogin() {
  close(); $("#app").hidden = true; $("#login").hidden = false; $("#lPass").value = ""; setBrand("#loginTop", "#loginSub"); $("#lUser").focus();
}
function showApp() {
  $("#login").hidden = true; $("#app").hidden = false;
  route();
  if (S.me.mustChangePassword) passwordDialog(true);
}
$("#loginForm").addEventListener("submit", async e => {
  e.preventDefault(); $("#lErr").textContent = ""; $("#lBtn").disabled = true;
  try { S = await request("/api/login", { username: $("#lUser").value, password: $("#lPass").value }); showApp(); }
  catch (err) { $("#lErr").textContent = err.message; }
  finally { $("#lBtn").disabled = false; }
});
function route() {
  if (!S) return;
  const h = location.hash.slice(1);
  UI.view = VIEWS.some(v => v[0] === h) && allowed(h) ? h : (allowed("cockpit") ? "cockpit" : S.me.views[0]);
  if (!$("#ov [data-a=close]") && $("#ov")) return render();   // Pflicht-Dialog offen lassen
  close(); render();
}
window.addEventListener("hashchange", route);
/* Änderungen anderer Mitarbeiter regelmäßig nachladen, solange kein Dialog offen ist */
setInterval(async () => { if (S && !$("#ov") && document.visibilityState === "visible") { try { await reload(); render(); } catch (e) { /* nächster Versuch */ } } }, 60000);

(async () => {
  try { await reload(); showApp(); }
  catch (e) { showLogin(); }
})();
