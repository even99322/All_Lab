// 量子工程平台 App（1.0.8）：可安裝的網頁程式。
// 頁面（/portal /data /live /setup …）先走網路、成功就留一份；斷線時開上次的畫面並顯示「離線」。
// /api/、登入、下載一律只走網路（不快取任何數據或帳號資料）。
const CACHE = "qel-app-v2";
const SHELL = ["/icon.svg", "/manifest.webmanifest", "/app-icon-192.png", "/app-icon-512.png",
               "/app-icon-maskable-512.png", "/app-icon-180.png", "/app-icon-32.png"];
const PAGES = ["/portal", "/data", "/live", "/setup", "/install", "/m", "/m/data"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function offline() {
  return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>量子工程平台</title><body style="margin:0;display:grid;place-items:center;height:100vh;background:#111317;color:#e9ecef;' +
    'font:16px system-ui,sans-serif;text-align:center"><div><img src="/icon.svg" width="64" alt=""><h2>連不到實驗室的 Hub</h2>' +
    '<p style="color:#aab1bb">請連上實驗室網路或 VPN 後再試一次。</p><button onclick="location.reload()" ' +
    'style="font:inherit;padding:8px 18px;border:0;border-radius:8px;background:#1971c2;color:#fff">重新整理</button></div>',
    {status: 503, headers: {"Content-Type": "text/html; charset=utf-8"}});
}

self.addEventListener("fetch", e => {
  const req = e.request;
  const u = new URL(req.url);
  if (req.method !== "GET" || u.origin !== location.origin) return;
  const path = u.pathname.replace(/\/$/, "") || "/";
  if (path.startsWith("/api/") || path.startsWith("/login") || path === "/logout" || path.startsWith("/get/")) return;
  if (SHELL.includes(path)) {
    e.respondWith(caches.match(path).then(r => r || fetch(req)));
    return;
  }
  if (req.mode === "navigate" || PAGES.includes(path)) {
    e.respondWith(fetch(req).then(r => {
      // 只存真正的頁面（被導去登入頁的不存）
      if (r.ok && !r.redirected && PAGES.includes(path)) {
        const c = r.clone();
        caches.open(CACHE).then(x => x.put(path, c));
      }
      return r;
    }).catch(() => caches.match(path).then(r => r || offline())));
  }
});
