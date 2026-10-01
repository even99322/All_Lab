// Service worker：讓網站可以安裝成 App（Android、iOS、電腦），並在沒有網路時顯示快取的內容。
// 策略：
//  - 程式本身（/、/static/…）：先連網路拿最新版，失敗時用快取；pdf.js 等第三方檔案直接用快取。
//  - 資料（GET /api/…）：先連網路（最多等 8 秒），失敗時用上次看過的內容。
//  - PDF：讀過的最近 20 篇自動留一份；「離線保存」的論文另外存放、不會被擠掉。
//  - 會改資料的操作（新增、修改、刪除）不快取，離線時由頁面提示。
const VERSION = 'v1.5.1';
const SHELL = `pl-shell-${VERSION}`;
const VENDOR = 'pl-vendor-1';
const API = 'pl-api';
const PDF = 'pl-pdf';
const OFFLINE = 'pl-offline';
const KEEP = [SHELL, VENDOR, API, PDF, OFFLINE];
const PRECACHE = ['/', '/static/style.css', '/static/app.js', '/static/more.js', '/static/research.js', '/static/pwa.js', '/static/desk.js',
  '/static/graph.js', '/static/viewer.js', '/static/icons.js', '/static/help.js', '/static/ink.js', '/static/notebook.js', '/static/notes.js', '/static/icon-192.png', '/static/apple-touch-icon.png', '/manifest.webmanifest'];
const VENDOR_PRE = ['/static/vendor/pdfjs/pdf.min.mjs', '/static/vendor/pdfjs/pdf.worker.min.mjs'];
const NO_CACHE = [/^\/api\/auth\//, /^\/api\/jobs/, /^\/api\/exports\//, /^\/api\/figures-zip/, /\.csv(\?|$)/, /^\/api\/admin\//, /\.md(\?|$)/, /\/annotated$/];
const PDF_MAX = 20;
const API_MAX = 400;

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const s = await caches.open(SHELL);
    await Promise.all(PRECACHE.map((u) => s.add(new Request(u, { cache: 'reload' })).catch(() => {})));
    const v = await caches.open(VENDOR);
    await Promise.all(VENDOR_PRE.map(async (u) => { if (!(await v.match(u))) await v.add(u).catch(() => {}); }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (!KEEP.includes(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('message', (e) => {
  if (e.data === 'clear-user-data') {        // 登出：清掉這個帳號看過的資料
    e.waitUntil(Promise.all([caches.delete(API), caches.delete(PDF), caches.delete(OFFLINE)]));
  }
});

async function trim(name, max) {
  const c = await caches.open(name);
  const keys = await c.keys();
  for (let i = 0; i < keys.length - max; i++) await c.delete(keys[i]);
}

function timeout(ms) { return new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms)); }

async function networkFirst(req, cacheName, { wait = 8000, max = 0 } = {}) {
  try {
    const res = await Promise.race([fetch(req), timeout(wait)]);
    if (res.ok && res.type !== 'opaque') {
      const copy = res.clone();
      caches.open(cacheName).then(async (c) => { await c.delete(req); await c.put(req, copy); if (max) trim(cacheName, max); }).catch(() => {});
    }
    return res;
  } catch (err) {
    const hit = await caches.match(req);
    if (hit) {
      if (cacheName !== API) return hit;
      // 標記「這是裝置上的舊資料」，網頁會稍後自動重新抓
      const hd = new Headers(hit.headers); hd.set('X-PL-Stale', '1');
      return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers: hd });
    }
    if (req.mode === 'navigate') {
      const shell = await caches.match('/');
      if (shell) return shell;
    }
    return new Response(JSON.stringify({ detail: '離線中，這個內容還沒有保存在這台裝置' }), { status: 503, headers: { 'Content-Type': 'application/json', 'X-PL-Offline': '1' } });
  }
}

async function cacheFirst(req, cacheName) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) { const copy = res.clone(); caches.open(cacheName).then((c) => c.put(req, copy)).catch(() => {}); }
  return res;
}

// PDF 的分段讀取（pdf.js 用 Range 取部分內容）：有網路直接走網路；離線時從快取切出那一段
async function pdfRequest(req) {
  const range = req.headers.get('range');
  if (!range) return networkFirst(req, PDF, { wait: 15000, max: PDF_MAX });
  try {
    return await fetch(req);
  } catch (err) {
    const hit = await caches.match(req.url);
    if (!hit) return new Response('', { status: 503 });
    const buf = await hit.arrayBuffer();
    const m = /bytes=(\d+)-(\d*)/.exec(range) || [0, '0', ''];
    const start = +m[1], end = m[2] ? Math.min(+m[2], buf.byteLength - 1) : buf.byteLength - 1;
    return new Response(buf.slice(start, end + 1), { status: 206, headers: {
      'Content-Type': 'application/pdf', 'Content-Range': `bytes ${start}-${end}/${buf.byteLength}`, 'Content-Length': String(end - start + 1), 'Accept-Ranges': 'bytes' } });
  }
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;
  const p = url.pathname;
  if (NO_CACHE.some((r) => r.test(p + url.search))) return;
  if (p.startsWith('/static/vendor/')) return e.respondWith(cacheFirst(req, VENDOR));
  if (/^\/api\/files\/\d+\/content$/.test(p) && !url.searchParams.get('download')) return e.respondWith(pdfRequest(req));
  if (/^\/api\/(files\/\d+\/thumb|figures\/\d+\/image)$/.test(p) && !url.searchParams.get('download')) return e.respondWith(cacheFirst(req, API));
  if (/^\/api\/papers\/\d+\/(annotations|notebook)$/.test(p)) return e.respondWith(networkFirst(req, API, { wait: 25000, max: API_MAX }));
  if (p.startsWith('/api/')) return e.respondWith(networkFirst(req, API, { max: API_MAX }));
  if (req.mode === 'navigate' || p === '/' || p.startsWith('/static/') || p === '/manifest.webmanifest') return e.respondWith(networkFirst(req, SHELL, { wait: 6000 }));
});
