// 安裝成 App（Android、iOS、電腦）與離線閱讀。
import { h, icon, api, toast, fail, modal, confirmBox, fmtDate } from './app.js';

const LS = 'pl-offline-list';
const AUTO = 'pl-offline-auto';
let deferred = null;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
const hasSW = () => 'serviceWorker' in navigator && window.isSecureContext;

const readList = () => { try { return JSON.parse(localStorage.getItem(LS) || '[]'); } catch { return []; } };
const writeList = (l) => { try { localStorage.setItem(LS, JSON.stringify(l)); } catch { /* */ } };
export const isOfflineSaved = (pid) => readList().some((x) => x.id === pid);
const autoOn = () => { try { return localStorage.getItem(AUTO) !== '0'; } catch { return true; } };

// 網路錯誤轉成看得懂的訊息
export function netError(method, e) {
  if (!navigator.onLine || e instanceof TypeError) {
    return new Error(method === 'GET' ? '離線中，這個內容還沒有保存在這台裝置' : '離線中，無法儲存變更（連上網路後再試一次）');
  }
  return e;
}

// ---------------------------------------------------------------- 註冊 service worker、離線提示
function init() {
  if (hasSW()) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((e) => console.warn('service worker', e));
  }
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferred = e; });
  window.addEventListener('appinstalled', () => { deferred = null; toast('已安裝，可以從主畫面或應用程式清單開啟'); });
  const bar = h('div', { class: 'offline-bar', hidden: navigator.onLine, role: 'status' }, icon('offline'), h('span', {}, '離線中', h('span', { class: 'hide-mobile' }, '：顯示的是這台裝置保存的內容，修改會無法儲存')));
  document.body.append(bar);
  const upd = () => { bar.hidden = navigator.onLine; document.body.classList.toggle('is-offline', !navigator.onLine); };
  window.addEventListener('online', () => { upd(); toast('已恢復連線'); });
  window.addEventListener('offline', upd);
  upd();
  if (standalone()) document.documentElement.classList.add('standalone');
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else setTimeout(init, 0);

// 讀過的論文：PDF 在背景完整下載一份進快取（之後沒網路也能讀），最多 20 篇
export async function rememberPdf(fid) {
  if (!hasSW() || !autoOn() || !fid || !navigator.onLine) return;
  try {
    const url = `/api/files/${fid}/content`;
    if (await caches.match(url)) return;
    setTimeout(() => fetch(url, { credentials: 'same-origin' }).catch(() => {}), 4000);
  } catch { /* */ }
}

// ---------------------------------------------------------------- 離線保存單篇論文
export async function offlineSave(p, fileId) {
  if (!hasSW()) return toast('離線保存需要用 HTTPS 網址開啟網站', 'err');
  const list = readList();
  const c = await caches.open('pl-offline');
  if (list.some((x) => x.id === p.id)) {
    const x = list.find((y) => y.id === p.id);
    for (const u of x.urls || []) await c.delete(u);
    writeList(list.filter((y) => y.id !== p.id));
    return toast('已取消離線保存');
  }
  toast('下載中…');
  const urls = [`/api/papers/${p.id}`, `/api/papers/${p.id}/annotations`, `/api/papers/${p.id}/params`, `/api/figures?paper=${p.id}&limit=200`];
  for (const f of p.files || []) urls.push(`/api/files/${f.id}/content`, `/api/files/${f.id}/thumb`);
  try {
    const figs = await api.get(`/api/figures?paper=${p.id}&limit=200`);
    figs.items.forEach((f) => urls.push(`/api/figures/${f.id}/image`));
    let n = 0;
    for (const u of urls) {
      const r = await fetch(u, { credentials: 'same-origin' });
      if (r.ok) { await c.put(u, r); n++; }
    }
    if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
    writeList([...list, { id: p.id, title: p.title, citekey: p.citekey, urls, at: new Date().toISOString() }]);
    toast(`已離線保存（${n} 個檔案），沒網路時也能打開這篇`);
  } catch (e) { fail(e); }
  void fileId;
}

// ---------------------------------------------------------------- 登出時清掉這個帳號的快取
export async function clearUserCaches() {
  writeList([]);
  try {
    if (navigator.serviceWorker?.controller) navigator.serviceWorker.controller.postMessage('clear-user-data');
    await Promise.all(['pl-api', 'pl-pdf', 'pl-offline'].map((k) => caches.delete(k)));
  } catch { /* */ }
}

