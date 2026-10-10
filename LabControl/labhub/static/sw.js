// 量子工程平台 PWA：只快取入口頁殼層；API 一律走網路（離線時顯示上次的入口頁）。
const CACHE = "qel-portal-v1";
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(["/icon.svg", "/manifest.webmanifest"]))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
  if (u.pathname === "/portal") {
    e.respondWith(fetch(e.request).then(r => { const c = r.clone(); if (r.ok) caches.open(CACHE).then(x => x.put("/portal", c)); return r; })
      .catch(() => caches.match("/portal")));
  } else if (u.pathname === "/icon.svg" || u.pathname === "/manifest.webmanifest") {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});
