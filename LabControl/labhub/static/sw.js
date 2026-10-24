// 量子工程平台 App（1.0.8）：可安裝的網頁程式。
// 頁面（/portal /data /live /setup …）先走網路、成功就留一份；斷線時開上次的畫面並顯示「離線」。
// /api/、登入、下載一律只走網路（不快取任何數據或帳號資料）。
// 1.0.25：推播通知（模擬完成、量測完成）——收到就顯示，點了打開對應的頁面。
const CACHE = "qel-app-v3";
const SHELL = ["/icon.svg", "/manifest.webmanifest", "/app-icon-192.png", "/app-icon-512.png",
               "/app-icon-maskable-512.png", "/app-icon-180.png", "/app-icon-32.png"];
const PAGES = ["/portal", "/data", "/live", "/setup", "/install", "/m", "/m/data", "/notify"];

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

// ---- 1.0.25：推播通知 ----
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = {body: e.data ? e.data.text() : ""}; }
  const opts = {body: d.body || "", icon: "/app-icon-192.png", data: {url: d.url || "/"},
                timestamp: d.time ? Math.round(d.time * 1000) : Date.now()};
  if (d.tag) { opts.tag = d.tag; opts.renotify = true; }
  e.waitUntil(self.registration.showNotification(d.title || "量子工程平台", opts));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "/", self.location.origin).href;
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(list => {
    for (const c of list) if (c.url === url && "focus" in c) return c.focus();
    for (const c of list) if ("navigate" in c) return c.navigate(url).then(w => w && w.focus()).catch(() => self.clients.openWindow(url));
    return self.clients.openWindow(url);
  }));
});

// 瀏覽器換了訂閱（過期）→ 用同樣的設定重新訂閱
self.addEventListener("pushsubscriptionchange", e => {
  e.waitUntil((async () => {
    const info = await fetch("/api/notify", {credentials: "same-origin"}).then(r => r.json());
    const old = e.oldSubscription ? await fetch("/api/notify/lookup", {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({endpoint: e.oldSubscription.endpoint})})
      .then(r => r.json()).then(j => j.sub).catch(() => null) : null;
    const key = Uint8Array.from(atob(info.key.replace(/-/g, "+").replace(/_/g, "/") + "==".slice(0, (4 - info.key.length % 4) % 4)), c => c.charCodeAt(0));
    const sub = e.newSubscription || await self.registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
    const body = {subscription: sub.toJSON(), origin: self.location.origin};
    if (old) Object.assign(body, {enabled: old.enabled, cats: old.cats, sources: old.sources});
    const res = await fetch("/api/notify/subscribe", {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}).then(r => r.json());
    if (old && res.sub && res.sub.id !== old.id)
      await fetch("/api/notify/sub/" + old.id, {method: "DELETE", credentials: "same-origin"});
  })().catch(() => {}));
});
