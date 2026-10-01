// 手寫即時儲存：每畫一筆就先寫進這台裝置（localStorage 的待送佇列），再送到伺服器。
// - 沒網路、伺服器忙、關掉分頁：佇列留在裝置上，下次打開網站或連上網路時自動補送，不會遺失。
// - 每一則手寫有瀏覽器產生的編號（uid），每一筆有自己的編號（s），重送也不會重複。
// - 重新打開論文時，還沒送出的筆畫會先合併進畫面。
import { S, toast, annGlobal } from './app.js';

const KEY = 'pl-ink-outbox';
let box = load();
let busy = false, timer = null, delay = 0, lastErr = '';
const listeners = new Set();
const live = new Map();          // uid -> 畫面上的標註物件（送出成功後補上伺服器編號）

function load() { try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v : []; } catch { return []; } }
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(box)); return true; } catch { return false; }
}
const rid = (n = 10) => {
  const a = new Uint8Array(n); (crypto.getRandomValues ? crypto.getRandomValues(a) : a.forEach((_, i) => { a[i] = Math.random() * 256; }));
  return [...a].map((b) => 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'[b % 62]).join('');
};
export const newUid = () => `k${Date.now().toString(36)}${rid(8)}`;
export const newSid = () => rid(10);
export const uidOf = (a) => a.client_uid || `a${a.id}`;

// 目前狀態：{ pending: 還沒送出的操作數, state: 'ok'|'saving'|'retry'|'error', error }
export function inkStatus() {
  const mine = box.filter((o) => o.user === S.user?.id).length;
  return { pending: mine, state: busy ? 'saving' : mine ? (lastErr ? 'retry' : 'saving') : 'ok', error: lastErr };
}
export function onInkStatus(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const notify = () => { const s = inkStatus(); listeners.forEach((fn) => { try { fn(s); } catch { /* */ } }); };

// 加入一個操作（append／set／delete），馬上寫進裝置，接著送出
export function pushInk(op) {
  box = load();                 // 其他分頁／視窗可能也寫過，先讀最新的
  box.push({ ...op, user: S.user?.id, t: Date.now(), k: rid(8) });
  if (!save()) toast('裝置儲存空間不足，手寫可能無法暫存；請保持連線');
  notify();
  schedule(0);
}

function schedule(ms) {
  clearTimeout(timer);
  timer = setTimeout(flush, ms);
}

export async function flush() {
  if (busy || !S.user) return;
  box = load();
  const mine = box.filter((o) => o.user === S.user.id);
  if (!mine.length) { lastErr = ''; notify(); return; }
  busy = true; notify();
  try {
    const batch = mine.slice(0, 60);
    let res;
    try {
      res = await fetch('/api/ink/sync', { method: 'POST', credentials: 'same-origin', headers: { 'X-PL': '1', 'Content-Type': 'application/json' },
        body: JSON.stringify({ ops: batch.map(({ user, t, k, ...o }) => o) }) });
    } catch { return retry('沒有網路，連上後會自動補送'); }
    if (res.status === 401) return retry('登入已過期，重新登入後會自動補送');
    if (res.status >= 500 || res.status === 429 || res.status === 408) return retry(`伺服器忙碌（${res.status}），稍後自動重試`);
    let data = null;
    try { data = await res.json(); } catch { /* */ }
    if (!res.ok) {
      // 整批被拒（例如格式錯）：只丟掉第一個，避免卡住後面的
      toast(`有一筆手寫無法儲存：${data?.detail || res.status}`);
      drop([batch[0]]);
      delay = 0; lastErr = '';
      return schedule(50);
    }
    for (const r of data.results || []) {
      const a = live.get(r.uid);
      if (a && r.id && r.ok && !r.deleted) { a.id = r.id; a.client_uid = r.uid; }
      if (!r.ok) toast(`有一筆手寫無法儲存：${r.error || '未知錯誤'}`);
    }
    drop(batch);
    delay = 0; lastErr = '';
    window.dispatchEvent(new CustomEvent('pl-ink-synced', { detail: data.results || [] }));
    if (box.some((o) => o.user === S.user.id)) schedule(0);
  } finally { busy = false; notify(); }
}
function drop(sent) { const ks = new Set(sent.map((o) => o.k)); box = load().filter((o) => !ks.has(o.k)); save(); }
function retry(msg) {
  lastErr = msg;
  delay = Math.min(60000, delay ? delay * 2 : 2000);
  schedule(delay);
}

// 登出後換人登入：只送自己的；別人的留到他下次登入
window.addEventListener('online', () => { delay = 0; schedule(0); });
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(); else schedule(0); });
window.addEventListener('pagehide', () => {
  // 關分頁前最後一次嘗試（小量才用 keepalive；送不出去也沒關係，佇列還在裝置上）
  const mine = box.filter((o) => o.user === S.user?.id);
  if (!mine.length || busy) return;
  const body = JSON.stringify({ ops: mine.slice(0, 60).map(({ user, t, k, ...o }) => o) });
  if (body.length > 60000) return;
  try { fetch('/api/ink/sync', { method: 'POST', keepalive: true, credentials: 'same-origin', headers: { 'X-PL': '1', 'Content-Type': 'application/json' }, body }); } catch { /* */ }
});
window.addEventListener('storage', (e) => { if (e.key === KEY) { box = load(); notify(); } });