// ---------------------------------------------------------------- 帳號選單的項目
export function pwaMenuItems() {
  const items = [];
  if (deferred) items.push({ label: '安裝成 App', icon: 'phone', run: async () => { deferred.prompt(); const r = await deferred.userChoice; if (r.outcome === 'accepted') deferred = null; } });
  else if (!standalone()) items.push({ label: isIOS() ? '加到主畫面（iPhone／iPad）' : '安裝成 App…', icon: 'phone', run: installHelp });
  items.push({ label: '離線閱讀與儲存空間', icon: 'offline', run: offlineManager });
  return items;
}

function installHelp() {
  const secure = window.isSecureContext;
  const steps = isIOS() ? [
    '用 Safari 打開這個網站（iOS 16.4 以後 Chrome、Edge 也可以）',
    '點下方（iPad 在右上角）的「分享」按鈕 ⬆︎',
    '往下滑，點「加入主畫面」',
    '按「新增」，主畫面就會出現 QEL 論文庫的圖示',
  ] : /android/i.test(navigator.userAgent) ? [
    '用 Chrome 打開這個網站',
    '點右上角 ⋮ 選單 →「安裝應用程式」或「加到主畫面」',
    '按「安裝」，之後從主畫面或應用程式清單開啟',
  ] : [
    'Chrome／Edge：網址列右邊會出現「安裝」圖示（電腦加一個方塊的圖案），點它',
    '或是選單 →「投放、儲存及分享」→「將網頁安裝為應用程式」',
    'macOS Safari（Sonoma 以後）：選單「檔案」→「加入 Dock」',
  ];
  modal('安裝成 App', h('div', { class: 'stack' },
    !secure ? h('div', { class: 'notice warn' }, icon('lock'), h('div', { class: 'small' },
      h('b', {}, '目前用的是 http 網址，手機與瀏覽器只允許 HTTPS 網站安裝成 App、離線閱讀。'),
      h('div', {}, '請改用 HTTPS 網址開啟（例如 Tailscale 的 https://<NAS 名稱>.<tailnet>.ts.net、Cloudflare Tunnel 或 Tailscale Funnel 的網址），做法見 README「從外網連線」。'))) : null,
    h('ol', { class: 'install-steps' }, steps.map((s) => h('li', {}, s))),
    h('p', { class: 'hint' }, '安裝後會用全螢幕開啟（沒有網址列），讀過的論文與看過的頁面會留在手機裡，沒網路時也能讀。登入狀態與網頁版共用。')));
}

async function offlineManager() {
  const list = readList();
  const est = navigator.storage?.estimate ? await navigator.storage.estimate().catch(() => null) : null;
  const mb = (n) => `${Math.round((n || 0) / 1e6)} MB`;
  const auto = h('input', { type: 'checkbox', checked: autoOn(), onchange: (e) => { try { localStorage.setItem(AUTO, e.target.checked ? '1' : '0'); } catch { /* */ } } });
  const body = h('div', { class: 'stack' },
    !hasSW() ? h('div', { class: 'notice warn' }, icon('lock'), h('div', { class: 'small' }, '離線閱讀需要用 HTTPS 網址開啟網站（http 的 IP 位址不行）。')) : null,
    h('label', { class: 'check' }, auto, '自動保存最近讀過的 20 篇 PDF（背景下載一次，之後沒網路也能讀）'),
    est ? h('p', { class: 'muted small' }, `這個網站在這台裝置用了 ${mb(est.usage)}（上限約 ${mb(est.quota)}）。`) : null,
    h('h3', {}, `離線保存的論文（${list.length}）`),
    list.length ? h('ul', { class: 'plain' }, list.map((x) => h('li', { class: 'row' }, h('a', { class: 'grow', href: `#/p/${x.id}` }, x.title),
      h('span', { class: 'muted small' }, fmtDate(x.at)),
      h('button', { class: 'linkbtn', onclick: async () => { await offlineSave({ id: x.id }, null); md.close(); offlineManager(); } }, '移除'))))
      : h('p', { class: 'muted small' }, '還沒有。在論文頁右上角 ⋯ 選「離線保存」，出差、飛機上也能讀。'),
    h('div', { class: 'row' }, h('span', { class: 'grow' }), h('button', { class: 'btn ghost', onclick: async () => {
      if (!await confirmBox('清除這台裝置保存的所有論文與頁面快取？（不影響伺服器上的資料）', '清除')) return;
      await clearUserCaches(); md.close(); toast('已清除');
    } }, '清除快取')));
  const md = modal('離線閱讀與儲存空間', body);
}
