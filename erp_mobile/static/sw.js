/* Service Worker: App-Gerüst offline verfügbar, Daten „Netz zuerst, sonst letzter Stand“. */
const SHELL = "pp-shell-v1";
const DATA = "pp-data-v1";
const FILES = ["./", "index.html", "app.js", "app.css", "manifest.webmanifest", "icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;          // Daten puffert die App selbst
  // App-Dateien: Netz zuerst (Updates sofort sichtbar), offline aus dem Speicher
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone();
    caches.open(SHELL).then((c) => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("index.html"))));
});