// 打開論文時：把還沒送出的操作套用到從伺服器拿到的標註清單上
export function applyPending(anns, pid) {
  box = load();
  const ops = box.filter((o) => o.user === S.user?.id && o.pid === pid);
  for (const o of ops) {
    let a = anns.find((x) => uidOf(x) === o.uid);
    if (o.op === 'delete') { if (a) anns.splice(anns.indexOf(a), 1); continue; }
    if (!a) {
      a = blankInk({ uid: o.uid, pid, file_id: o.file_id ?? null, page: o.page, nb: o.nb ? 1 : 0, private: o.private });
      anns.push(a);
    }
    if (!a.ink) a.ink = { strokes: [] };
    if (o.op === 'set') a.ink.strokes = [...o.strokes];
    else { const seen = new Set(a.ink.strokes.map((s) => s.s).filter(Boolean)); a.ink.strokes.push(...o.strokes.filter((s) => !s.s || !seen.has(s.s))); }
    if (!a.ink.strokes.length) anns.splice(anns.indexOf(a), 1);
  }
  anns.forEach((a) => { if (a.client_uid) live.set(a.client_uid, a); });
  return anns;
}

export function blankInk({ uid, pid, file_id = null, page, nb = 0, private: priv }) {
  const a = { id: `tmp-${uid}`, client_uid: uid, paper_id: pid, file_id, page, nb, kind: 'ink', color: 'ink', quote: '', body: '', rects: [], replies: [],
    private: priv === undefined ? (annGlobal() ? 0 : 1) : (priv ? 1 : 0), author_id: S.user?.id, who: S.user?.display_name || '',
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ink: { strokes: [] } };
  live.set(uid, a);
  return a;
}
export const isTemp = (a) => typeof a.id === 'string' && a.id.startsWith('tmp-');

// 一個手寫編輯工作階段：記住「這次在第幾頁寫的是哪一則」與復原紀錄。PDF 上的手寫與筆記頁共用。
export class InkEditor {
  // host：{ pid, nb, anns() → 目前的標註陣列, fileId() → 目前的檔案, refresh() → 重畫 }
  constructor(host) { this.h = host; this.session = {}; this.undo = []; }
  reset() { this.session = {}; this.undo = []; }
  stroke(page, stroke) {
    const { pid, nb } = this.h;
    const anns = this.h.anns();
    if (!stroke.s) stroke.s = newSid();
    let a = anns.find((x) => uidOf(x) === this.session[page]);
    if (a && (a.ink?.strokes?.length || 0) >= 1500) a = null;   // 一則太大就另開一則
    if (!a) {
      a = blankInk({ uid: newUid(), pid, file_id: nb ? null : this.h.fileId(), page, nb: nb ? 1 : 0 });
      anns.push(a);
      this.session[page] = a.client_uid;
      this.undo.push({ uid: a.client_uid, strokes: [], created: true });
    } else this.undo.push({ uid: uidOf(a), strokes: [...a.ink.strokes] });
    a.ink.strokes.push(stroke);
    a.updated_at = new Date().toISOString();
    pushInk({ op: 'append', uid: uidOf(a), pid, file_id: a.file_id, page, nb: a.nb ? 1 : 0, private: !!a.private, strokes: [stroke] });
    this.h.refresh();
  }
  erase(changed) {
    const anns = this.h.anns();
    for (const a of changed) {
      if (!a.ink.strokes.length) { const i = anns.indexOf(a); if (i >= 0) anns.splice(i, 1); pushInk({ op: 'delete', uid: uidOf(a), pid: this.h.pid }); }
      else pushInk({ op: 'set', uid: uidOf(a), pid: this.h.pid, file_id: a.file_id, page: a.page, nb: a.nb ? 1 : 0, private: !!a.private, strokes: a.ink.strokes });
    }
    this.h.refresh();
  }
  undoLast() {
    const u = this.undo.pop();
    if (!u) return;
    const anns = this.h.anns();
    const a = anns.find((x) => uidOf(x) === u.uid);
    if (!a) return this.h.refresh();
    if (!u.strokes.length) {
      anns.splice(anns.indexOf(a), 1);
      Object.keys(this.session).forEach((k) => { if (this.session[k] === u.uid) delete this.session[k]; });
      pushInk({ op: 'delete', uid: u.uid, pid: this.h.pid });
    } else {
      a.ink.strokes = u.strokes;
      pushInk({ op: 'set', uid: u.uid, pid: this.h.pid, file_id: a.file_id, page: a.page, nb: a.nb ? 1 : 0, private: !!a.private, strokes: u.strokes });
    }
    this.h.refresh();
  }
}

// 手寫工具列上的儲存狀態
export function inkStatusBadge(h) {
  const el = h('span', { class: 'ink-status', 'aria-live': 'polite' });
  const draw = (s) => {
    el.className = `ink-status ${s.state}`;
    el.textContent = s.state === 'ok' ? '✓ 已儲存' : s.state === 'saving' ? '儲存中…' : `⚠ ${s.pending} 筆待上傳`;
    el.title = s.state === 'retry' ? `${s.error}（已存在這台裝置，不會遺失）` : s.state === 'ok' ? '所有手寫都已存到伺服器' : '正在上傳';
  };
  draw(inkStatus());
  const off = onInkStatus((s) => { if (!el.isConnected) return off(); draw(s); });
  return el;
}

// 網站開啟時補送上次沒送完的
setTimeout(() => schedule(0), 1500);
