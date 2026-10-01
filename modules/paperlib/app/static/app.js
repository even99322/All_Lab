// LAB-QEL論文庫 — 單頁前端（不需編譯）。路由用網址 # 片段，手機、平板、電腦共用同一套畫面。
import { Viewer, COLORS, inkPreview } from './viewer.js';
import { I } from './icons.js';
import { viewGraph } from './graph.js';
import { viewDesk, deskButton, installLinkInterceptor, openInDesk, openDeskWindow, deskState, go, isDeskWindow, wantsSeparateWindow, setSeparateWindow, toggleNotebook, notebookOpenFor } from './desk.js';
import { InkEditor, applyPending, inkStatusBadge, isTemp, pushInk, uidOf } from './ink.js';
import { notifBell, myAccount, viewFeeds, viewMeetings, viewAsk, viewReports, scheduleDialog, aiKeyinfoDialog, askDialog, repliesBlock, mentionAssist, watchJob, adminSite, adminNotify, adminZotero, adminMaint } from './more.js';
import { viewParams, paramsBlock, viewFigures, figuresPanel, saveCrop, similarBlock, versionNotes, viewVersions, viewPaths, viewPath, slidesDialog, viewDashboard, viewJournalSearch } from './research.js';
import { viewHelp } from './help.js';
import { viewNotes } from './notes.js';
import { pwaMenuItems, offlineSave, isOfflineSaved, clearUserCaches, netError, rememberPdf } from './pwa.js';

// ================================================================== 小工具
// replaceChildren／append 自動略過 null、false（條件式顯示的元件用 cond ? el : null），陣列會攤平
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  for (const fn of ['replaceChildren', 'append', 'prepend']) {
    const orig = proto[fn];
    proto[fn] = function (...kids) { return orig.apply(this, kids.flat(Infinity).filter((x) => x != null && x !== false)); };
  }
}
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'style' && typeof v === 'object') for (const [sk, sv] of Object.entries(v)) { if (sk.startsWith('--')) el.style.setProperty(sk, sv); else el.style[sk] = sv; }
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const icon = (name, cls = '') => h('span', { class: `ic ${cls}`, html: I[name] || '' });

// ================================================================== 淺色／深色
const THEMES = { auto: ['auto', '跟隨系統'], light: ['sun', '淺色'], dark: ['moon', '深色'] };
const getTheme = () => { try { return localStorage.getItem('pl-theme') || 'auto'; } catch { return 'auto'; } };
const isDark = () => getTheme() === 'dark' || (getTheme() === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
function applyTheme() {
  const t = getTheme();
  if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  let pdf = false; try { pdf = localStorage.getItem('pl-pdfdark') === '1'; } catch { /* */ }
  if (pdf) document.documentElement.dataset.pdf = 'dark'; else delete document.documentElement.dataset.pdf;
  document.querySelectorAll('.theme-btn').forEach((b) => { b.replaceChildren(icon(THEMES[t][0])); b.title = `外觀：${THEMES[t][1]}（點一下切換）`; });
  window.dispatchEvent(new Event('pl-theme'));
}
function setTheme(t) { try { localStorage.setItem('pl-theme', t); } catch { /* */ } applyTheme(); }
function themeButton() {
  const b = h('button', { class: 'icon-btn theme-btn', 'aria-label': '切換淺色／深色', onclick: (e) => menu(e.currentTarget, [
    ...Object.entries(THEMES).map(([k, [ic, label]]) => ({ label: `${getTheme() === k ? '✓ ' : ''}${label}`, icon: ic, run: () => setTheme(k) })),
    '-',
    { label: `${document.documentElement.dataset.pdf === 'dark' ? '✓ ' : ''}PDF 也用深色（夜間閱讀）`, icon: 'moon', run: () => {
      const on = document.documentElement.dataset.pdf !== 'dark'; try { localStorage.setItem('pl-pdfdark', on ? '1' : '0'); } catch { /* */ } applyTheme();
    } },
  ]) });
  b.append(icon(THEMES[getTheme()][0])); b.title = `外觀：${THEMES[getTheme()][1]}`;
  return b;
}
window.addEventListener('storage', (e) => { if (e.key === 'pl-theme' || e.key === 'pl-pdfdark') applyTheme(); });
matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => applyTheme());
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const fmtDate = (s) => { if (!s) return ''; const d = new Date(s); return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`; };
const fmtSize = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round((n || 0) / 1e3)} KB`);
const ROLE = { main: '正文', sm: '補充資料', peer_review: '審稿意見', version: '其他版本', other: '其他' };
const KIND = { article: '期刊論文', review: '回顧', preprint: '預印本', book: '書籍', thesis: '學位論文', web: '網頁／講義', other: '其他' };
const KEYINFO_DEFAULT = ['重點', '架設／平台', '關鍵參數', '可萃取特徵／觀測量', '與我們實驗的關係', '備註'];

function authorLine(p) {
  const a = p.authors || [];
  if (!a.length) return '作者未填';
  const more = (p.n_authors || a.length) > 1 || !p.authors_complete;
  return a[0] + (more ? ' 等' : '');
}
function snippetHtml(s) { return esc(s).replace(/⟦/g, '<mark>').replace(/⟧/g, '</mark>'); }

// ================================================================== API
async function req(method, url, body, isForm) {
  const opt = { method, headers: { 'X-PL': '1' }, credentials: 'same-origin' };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers['Content-Type'] = 'application/json'; }
  }
  let r;
  try { r = await fetch(url, opt); } catch (e) { throw netError(method, e); }
  if (r.status === 401) { S.user = null; renderAuth(); throw new Error('請先登入'); }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (r.headers.get('X-PL-Stale') && data && typeof data === 'object') Object.defineProperty(data, '_stale', { value: true });
  if (!r.ok) throw new Error((data && data.detail) || `錯誤 ${r.status}`);
  return data;
}
const api = {
  get: (u) => req('GET', u), post: (u, b) => req('POST', u, b ?? {}), patch: (u, b) => req('PATCH', u, b), put: (u, b) => req('PUT', u, b),
  del: (u) => req('DELETE', u), form: (u, fd) => req('POST', u, fd, true),
};

// ================================================================== 全域狀態
const S = { site: null, user: null, cats: [], catById: {}, tags: [], tagByName: {}, rels: [], overview: null };
async function loadMeta() {
  const [cats, tags, rels] = await Promise.all([api.get('/api/categories'), api.get('/api/tags'), api.get('/api/rels')]);
  S.cats = cats; S.catById = Object.fromEntries(cats.map((c) => [c.id, c])); S.tags = tags; S.rels = rels;
  S.tagByName = Object.fromEntries(tags.map((t) => [t.name, t]));
}
// 權限：paper 有給時套用「只能修改自己上傳的論文」
const can = (k, p) => !!S.user?.perms?.[k] && !(p && S.user.perms.own_only && p.added_by !== S.user.id);
const isAdmin = () => S.user?.role === 'admin';
const roleName = (r) => S.site?.role_names?.[r] || r;
// 標籤外觀跟分類不同：# 開頭、方角、左側色條
function tagChip(name, { href = true, onRemove } = {}) {
  const color = S.tagByName[name]?.color || '#6b7280';
  const kids = [h('span', { class: 'hash' }, '#'), name, onRemove ? h('button', { 'aria-label': `移除 ${name}`, onclick: onRemove }, '×') : null];
  return href && !onRemove ? h('a', { class: 'tag', href: `#/t/${encodeURIComponent(name)}`, style: { '--t': color } }, kids)
    : h('span', { class: 'tag', style: { '--t': color } }, kids);
}
const groups = () => [...new Set(S.cats.map((c) => c.grp))];

// ================================================================== 提示、對話框、選單
function toast(msg, kind = '') {
  const t = h('div', { class: `toast ${kind}` }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.classList.add('out'), 2600);
  setTimeout(() => t.remove(), 3100);
}
const fail = (e) => toast(e.message || String(e), 'err');

function modal(title, body, { wide = false, onClose } = {}) {
  const close = () => { wrap.remove(); document.removeEventListener('keydown', key); onClose && onClose(); };
  const key = (e) => { if (e.key === 'Escape') close(); };
  const wrap = h('div', { class: 'modal-wrap', onclick: (e) => { if (e.target === wrap) close(); } },
    h('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-label': title },
      h('div', { class: 'modal-head' }, h('h2', {}, title), h('button', { class: 'icon-btn', 'aria-label': '關閉', onclick: close }, icon('x'))),
      h('div', { class: 'modal-body' }, body)));
  document.body.append(wrap);
  document.addEventListener('keydown', key);
  return { close, el: wrap };
}

function menu(anchor, items) {
  document.querySelectorAll('.menu').forEach((m) => m.remove());
  const m = h('div', { class: 'menu', role: 'menu' }, items.filter(Boolean).map((it) => it === '-' ? h('hr') :
    h('button', { class: `menu-item ${it.danger ? 'danger' : ''}`, onclick: () => { m.remove(); it.run(); } },
      it.icon ? icon(it.icon) : null, it.label)));
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  const mw = 220;
  m.style.top = `${Math.min(r.bottom + 4, window.innerHeight - m.offsetHeight - 8)}px`;
  m.style.left = `${Math.max(8, Math.min(r.right - mw, window.innerWidth - mw - 8))}px`;
  const esc = (e) => { if (e.key === 'Escape') { m.remove(); document.removeEventListener('keydown', esc); } };
  document.addEventListener('keydown', esc);
  setTimeout(() => document.addEventListener('click', function off(e) {
    if (!m.contains(e.target)) { m.remove(); document.removeEventListener('click', off); }
  }), 0);
}

function confirmBox(text, okLabel = '確定') {
  return new Promise((res) => {
    const m = modal('確認', h('div', {}, h('p', {}, text), h('div', { class: 'row end' },
      h('button', { class: 'btn', onclick: () => { m.close(); res(false); } }, '取消'),
      h('button', { class: 'btn danger', onclick: () => { m.close(); res(true); } }, okLabel))));
  });
}

// ================================================================== 登入／初次設定
async function renderAuth(mode = 'login') {
  document.body.classList.remove('has-shell');
  const setup = S.site?.needs_setup;
  if (setup) mode = 'login';
  const msg = h('p', { class: 'form-err' });
  if (mode === 'register') {
    const f = h('form', { class: 'auth-card', onsubmit: async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(f));
      if (d.password !== d.password2) { msg.textContent = '兩次輸入的密碼不一樣'; return; }
      delete d.password2;
      const btn = f.querySelector('button[type=submit]'); btn.disabled = true;
      try {
        await api.post('/api/auth/register', d);
        $('#app').replaceChildren(h('div', { class: 'auth' }, h('div', { class: 'auth-card' },
          h('div', { class: 'auth-logo' }, icon('check', 'big')),
          h('h1', {}, '申請已送出'),
          h('p', {}, `站長審核通過後，就可以用帳號「${d.username}」登入。`),
          h('p', { class: 'muted' }, '審核結果會寄到你填的 Email（如果網站有設定寄信）。'),
          h('button', { class: 'btn primary block', onclick: () => renderAuth('login') }, '回到登入'))));
      } catch (err) { msg.textContent = err.message; btn.disabled = false; }
    } },
    h('div', { class: 'auth-logo' }, icon('logo', 'big')),
    h('h1', {}, '申請帳號'),
    h('p', { class: 'muted' }, `填好後送給 ${S.site?.name || '論文庫'} 的站長審核，通過後就能登入。`),
    h('label', {}, '用戶名稱', h('input', { name: 'display_name', required: true, maxlength: 40, placeholder: '別人看到的名字，例如：王小明' })),
    h('label', {}, '帳號', h('input', { name: 'username', required: true, autocomplete: 'username', autocapitalize: 'none', pattern: '[A-Za-z0-9._\\-]{3,32}', title: '3–32 個英文字母、數字或 . _ -', placeholder: '登入用，英文或數字' })),
    h('label', {}, '密碼', h('input', { name: 'password', type: 'password', required: true, minlength: 8, autocomplete: 'new-password', placeholder: '至少 8 個字元' })),
    h('label', {}, '再輸入一次密碼', h('input', { name: 'password2', type: 'password', required: true, minlength: 8, autocomplete: 'new-password' })),
    h('label', {}, 'Email（收通知與審核結果）', h('input', { name: 'email', type: 'email', required: true, autocomplete: 'email', placeholder: 'name@example.com' })),
    h('label', {}, '申請說明（選填）', h('textarea', { name: 'note', rows: 2, maxlength: 500, placeholder: '例如：我是 OO 老師實驗室的碩一學生' })),
    h('input', { name: 'website', class: 'hp', tabindex: '-1', autocomplete: 'off', 'aria-hidden': 'true' }),
    msg,
    h('button', { class: 'btn primary block', type: 'submit' }, '送出申請'),
    h('button', { class: 'linkbtn auth-switch', type: 'button', onclick: () => renderAuth('login') }, '已經有帳號？回到登入'));
    $('#app').replaceChildren(h('div', { class: 'auth' }, f));
    return;
  }
  const codeIn = h('input', { name: 'code', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6, placeholder: '123456' });
  const codeBox = h('label', { hidden: true }, '兩步驟驗證碼', codeIn);
  const f = h('form', { class: 'auth-card', onsubmit: async (e) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(f));
    try {
      const r = await api.post(setup ? '/api/auth/setup' : '/api/auth/login', d);
      if (r && r.need_2fa) {   // 開了兩步驟驗證：要求輸入 App 上的 6 位數
        codeBox.hidden = false; codeIn.required = true; codeIn.focus();
        msg.textContent = '請輸入驗證 App 上的 6 位數驗證碼';
        return;
      }
      try { localStorage.removeItem('pl-email-later'); } catch { /* */ }
      if (!location.hash || location.hash === '#') location.hash = '#/';
      boot();
    } catch (err) { msg.textContent = err.message; }
  } },
  h('div', { class: 'auth-logo' }, icon('logo', 'big')),
  h('h1', {}, S.site?.name || '論文庫'),
  h('p', { class: 'muted' }, setup ? '第一次使用：建立管理員帳號' : '請登入'),
  h('label', {}, '帳號', h('input', { name: 'username', autocomplete: 'username', required: true, autocapitalize: 'none' })),
  setup ? h('label', {}, '顯示名稱', h('input', { name: 'display_name', placeholder: '例如：張譯文' })) : null,
  h('label', {}, '密碼', h('input', { name: 'password', type: 'password', autocomplete: setup ? 'new-password' : 'current-password', required: true, minlength: setup ? 6 : null })),
  codeBox,
  msg,
  h('button', { class: 'btn primary block', type: 'submit' }, setup ? '建立並進入' : '登入'),
  !setup && S.site?.register ? h('button', { class: 'linkbtn auth-switch', type: 'button', onclick: () => renderAuth('register') }, '還沒有帳號？申請註冊') : null);
  $('#app').replaceChildren(h('div', { class: 'auth' }, f));
}

// 登入後：還沒填 Email 的帳號提醒填寫（每次登入提醒一次，可以先跳過）
// 按「下次再說」後 12 小時內不再跳出；重新登入會再提醒
const EMAIL_SNOOZE = 12 * 3600 * 1000;
const emailSnoozed = () => { try { return Date.now() - (+localStorage.getItem('pl-email-later') || 0) < EMAIL_SNOOZE; } catch { return false; } };
const snoozeEmail = () => { try { localStorage.setItem('pl-email-later', String(Date.now())); } catch { /* */ } };
function emailReminder(force = false) {
  if (!S.user || S.user.has_email || isDeskWindow() || document.querySelector('.modal-email')) return;
  if (!force && emailSnoozed()) return;
  const inp = h('input', { type: 'email', required: true, placeholder: 'name@example.com', autocomplete: 'email' });
  const err = h('p', { class: 'form-err' });
  let saved = false;
  const m = modal('請填寫你的 Email', h('form', { class: 'stack modal-email', onsubmit: async (e) => {
    e.preventDefault();
    try { await api.patch('/api/me', { email: inp.value.trim() }); S.user.has_email = true; saved = true; m.close(); toast('已儲存 Email'); document.querySelectorAll('.email-banner').forEach((x) => x.remove()); } catch (x) { err.textContent = x.message; }
  } },
    h('p', {}, '你的帳號還沒有 Email。填了之後才能收到：'),
    h('ul', { class: 'small' }, h('li', {}, '有人 @你、指派論文、排你報告的通知信'), h('li', {}, '每週摘要（可以在「我的帳號」關掉）'), h('li', {}, '忘記密碼或帳號異動時的聯絡')),
    inp, err,
    h('div', { class: 'row end' }, h('button', { class: 'btn ghost', type: 'button', onclick: () => m.close() }, '下次再說'), h('button', { class: 'btn primary', type: 'submit' }, '儲存'))),
  { onClose: () => { if (!saved) snoozeEmail(); } });
  setTimeout(() => inp.focus(), 50);
}
// 已經登入、分頁一直開著的人：定期確認，還沒填就提醒（管理員按了「提醒沒填 Email 的人」也會在這裡跳出）
let emailTimer = null;
function watchEmail() {
  clearInterval(emailTimer);
  const check = async () => {
    if (!S.user || S.user.has_email) return;
    try { const me = await api.get('/api/me'); S.user.has_email = !!me.email; } catch { return; }
    emailReminder();
  };
  emailTimer = setInterval(check, 10 * 60 * 1000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') check(); });
}

// ================================================================== 外框：頂列、側欄
function shell() {
  document.body.classList.add('has-shell');
  const search = h('input', { type: 'search', placeholder: '搜尋標題、作者、全文、筆記…', 'aria-label': '搜尋', enterkeyhint: 'search' });
  const form = h('form', { class: 'search', onsubmit: (e) => { e.preventDefault(); const q = search.value.trim(); if (q) location.hash = `#/${searchMode() === 'sem' ? 'sem' : 's'}/${encodeURIComponent(q)}`; } },
    icon('search'), search);
  const top = h('header', { class: 'topbar' },
    h('button', { class: 'icon-btn only-mobile', 'aria-label': '選單', onclick: () => document.body.classList.toggle('nav-open') }, icon('menu')),
    h('a', { class: 'brand', href: '#/' }, icon('logo'), h('span', { class: 'brand-name' }, S.site.name)),
    form,
    can('upload') ? h('button', { class: 'btn primary', onclick: () => openUpload() }, icon('upload'), h('span', { class: 'hide-mobile' }, '上傳')) : null,
    deskButton(),
    themeButton(),
    notifBell(),
    h('button', { class: 'avatar', 'aria-label': '帳號', onclick: (e) => menu(e.currentTarget, [
      { label: `${S.user.display_name}（${S.user.owner ? '站長' : roleName(S.user.role)}）· 我的帳號`, icon: 'user', run: myAccount },
      !S.user.has_email ? { label: '填寫 Email（收通知信）', icon: 'bell', run: () => emailReminder(true) } : null,
      { label: `程式版本 ${S.site.version || '（舊版）'}`, icon: 'info', run: () => toast(`伺服器程式版本：${S.site.version || '舊版'}`) },
      { label: '使用說明', icon: 'help', run: () => { location.hash = '#/help'; } },
      { label: '變更密碼', icon: 'key', run: changePassword },
      matchMedia('(pointer: fine)').matches ? { label: `${wantsSeparateWindow() ? '✓ ' : ''}論文開在獨立的閱讀桌視窗`, icon: 'stack', run: () => { setSeparateWindow(!wantsSeparateWindow()); toast(wantsSeparateWindow() ? '點論文會在獨立視窗開啟，可多篇並排' : '點論文改為在這個視窗開啟'); } } : null,
      isAdmin() || can('manage') ? { label: '管理', icon: 'gear', run: () => { location.hash = isAdmin() ? '#/admin' : '#/admin/cats'; } } : null,
      ...pwaMenuItems(),
      '-', { label: '登出', icon: 'logout', run: async () => { await api.post('/api/auth/logout'); await clearUserCaches(); S.user = null; renderAuth(); } },
    ]) }, (S.user.display_name || '?').slice(0, 1)));
  const nav = h('nav', { class: 'sidebar', id: 'nav' });
  const main = h('main', { id: 'view', tabindex: '-1' });
  $('#app').replaceChildren(top, h('div', { class: 'layout' }, nav, main), h('div', { class: 'scrim', onclick: () => document.body.classList.remove('nav-open') }));
  S.searchInput = search;
}

async function refreshNav() {
  const ov = await api.get('/api/overview');
  S.overview = ov;
  await loadMeta();
  drawNav();
}
function drawNav() {
  const ov = S.overview;
  if (!ov) return;
  const cur = location.hash;
  const link = (href, label, count, extra = {}) => h('a', { class: `nav-item ${cur === href ? 'active' : ''} ${extra.cls || ''}`, href },
    extra.icon ? icon(extra.icon) : extra.dot ? h('span', { class: 'dot', style: { background: extra.dot } }) : null,
    h('span', { class: 'nav-label' }, label), count != null ? h('span', { class: 'count' }, count) : null);
  const nav = $('#nav');
  if (!nav) return;
  nav.replaceChildren(
    link('#/', '論文群總覽', null, { icon: 'grid' }),
    link('#/all', '全部論文', ov.total, { icon: 'stack' }),
    link('#/inbox', '未歸檔待讀', ov.inbox.count, { icon: 'inbox', cls: ov.inbox.count ? 'attn' : '' }),
    link('#/todo', '我的待讀', ov.n_todo || null, { icon: 'bookmark' }),
    link('#/reading', '閱讀中', ov.n_reading || null, { icon: 'book' }),
    link('#/notes', '我的筆記', ov.n_my_notes || null, { icon: 'pen' }),
    link('#/assigned', '指派給我', (ov.assigned || []).filter((a) => a.status !== '已閱讀').length || null, { icon: 'at', cls: (ov.assigned || []).some((a) => a.status !== '已閱讀') ? 'attn' : '' }),
    link('#/required', '全站必讀', ov.required?.total ? `${ov.required.read}/${ov.required.total}` : null, { icon: 'flag' }),
    h('a', { class: 'nav-item', href: '#/desk', onclick: (e) => { e.preventDefault(); openDeskWindow(); } }, icon('stack'), h('span', { class: 'nav-label' }, '閱讀桌（多篇並排）'), h('span', { class: 'count' }, deskState().open.length || '')),
    link('#/feeds', '新論文追蹤', ov.feed_new || null, { icon: 'rss', cls: ov.feed_new ? 'attn' : '' }),
    link('#/jsearch', '期刊搜尋', null, { icon: 'search', cls: 'sub-item' }),
    link('#/meetings', '組會／閱讀清單', ov.n_meetings || null, { icon: 'calendar' }),
    link('#/paths', '入門路徑', (ov.my_paths || []).length || null, { icon: 'route', cls: (ov.my_paths || []).length ? 'attn' : '' }),
    can('ai') ? link('#/ask', '問論文庫（AI）', null, { icon: 'spark' }) : null,
    can('ai') ? link('#/reports', 'AI 綜述', null, { icon: 'star' }) : null,
    h('div', { class: 'nav-sec' }, icon('sliders'), '研究工具'),
    link('#/params', '參數比較與對照表', null, { icon: 'table' }),
    link('#/figures', '圖表剪貼簿', ov.n_figures || null, { icon: 'image' }),
    link('#/dashboard', '實驗室動態', null, { icon: 'chart' }),
    ov.n_versions || isAdmin() || can('manage') ? link('#/versions', '預印本與重複論文', ov.n_versions || null, { icon: 'merge', cls: ov.n_versions ? 'attn' : '' }) : null,
    h('div', { class: 'nav-sec' }, icon('folder'), '分類'),
    ...groups().map((g) => h('div', { class: 'nav-group' }, h('div', { class: 'nav-head' }, g),
      S.cats.filter((c) => c.grp === g).map((c) => [link(`#/c/${c.id}`, c.name, c.count, { dot: c.color }),
        cur.startsWith(`#/c/${c.id}`) && (cur === `#/c/${c.id}` || cur.startsWith(`#/c/${c.id}?`)) ? (c.folders || []).map((f) =>
          link(`#/c/${c.id}?f=${f.id}`, f.name, f.count, { icon: 'folder', cls: 'sub-item' })) : null]))),
    h('div', { class: 'nav-sec' }, icon('tag'), '標籤', h('a', { class: 'nav-more', href: '#/tags' }, '全部')),
    h('div', { class: 'nav-group tags' }, S.tags.filter((t) => t.count).slice(0, 12).map((t) =>
      h('a', { class: `nav-item tagitem ${cur === `#/t/${encodeURIComponent(t.name)}` ? 'active' : ''}`, href: `#/t/${encodeURIComponent(t.name)}`, style: { '--t': t.color } },
        h('span', { class: 'hash' }, '#'), h('span', { class: 'nav-label' }, t.name), h('span', { class: 'count' }, t.count))),
      !S.tags.length ? h('div', { class: 'muted small pad-s' }, '還沒有標籤') : null),
    h('div', { class: 'nav-group' }, link('#/graph', '關聯圖', ov.n_links, { icon: 'graph' }),
      isAdmin() || can('manage') ? link(isAdmin() ? '#/admin' : '#/admin/cats', '管理', ov.n_regs || null, { icon: 'gear', cls: ov.n_regs ? 'attn' : '' }) : null,
      link('#/help', '使用說明', null, { icon: 'help' })),
  );
}

function changePassword() {
  const f = h('form', { class: 'stack', onsubmit: async (e) => {
    e.preventDefault();
    try { await api.post('/api/me/password', Object.fromEntries(new FormData(f))); m.close(); toast('密碼已更新'); } catch (err) { fail(err); }
  } }, h('label', {}, '目前密碼', h('input', { name: 'old', type: 'password', required: true })),
  h('label', {}, '新密碼（至少 6 字元）', h('input', { name: 'new', type: 'password', required: true, minlength: 6 })),
  h('button', { class: 'btn primary', type: 'submit' }, '更新'));
  const m = modal('變更密碼', f);
}

const searchMode = () => { try { return localStorage.getItem('pl-search') || 'kw'; } catch { return 'kw'; } };
const setSearchMode = (m) => { try { localStorage.setItem('pl-search', m); } catch { /* */ } };

// ================================================================== 路由
let cleanup = null;
async function route() {
  document.body.classList.remove('nav-open');
  document.querySelectorAll('.menu').forEach((m) => m.remove());
  if (cleanup) { try { cleanup(); } catch (e) { console.warn(e); } cleanup = null; }
  document.body.classList.remove('reading', 'desk-mode');
  const hash = location.hash || '#/';
  const [path, qs] = hash.slice(1).split('?');
  const params = new URLSearchParams(qs || '');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  const view = $('#view');
  view.className = '';
  view.scrollTop = 0;
  if (parts[0] === 'c' || document.querySelector('#nav .sub-item')) drawNav();   // 分類的資料夾顯示在側欄
  document.querySelectorAll('#nav .nav-item').forEach((a) => a.classList.toggle('active', a.getAttribute('href') === `#${path}` || a.getAttribute('href') === hash));
  try {
    if (!parts.length) return await viewHome(view);
    switch (parts[0]) {
      case 'all': return await viewList(view, { scope: 'all' });
      case 'inbox': return await viewList(view, { scope: 'inbox' });
      case 'reading': return await viewList(view, { scope: 'all', status: '閱讀中', title: '閱讀中', desc: '你標成「閱讀中」的論文（只有你看得到）。' });
      case 'todo': return await viewList(view, { scope: 'all', status: '待讀', title: '我的待讀', desc: '你標成「待讀」的論文，包含別人指派給你的（只有你看得到）。' });
      case 'assigned': return await viewList(view, { scope: params.get('by') === 'me' ? 'assigned_by_me' : 'assigned', title: params.get('by') === 'me' ? '我指派的' : '指派給我' });
      case 'required': return await viewList(view, { scope: 'required', title: '全站必讀' });
      case 'c': return await viewList(view, { scope: 'all', cat: +parts[1], folder: params.get('f') || '' });
      case 't': return await viewList(view, { scope: 'all', tag: parts[1] });
      case 'tags': return await viewTags(view);
      case 'feeds': return await viewFeeds(view, params);
      case 'jsearch': return await viewJournalSearch(view, params);
      case 'meetings': return await viewMeetings(view, params);
      case 'ask': return await viewAsk(view, params);
      case 'reports': cleanup = await viewReports(view, params); return;
      case 's': if (S.searchInput) S.searchInput.value = parts[1] || ''; setSearchMode('kw'); return await viewList(view, { scope: 'all', q: parts[1] || '' });
      case 'sem': if (S.searchInput) S.searchInput.value = parts[1] || ''; setSearchMode('sem'); return await viewList(view, { scope: 'all', q: parts[1] || '', semantic: true });
      case 'params': return await viewParams(view, params);
      case 'figures': return await viewFigures(view, params);
      case 'versions': return await viewVersions(view);
      case 'paths': return parts[1] ? await viewPath(view, +parts[1]) : await viewPaths(view);
      case 'dashboard': return await viewDashboard(view, params);
      case 'p': return await viewPaper(view, +parts[1], parts[2] || '', params);
      case 'desk': cleanup = await viewDesk(view, params); return;
      case 'graph': cleanup = await viewGraph(view, params); return;
      case 'help': cleanup = viewHelp(view, parts[1] || ''); return;
      case 'notes': return await viewNotes(view, params);
      case 'admin': cleanup = await viewAdmin(view, parts[1] || 'users', parts[2] || ''); return;
      default: location.hash = '#/';
    }
  } catch (e) {
    if (e.message !== '請先登入') view.replaceChildren(h('div', { class: 'empty' }, h('p', {}, '載入失敗：', e.message)));
  }
}

// ================================================================== 總覽
function thumbImg(fid, cls = '') {
  return fid ? h('img', { class: cls, loading: 'lazy', src: `/api/files/${fid}/thumb`, alt: '' }) : h('div', { class: `thumb-empty ${cls}` }, icon('file'));
}

async function viewHome(view) {
  const ov = S.overview || await api.get('/api/overview');
  view.classList.add('page');
  const inbox = ov.inbox;
  const catCard = (c) => h('a', { class: 'cat-card', href: `#/c/${c.id}`, style: { '--c': c.color } },
    h('div', { class: 'collage' }, [0, 1, 2, 3].map((i) => thumbImg(c.thumbs[i]))),
    h('div', { class: 'cat-info' },
      h('div', { class: 'cat-name' }, h('span', { class: 'dot', style: { background: c.color } }), c.name),
      h('div', { class: 'cat-desc' }, c.description),
      h('div', { class: 'cat-count' }, h('b', {}, c.count), ' 篇', c.unread ? h('span', { class: 'pill warn' }, `待讀 ${c.unread}`) : null)));
  view.replaceChildren(
    h('div', { class: 'home-head' },
      h('h1', {}, '論文群總覽'),
      h('p', { class: 'muted' }, `共 ${ov.total} 篇論文、${ov.n_ann} 則標註、${ov.n_links} 條關聯。點任一類別看該類全部論文。`)),
    (ov.pinned || []).length ? h('section', { class: 'pin-strip' }, h('h2', {}, icon('pin'), '置頂論文'),
      h('div', { class: 'pin-row' }, ov.pinned.map((p) => h('a', { class: 'pin-card', href: `#/p/${p.id}` }, thumbImg(p.thumb),
        h('div', {}, h('div', { class: 'pin-title' }, p.title), h('div', { class: 'muted small' }, [p.citekey, p.year].filter(Boolean).join(' · '))))))) : null,
    (ov.recent || []).length ? h('section', { class: 'pin-strip recent-strip' }, h('h2', {}, icon('clock'), '最近開啟'),
      h('div', { class: 'pin-row' }, ov.recent.map((p) => { const lp = lastPage(p.id); return h('a', { class: 'pin-card', href: `#/p/${p.id}` }, thumbImg(p.thumb),
        h('div', {}, h('div', { class: 'pin-title' }, p.title), h('div', { class: 'muted small' }, [p.citekey, lp > 1 ? `讀到 p.${lp}` : '', p.status || ''].filter(Boolean).join(' · ')))); }))) : null,
    (() => { const todo = (ov.assigned || []).filter((a) => a.status !== '已閱讀'); return todo.length ? h('a', { class: 'inbox-banner assign', href: '#/assigned' }, icon('at', 'big'),
      h('div', { class: 'grow' }, h('div', { class: 'ib-title' }, `指派給你的論文：${todo.length} 篇還沒讀完`),
        todo.slice(0, 3).map((a) => h('div', { class: 'muted' }, `${a.by || ''}：${a.title}${a.note ? `（${a.note}）` : ''}`))),
      h('span', { class: 'btn' }, '去讀', icon('chev'))) : null; })(),
    ...(ov.my_paths || []).slice(0, 2).map((pt) => h('a', { class: 'inbox-banner path', href: `#/paths/${pt.id}` }, icon('route', 'big'),
      h('div', { class: 'grow' }, h('div', { class: 'ib-title' }, `入門路徑「${pt.title}」：${pt.done}/${pt.total}`),
        h('div', { class: 'muted' }, `下一篇：${pt.next.title}`), pt.next.goal ? h('div', { class: 'muted small' }, `目標：${pt.next.goal}`) : null),
      h('span', { class: 'btn' }, '繼續', icon('chev')))),
    !S.user.has_email ? h('a', { class: 'inbox-banner email-banner', href: '#/', onclick: (e) => { e.preventDefault(); emailReminder(true); } }, icon('bell', 'big'),
      h('div', { class: 'grow' }, h('b', {}, '你的帳號還沒有 Email'), h('div', { class: 'muted small' }, '填了才收得到通知信與每週摘要。點這裡填寫。'))) : null,
    ov.required?.total ? h('a', { class: 'inbox-banner req', href: '#/required' }, icon('flag', 'big'),
      h('div', { class: 'grow' }, h('div', { class: 'ib-title' }, `全站必讀：你已讀 ${ov.required.read} / ${ov.required.total} 篇`),
        h('span', { class: 'prog wide' }, h('span', { style: { width: `${(100 * ov.required.read) / ov.required.total}%` } }))),
      h('span', { class: 'btn' }, '查看', icon('chev'))) : null,
    (ov.my_talks || []).length ? h('a', { class: 'inbox-banner talk', href: '#/meetings' }, icon('calendar', 'big'),
      h('div', { class: 'grow' }, h('div', { class: 'ib-title' }, '你接下來要報告'),
        ov.my_talks.map((t) => h('div', { class: 'muted' }, `${t.date}「${t.title}」：${t.paper_title || '（自由主題）'}`))),
      h('span', { class: 'btn' }, '看組會', icon('chev'))) : null,
    inbox.count ? h('a', { class: 'inbox-banner', href: '#/inbox' },
      h('div', { class: 'collage small' }, [0, 1, 2, 3].map((i) => thumbImg(inbox.thumbs[i]))),
      h('div', {}, h('div', { class: 'ib-title' }, icon('inbox'), ` 未歸檔待讀 ${inbox.count} 篇`),
        h('div', { class: 'muted' }, '新上傳的論文先放這裡。系統會給建議分類，點一下就能歸檔。')),
      h('span', { class: 'btn primary' }, '開始歸檔', icon('chev'))) : null,
    ov.feed_new ? h('a', { class: 'inbox-banner feed', href: '#/feeds' }, icon('rss', 'big'),
      h('div', { class: 'grow' }, h('div', { class: 'ib-title' }, `新論文追蹤：${ov.feed_new} 篇待看`), h('div', { class: 'muted' }, 'arXiv 與期刊 RSS 找到的新論文，挑有興趣的加入論文庫。')),
      h('span', { class: 'btn' }, '去看看', icon('chev'))) : null,
    ...groups().map((g) => h('section', { class: 'cat-section' }, h('h2', {}, g),
      h('div', { class: 'cat-grid' }, S.cats.filter((c) => c.grp === g).map((c) => catCard(ov.categories.find((x) => x.id === c.id) || { ...c, thumbs: [] }))))),
    ov.tags.some((t) => t.count) ? h('section', { class: 'cat-section' }, h('h2', {}, '標籤', h('a', { class: 'h2-more', href: '#/tags' }, '全部標籤 →')),
      h('div', { class: 'tag-strip' }, ov.tags.filter((t) => t.count).slice(0, 16).map((t) => h('a', { class: 'tag big', href: `#/t/${encodeURIComponent(t.name)}`, style: { '--t': t.color } },
        h('span', { class: 'hash' }, '#'), t.name, h('span', { class: 'count' }, t.count))))) : null,
    ov.activity.length ? h('section', { class: 'cat-section' }, h('h2', {}, '最近動態'),
      h('ul', { class: 'activity' }, ov.activity.map((a) => h('li', {},
        h('span', { class: 'muted' }, fmtDate(a.at)), ' ', h('b', {}, a.who || '系統'), ' ',
        { upload: '上傳了', attach: '加入附件到', annotate: '標註了', link: '建立關聯：', edit: '編輯了', import: '匯入了', bulk: '批次整理了', delete: '刪除了', file: '調整檔案：', settings: '變更了網站設定', assign: '指派論文：', required: '設為全站必讀：', status: '更新狀態', feed: '從追蹤加入了', reply: '回覆了討論：', meeting: '排進組會：', ocr: 'OCR 了', zotero: '從 Zotero 匯入了', folder: '新增資料夾', move: '整理到資料夾：', figure: '存了圖卡：', params: '更新參數：', path: '建立入門路徑', path_assign: '安排入門路徑', merge: '合併了重複論文：', published: '偵測到正式發表：', approve: '核准註冊：', reject: '駁回註冊：', owner: '變更站長：', delete_user: '刪除帳號：' }[a.action] || a.action, ' ',
        a.paper_id ? h('a', { href: `#/p/${a.paper_id}` }, a.title || `#${a.paper_id}`) : h('span', {}, a.detail))))) : null,
  );
}

// ================================================================== 論文群（列表）
function paperCard(p, opts) {
  const cats = (p.categories || []).map((id) => S.catById[id]).filter(Boolean);
  const sug = !cats.length && p.suggested_category_id ? S.catById[p.suggested_category_id] : null;
  const sel = h('input', { type: 'checkbox', class: 'sel', 'aria-label': '選取', checked: opts.selected.has(p.id),
    onchange: (e) => { e.target.checked ? opts.selected.add(p.id) : opts.selected.delete(p.id); opts.onSelect(); } });
  const pinned = opts.currentCat ? p.cat_pinned : p.pinned;
  const card = h('article', { class: `card ${opts.selected.has(p.id) ? 'is-sel' : ''} ${pinned ? 'pinned' : ''}`, dataset: { id: p.id },
    draggable: opts.currentCat && can('categorize', p) ? 'true' : null,
    ondragstart: (e) => {
      const ids = opts.selected.has(p.id) ? [...opts.selected] : [p.id];
      e.dataTransfer.setData('text/pl-ids', JSON.stringify(ids)); e.dataTransfer.effectAllowed = 'move';
      document.body.classList.add('dragging');
    },
    ondragend: () => document.body.classList.remove('dragging') },
    opts.selecting ? sel : null,
    pinned ? h('span', { class: 'pin-badge', title: opts.currentCat ? '在此分類置頂' : '全站置頂' }, icon('pin')) : null,
    can('categorize', p) || can('meeting') || can('manage') ? h('button', { class: 'icon-btn sm card-menu', 'aria-label': '更多', onclick: (e) => cardMenu(e.currentTarget, p, opts) }, icon('more')) : null,
    h('a', { class: 'thumb', href: `#/p/${p.id}` }, thumbImg(p.thumb),
      p.status ? h('span', { class: `status s-${p.status}` }, p.status) : null),
    h('div', { class: 'card-body' },
      h('a', { class: 'card-title', href: `#/p/${p.id}` }, p.title),
      h('div', { class: 'card-meta' }, [authorLine(p), p.year, p.venue].filter(Boolean).join(' · ')),
      p.snippet ? h('div', { class: 'snippet', html: snippetHtml(p.snippet) }) : null,
      (p.assignees || []).length ? h('div', { class: `assignees ${p.assigned_to_me ? 'me' : ''}`, title: p.my_assign ? `${p.my_assign.by || ''} 指派${p.my_assign.note ? `：${p.my_assign.note}` : ''}` : '已指派' },
        icon('at'), p.assignees.map((n) => `@${n}`).join(' '), p.my_assign?.note ? h('span', { class: 'muted' }, ` · ${p.my_assign.note}`) : null) : null,
      h('div', { class: 'chips' },
        cats.filter((c) => c.id !== opts.currentCat).map((c) => h('a', { class: 'chip', href: `#/c/${c.id}`, style: { '--c': c.color } }, h('span', { class: 'dot', style: { background: c.color } }), c.name)),
        (p.tags || []).filter((t) => t !== opts.currentTag).map((t) => tagChip(t))),
      h('div', { class: 'card-foot' },
        p.required ? h('span', { class: 'badge req' }, icon('flag'), '必讀') : null,
        p.kind !== 'article' ? h('span', { class: 'badge' }, KIND[p.kind] || p.kind) : null,
        p.has_sm ? h('span', { class: 'badge' }, 'SM') : null,
        p.n_ann ? h('span', { class: 'badge' }, icon('pen'), p.n_ann) : null,
        p.n_links ? h('span', { class: 'badge' }, icon('link'), p.n_links) : null,
        h('span', { class: 'grow' }), h('span', { class: 'muted small' }, p.citekey)),
      !cats.length && can('categorize', p) ? h('div', { class: 'suggest' },
        sug ? h('button', { class: 'btn small', style: { '--c': sug.color }, onclick: () => opts.file(p.id, [sug.id]) },
          h('span', { class: 'dot', style: { background: sug.color } }), `歸到「${sug.name}」`) : h('span', { class: 'muted small' }, '尚未分類'),
        h('button', { class: 'btn small ghost', onclick: (e) => pickCategories(e.currentTarget, [], (ids) => opts.file(p.id, ids)) }, '選分類…')) : null));
  return card;
}

// 指派論文給一位或多位成員（公開，被指派的人會收到通知）
async function assignDialog(pids, done, current = []) {
  const users = (await api.get('/api/users')).filter((u) => !u.disabled);
  const chosen = new Set();
  const note = h('input', { placeholder: '說明（選填），例如：下週組會前讀完、重點看 Fig. 3' });
  const md = modal(pids.length > 1 ? `指派 ${pids.length} 篇論文` : '指派這篇論文', h('div', { class: 'stack' },
    h('p', { class: 'muted small' }, '點選要指派的成員（可複選）。指派是全站可見的；被指派的人會收到通知，並自動列入他的「待讀」。'),
    h('div', { class: 'chips wrap' }, users.map((u) => h('button', { class: `chip toggle at-chip ${current.includes(u.id) ? 'on' : ''}`, disabled: current.includes(u.id),
      onclick: (e) => { chosen.has(u.id) ? chosen.delete(u.id) : chosen.add(u.id); e.currentTarget.classList.toggle('on'); } }, `@${u.display_name}`))),
    note,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      if (!chosen.size) return toast('請至少選一位成員');
      try { await api.post(`/api/papers/${pids[0]}/assign`, { user_ids: [...chosen], note: note.value, paper_ids: pids }); md.close(); toast(`已指派給 ${chosen.size} 位成員`); done && done(); } catch (e) { fail(e); }
    } }, '指派'))));
}

function cardMenu(anchor, p, opts) {
  const cat = opts.currentCat ? S.catById[opts.currentCat] : null;
  const pinned = cat ? p.cat_pinned : p.pinned;
  menu(anchor, [
    can('categorize', p) && { label: pinned ? '取消置頂' : (cat ? `在「${cat.name}」置頂` : '全站置頂'), icon: 'pin', run: async () => {
      try { await api.post(`/api/papers/${p.id}/pin`, { pinned: !pinned, cat: cat?.id || null }); toast(pinned ? '已取消置頂' : '已置頂'); opts.reload(); } catch (e) { fail(e); }
    } },
    cat && can('categorize', p) ? { label: '移到資料夾…', icon: 'folder', run: () => folderMenu(anchor, cat, [p.id], opts.reload) } : null,
    can('meeting') ? { label: '指派給…', icon: 'at', run: () => assignDialog([p.id], opts.reload) } : null,
    can('manage') ? { label: p.required ? '取消全站必讀' : '設為全站必讀', icon: 'flag', run: async () => { await api.post(`/api/papers/${p.id}/required`, { required: !p.required }); toast(p.required ? '已取消必讀' : '已設為全站必讀'); refreshNav(); opts.reload(); } } : null,
    !cat && can('categorize', p) && (p.categories || []).length ? { label: '移到資料夾…', icon: 'folder', run: () => menu(anchor, p.categories.map((cid) => S.catById[cid]).filter(Boolean)
      .map((c) => ({ label: `「${c.name}」裡的資料夾…`, run: () => folderMenu(anchor, c, [p.id], opts.reload) }))) } : null,
  ]);
}

// 選資料夾（含「移出資料夾」與「新資料夾」）
function folderMenu(anchor, cat, ids, done) {
  const move = async (fid) => {
    try { await api.post('/api/papers/move', { ids, cat: cat.id, folder_id: fid }); toast(fid ? '已移到資料夾' : '已移出資料夾'); await refreshNav(); done && done(); } catch (e) { fail(e); }
  };
  menu(anchor, [
    ...(cat.folders || []).map((f) => ({ label: f.name, icon: 'folder', run: () => move(f.id) })),
    (cat.folders || []).length ? '-' : null,
    { label: '不放資料夾', run: () => move(null) },
    can('categorize') ? { label: '新資料夾…', icon: 'plus', run: async () => {
      const n = prompt(`在「${cat.name}」新增資料夾`); if (!n) return;
      try { const r = await api.post(`/api/categories/${cat.id}/folders`, { name: n }); await loadMeta(); move(r.id); } catch (e) { fail(e); }
    } } : null,
  ]);
}

function pickCategories(anchor, selected, done) {
  const chosen = new Set(selected);
  const body = h('div', { class: 'catpick' },
    groups().map((g) => h('div', {}, h('div', { class: 'nav-head' }, g),
      S.cats.filter((c) => c.grp === g).map((c) => h('label', { class: 'check' },
        h('input', { type: 'checkbox', checked: chosen.has(c.id), onchange: (e) => (e.target.checked ? chosen.add(c.id) : chosen.delete(c.id)) }),
        h('span', { class: 'dot', style: { background: c.color } }), c.name)))),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: () => { m.close(); done([...chosen]); } }, '確定')));
  const m = modal('選擇分類（可複選）', body);
}

function pickTags(done) {
  const chosen = new Set();
  const extra = h('input', { placeholder: '新標籤（多個用逗號分隔）' });
  const body = h('div', { class: 'stack' },
    S.tags.length ? h('div', { class: 'chips wrap' }, S.tags.map((t) => h('button', { class: 'tag toggle', style: { '--t': t.color },
      onclick: (e) => { chosen.has(t.name) ? chosen.delete(t.name) : chosen.add(t.name); e.currentTarget.classList.toggle('on'); } }, h('span', { class: 'hash' }, '#'), t.name))) : null,
    extra,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: () => {
      extra.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean).forEach((x) => chosen.add(x));
      m.close(); if (chosen.size) done([...chosen]);
    } }, '加上')));
  const m = modal('加標籤（可複選）', body);
}

async function viewTags(view) {
  view.classList.add('page');
  const ov = await api.get('/api/overview');
  const card = (t) => h('a', { class: 'cat-card tag-card', href: `#/t/${encodeURIComponent(t.name)}`, style: { '--c': t.color } },
    h('div', { class: 'collage' }, [0, 1, 2, 3].map((i) => thumbImg(t.thumbs[i]))),
    h('div', { class: 'cat-info' },
      h('div', { class: 'cat-name' }, h('span', { class: 'hash', style: { color: t.color } }, '#'), t.name),
      h('div', { class: 'cat-desc' }, t.description),
      h('div', { class: 'cat-count' }, h('b', {}, t.count), ' 篇')));
  view.replaceChildren(
    h('div', { class: 'home-head' }, h('h1', {}, '標籤'),
      h('p', { class: 'muted' }, '標籤和分類是分開的：分類是論文屬於哪一種架設（一篇通常一到兩類）；標籤是跨分類的自由註記，例如專案書單「P1」、「推甄 Ref」、「研究中」、「組會報告」。'),
      can('manage') ? h('a', { class: 'btn small', href: '#/admin/tags' }, icon('gear'), '管理標籤') : null),
    ov.tags.length ? h('div', { class: 'cat-grid' }, ov.tags.map(card)) : h('div', { class: 'empty' }, icon('tag', 'big'), h('p', {}, '還沒有標籤。在論文的「重點」分頁加上標籤。')));
}

async function viewList(view, params) {
  view.classList.add('page');
  const st = { ...params, sort: params.q ? 'relevance' : 'year', status: params.status || '', offset: 0, items: [], total: 0, mode: localStorage.getItem('pl-mode') || 'grid', selecting: false, selected: new Set() };
  const cat = st.cat ? S.catById[st.cat] : null;
  const tagInfo = st.tag ? S.tagByName[st.tag] : null;
  let title = '全部論文', desc = '';
  if (st.scope === 'inbox') { title = '未歸檔待讀'; desc = '還沒有分類的論文。用卡片上的建議一鍵歸檔，或勾選多篇批次處理。'; }
  if (cat) { title = cat.name; desc = cat.description; }
  const curFolder = cat && st.folder && st.folder !== 'none' ? (cat.folders || []).find((f) => String(f.id) === st.folder) : null;
  if (curFolder) title = `${cat.name} ／ ${curFolder.name}`;
  if (cat && st.folder === 'none') title = `${cat.name} ／ 不在資料夾`;
  if (st.tag) { title = `#${st.tag}`; desc = tagInfo?.description || ''; }
  if (st.q) title = st.semantic ? `語意搜尋：「${st.q}」` : `搜尋：「${st.q}」`;
  const modeNote = h('div', { class: 'search-mode' });
  if (params.title) title = params.title;
  if (params.desc) desc = params.desc;
  if (st.scope === 'assigned') desc = '別人指派你讀的論文。讀完把狀態改成「已閱讀」，指派的人會收到通知。';
  if (st.scope === 'assigned_by_me') desc = '你指派給別人的論文；卡片上打勾的名字代表已讀完。';
  if (st.scope === 'required') desc = '實驗室每個人都該讀的論文。進度用你自己的「已閱讀」狀態計算。';

  const grid = h('div', { class: 'cards' });
  const countEl = h('span', { class: 'muted' });
  const more = h('button', { class: 'btn block', onclick: () => load(false) }, '載入更多');
  const selBar = h('div', { class: 'selbar', hidden: true });

  const fileTo = async (id, ids) => {
    try { await api.post('/api/papers/bulk', { ids: [id], add_categories: ids }); toast('已歸檔'); await refreshNav(); await load(true); } catch (e) { fail(e); }
  };
  const opts = { selected: st.selected, currentCat: st.cat, currentTag: st.tag, get selecting() { return st.selecting; }, onSelect: () => renderSelBar(), file: fileTo,
    reload: async () => { await loadMeta(); drawFolders(); await load(true); } };
  const folderBar = h('div', { class: 'folder-bar' });
  const dropTarget = (el, fid) => {
    if (!can('categorize')) return el;
    el.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('text/pl-ids')) { e.preventDefault(); el.classList.add('drop'); } });
    el.addEventListener('dragleave', () => el.classList.remove('drop'));
    el.addEventListener('drop', async (e) => {
      e.preventDefault(); el.classList.remove('drop');
      const ids = JSON.parse(e.dataTransfer.getData('text/pl-ids') || '[]');
      if (!ids.length) return;
      try { await api.post('/api/papers/move', { ids, cat: cat.id, folder_id: fid }); toast(`已移動 ${ids.length} 篇`); st.selected.clear(); await opts.reload(); refreshNav(); } catch (err) { fail(err); }
    });
    return el;
  };
  function drawFolders() {
    if (!cat) return;
    const c = S.catById[cat.id] || cat;
    const fs = c.folders || [];
    const inFolders = fs.reduce((a, f) => a + f.count, 0);
    const chip = (key, label, n, fid, extra) => {
      const el = h('a', { class: `folder-chip ${String(st.folder || '') === String(key) ? 'on' : ''}`, href: `#/c/${c.id}${key ? `?f=${key}` : ''}` },
        icon(key === '' ? 'stack' : key === 'none' ? 'inbox' : 'folder'), label, h('span', { class: 'count' }, n), extra);
      return key === '' ? el : dropTarget(el, fid);
    };
    folderBar.hidden = !fs.length && !can('categorize');
    folderBar.replaceChildren(
      chip('', '全部', c.count, null),
      ...fs.map((f) => chip(f.id, f.name, f.count, f.id, can('categorize') ? h('button', { class: 'fc-more', 'aria-label': '資料夾選項', onclick: (e) => { e.preventDefault(); e.stopPropagation(); menu(e.currentTarget, [
        { label: '改名', icon: 'pen', run: async () => { const n = prompt('資料夾名稱', f.name); if (n) { await api.patch(`/api/folders/${f.id}`, { name: n }); opts.reload(); refreshNav(); } } },
        { label: '往前移', icon: 'up', run: async () => { const ids = fs.map((x) => x.id); const i = ids.indexOf(f.id); if (i > 0) { [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; await api.post(`/api/categories/${c.id}/folders/order`, { ids }); opts.reload(); refreshNav(); } } },
        { label: '刪除資料夾（論文留在分類裡）', icon: 'trash', danger: true, run: async () => { if (await confirmBox(`刪除資料夾「${f.name}」？裡面的 ${f.count} 篇論文會回到「不在資料夾」，不會被刪除。`, '刪除')) { await api.del(`/api/folders/${f.id}`); if (String(st.folder) === String(f.id)) location.hash = `#/c/${c.id}`; else { opts.reload(); refreshNav(); } } } },
      ]); } }, '⋯') : null)),
      fs.length ? chip('none', '不在資料夾', Math.max(0, c.count - inFolders), null) : null,
      can('categorize') ? h('button', { class: 'folder-chip add', onclick: async () => { const n = prompt(`在「${c.name}」新增資料夾（例如：理論、實驗、回顧）`); if (n) { try { await api.post(`/api/categories/${c.id}/folders`, { name: n }); await opts.reload(); refreshNav(); } catch (e) { fail(e); } } } }, icon('plus'), '新資料夾') : null,
      can('categorize') && fs.length ? h('span', { class: 'muted small hide-mobile' }, '可以把論文卡片拖到資料夾上') : null);
  }

  function renderSelBar() {
    const n = st.selected.size;
    selBar.hidden = !st.selecting;
    grid.querySelectorAll('.card').forEach((c) => c.classList.toggle('is-sel', st.selected.has(+c.dataset.id)));
    const ids = () => [...st.selected];
    const run = async (body, msg) => { if (!n) return toast('請先勾選論文'); try { await api.post('/api/papers/bulk', { ids: ids(), ...body }); toast(msg); st.selected.clear(); await refreshNav(); await load(true); } catch (e) { fail(e); } };
    selBar.replaceChildren(
      h('span', {}, `已選 ${n} 篇`),
      h('button', { class: 'btn small', onclick: () => { st.items.forEach((p) => st.selected.add(p.id)); renderSelBar(); } }, '全選'),
      can('categorize') ? h('button', { class: 'btn small', onclick: (e) => pickCategories(e.currentTarget, [], (cats) => run({ add_categories: cats }, '已加入分類')) }, icon('folder'), '加入分類') : null,
      can('categorize') && st.scope === 'inbox' ? h('button', { class: 'btn small', onclick: () => run({ accept_suggestion: true }, '已套用建議分類') }, '套用建議') : null,
      can('categorize') && cat ? h('button', { class: 'btn small', onclick: (e) => { if (!n) return toast('請先勾選論文'); folderMenu(e.currentTarget, S.catById[cat.id] || cat, ids(), () => { st.selected.clear(); opts.reload(); }); } }, icon('folder'), '移到資料夾') : null,
      can('categorize') && cat ? h('button', { class: 'btn small', onclick: () => run({ remove_categories: [cat.id] }, '已移出此分類') }, '移出此分類') : null,
      can('tag') ? h('button', { class: 'btn small', onclick: () => pickTags((t) => run({ add_tags: t }, '已加標籤')) }, icon('tag'), '加標籤') : null,
      can('tag') && st.tag ? h('button', { class: 'btn small', onclick: () => run({ remove_tags: [st.tag] }, '已移除標籤') }, `移除 #${st.tag}`) : null,
      h('button', { class: 'btn small', title: '閱讀狀態只有你看得到', onclick: (e) => menu(e.currentTarget, [...S.site.statuses.map((s) => ({ label: s, run: () => run({ status: s }, `已設為${s}`) })), { label: '清除（未標記）', run: () => run({ status: '' }, '已清除狀態') }]) }, '我的狀態'),
      can('meeting') ? h('button', { class: 'btn small', onclick: () => { if (!n) return toast('請先勾選論文'); assignDialog(ids(), () => { st.selected.clear(); refreshNav(); load(true); }); } }, icon('at'), '指派') : null,
      h('button', { class: 'btn small', onclick: () => { if (!n) return toast('請先勾選論文'); location.hash = `#/params?ids=${ids().join(',')}`; } }, icon('table'), '比較參數'),
      can('manage') ? h('button', { class: 'btn small', onclick: async (e) => { if (!n) return toast('請先勾選論文'); menu(e.currentTarget, [
        { label: '設為全站必讀', icon: 'flag', run: async () => { await api.post(`/api/papers/${ids()[0]}/required`, { required: true, paper_ids: ids() }); toast('已設為必讀'); st.selected.clear(); refreshNav(); load(true); } },
        { label: '取消全站必讀', run: async () => { await api.post(`/api/papers/${ids()[0]}/required`, { required: false, paper_ids: ids() }); toast('已取消'); st.selected.clear(); refreshNav(); load(true); } }]); } }, icon('flag'), '必讀') : null,
      h('span', { class: 'grow' }),
      h('button', { class: 'btn small ghost', onclick: () => { st.selecting = false; st.selected.clear(); draw(); } }, '完成'));
  }

  const statusSel = h('select', { 'aria-label': '我的閱讀狀態', title: '閱讀狀態只有你看得到', onchange: (e) => { st.status = e.target.value; load(true); } },
    h('option', { value: '' }, '我的狀態：全部'), ...S.site.statuses.map((s) => h('option', { value: s, selected: st.status === s }, s)), h('option', { value: 'none' }, '未標記'));
  const sortSel = h('select', { 'aria-label': '排序', onchange: (e) => { st.sort = e.target.value; load(true); } },
    st.q ? h('option', { value: 'relevance' }, '相關度') : null,
    h('option', { value: 'year', selected: !st.q }, '年份新→舊'), h('option', { value: 'year_asc' }, '年份舊→新'),
    h('option', { value: 'added' }, '最近加入'), h('option', { value: 'updated' }, '最近修改'), h('option', { value: 'title' }, '標題'), h('option', { value: 'citekey' }, 'citekey'));
  const modeBtn = h('button', { class: 'icon-btn', 'aria-label': '切換檢視', onclick: () => { st.mode = st.mode === 'grid' ? 'list' : 'grid'; localStorage.setItem('pl-mode', st.mode); draw(); } }, icon('list'));
  const exportUrl = `/api/export/bibtex?scope=${st.scope}${st.cat ? `&cat=${st.cat}` : ''}${st.tag ? `&tag=${encodeURIComponent(st.tag)}` : ''}`;

  view.replaceChildren(
    h('div', { class: 'list-head', style: cat ? { '--c': cat.color } : {} },
      h('div', {}, h('h1', {}, cat ? h('span', { class: 'dot big', style: { background: cat.color } }) : null,
        tagInfo ? h('span', { class: 'kind-label tag-l', style: { '--t': tagInfo.color } }, '標籤') : cat ? h('span', { class: 'kind-label' }, '分類') : null, title),
        desc ? h('p', { class: 'muted' }, desc) : null),
      h('div', { class: 'row' }, countEl, cat && can('ai') && S.site.ai?.enabled ? h('a', { class: 'btn small ghost', href: `#/reports?cat=${cat.id}` }, icon('star'), 'AI 綜述') : null)),
    cat ? folderBar : null,
    st.q ? modeNote : null,
    h('div', { class: 'toolbar' }, st.semantic ? null : statusSel, st.semantic ? null : sortSel, h('span', { class: 'grow' }),
      h('button', { class: 'btn small', onclick: () => { st.selecting = !st.selecting; draw(); } }, icon('check'), '選取'),
      h('a', { class: 'btn small ghost', href: exportUrl, download: 'papers.bib' }, 'BibTeX'), modeBtn),
    grid, more, selBar);

  // 分類的「全部」檢視：依資料夾分段（置頂 → 各資料夾 → 不在資料夾）
  const grouped = () => cat && !st.folder && !st.status && !st.q && (S.catById[cat.id]?.folders || []).length > 0;
  function section(key, title, items, fid, ic) {
    let closed = false; try { closed = localStorage.getItem(`pl-fold-${cat.id}-${key}`) === '1'; } catch { /* */ }
    const body = h('div', { class: `cards ${st.mode}`, hidden: closed }, items.map((p) => paperCard(p, opts)));
    const head = h('button', { class: 'sec-head', onclick: () => { body.hidden = !body.hidden; head.classList.toggle('closed', body.hidden); try { localStorage.setItem(`pl-fold-${cat.id}-${key}`, body.hidden ? '1' : '0'); } catch { /* */ } } },
      h('span', { class: 'caret' }, '▾'), icon(ic), h('b', {}, title), h('span', { class: 'count' }, items.length));
    if (closed) head.classList.add('closed');
    return h('section', { class: 'folder-sec' }, key === 'pin' ? head : dropTarget(head, fid), body);
  }
  function draw() {
    if (grouped() && st.items.length) {
      const fs = S.catById[cat.id].folders;
      const pins = st.items.filter((p) => p.cat_pinned);
      const rest = st.items.filter((p) => !p.cat_pinned);
      grid.className = 'folder-groups';
      grid.replaceChildren(
        pins.length ? section('pin', '置頂', pins, null, 'pin') : null,
        ...fs.map((f) => section(f.id, f.name, rest.filter((p) => p.folder_id === f.id), f.id, 'folder')),
        section('none', '不在資料夾', rest.filter((p) => !p.folder_id), null, 'inbox'));
      countEl.textContent = `${st.total} 篇`;
      more.hidden = st.items.length >= st.total;
      renderSelBar();
      return;
    }
    grid.className = `cards ${st.mode}`;
    grid.replaceChildren(...(st.items.length ? st.items.map((p) => paperCard(p, opts)) :
      [h('div', { class: 'empty' }, icon('inbox', 'big'), h('p', {}, st.q ? '沒有找到符合的論文。' : st.scope === 'inbox' ? '全部都歸檔好了。' : '這裡還沒有論文。'))]));
    countEl.textContent = `${st.total} 篇`;
    more.hidden = st.items.length >= st.total;
    renderSelBar();
  }
  async function load(reset) {
    if (reset) { st.offset = 0; st.items = []; }
    if (st.semantic) {
      const r = await api.get(`/api/semantic?q=${encodeURIComponent(st.q)}`);
      st.items = r.items.map((p) => ({ ...p, snippet: p.why?.length ? `相關：${p.why.map((w) => `⟦${w}⟧`).join('、')}` : '' }));
      st.total = r.total; st.offset = st.items.length;
      modeNote.replaceChildren(icon('sim'), h('span', {}, r.mode === 'embedding' ? '語意搜尋（向量模型）：依意思找，換句話說、中英文都能對到。' : '語意搜尋（內建）：依內容關鍵詞的相似度排序' + (r.keywords?.length ? `；AI 把問題轉成：${r.keywords.join('、')}` : '') + '。'),
        h('a', { class: 'btn small ghost', href: `#/s/${encodeURIComponent(st.q)}` }, '改用關鍵字搜尋'));
      draw();
      return;
    }
    if (st.q) modeNote.replaceChildren(icon('search'), h('span', {}, '關鍵字搜尋：標題、作者、全文、筆記裡出現這些字的論文。'),
      h('a', { class: 'btn small ghost', href: `#/sem/${encodeURIComponent(st.q)}` }, icon('sim'), '改用語意搜尋（依意思找）'));
    const q = new URLSearchParams({ scope: st.scope, sort: st.sort, limit: grouped() ? 500 : 60, offset: st.offset });
    if (st.cat) q.set('cat', st.cat);
    if (st.cat && st.folder) q.set('folder', st.folder);
    if (st.tag) q.set('tag', st.tag);
    if (st.q) q.set('q', st.q);
    if (st.status) q.set('status', st.status);
    const r = await api.get(`/api/papers?${q}`);
    st.items = st.items.concat(r.items); st.total = r.total; st.offset = st.items.length;
    draw();
  }
  drawFolders();
  if (st.scope === 'required') requiredProgress(grid);
  await load(true);
}

// 全站必讀的進度（管理員看得到每位成員；一般成員只看自己的）
async function requiredProgress(before) {
  const r = await api.get('/api/required/progress');
  if (!r.papers.length) return;
  const bar = (n, t) => h('span', { class: 'prog' }, h('span', { style: { width: `${t ? (100 * n) / t : 0}%` } }));
  const box = h('div', { class: 'card-form req-prog' },
    h('b', {}, r.all ? '成員進度' : '我的進度'),
    ...r.users.map((u) => h('details', { class: 'req-user' },
      h('summary', {}, h('span', { class: 'req-name' }, u.display_name), bar(u.n_done, r.papers.length), h('span', { class: 'muted small' }, `${u.n_done}/${r.papers.length}`)),
      h('div', { class: 'chips wrap' }, r.papers.map((p) => h('a', { class: `chip ${u.done.includes(p.id) ? 'done' : ''}`, href: `#/p/${p.id}`, title: p.title }, u.done.includes(p.id) ? '✓ ' : '', p.citekey))))));
  before.before(box);
}

// ================================================================== 單篇論文
async function viewPaper(view, pid, tab, params) {
  view.classList.add('paper-view');
  document.body.classList.add('reading');
  const m = await mountPaper(view, pid, { tab, params });
  cleanup = m.destroy;
}

// 右側欄收合（記在這台裝置；閱讀桌裡所有論文一起收合）
// 標註的顯示範圍：預設只看自己的、新增的都是私人；開啟「全域筆記」才看所有人的公開標註、新增的也公開
const annGlobal = () => { try { return localStorage.getItem('pl-ann-global') === '1'; } catch { return false; } };
function setAnnGlobal(on) {
  try { localStorage.setItem('pl-ann-global', on ? '1' : '0'); } catch { /* */ }
  window.dispatchEvent(new Event('pl-ann-global'));
}
function annGlobalSwitch() {
  const on = annGlobal();
  return h('button', { class: `ann-scope ${on ? 'on' : ''}`, role: 'switch', 'aria-checked': on ? 'true' : 'false',
    title: on ? '全域筆記：顯示所有人的公開標註，新增的標註會公開。按一下改回只看自己的。' : '個人模式：只顯示你自己的標註，新增的標註只有你看得到。按一下改成全域筆記。',
    onclick: () => { setAnnGlobal(!annGlobal()); toast(annGlobal() ? '全域筆記：顯示所有人的標註，新增的會公開' : '個人模式：只顯示你自己的標註，新增的只有你看得到'); } },
    h('span', { class: 'ann-scope-track' }, h('span', { class: 'ann-scope-knob' })), h('span', {}, '全域筆記'));
}
const panelOff = () => { try { return localStorage.getItem('pl-panel-off') === '1'; } catch { return false; } };
function setPanelOff(on) {
  try { localStorage.setItem('pl-panel-off', on ? '1' : '0'); } catch { /* */ }
  window.dispatchEvent(new Event('pl-panel'));
}
// 每篇論文讀到第幾頁（記在這台裝置，下次打開從這頁繼續）
const LP = 'pl-lastpage';
function lastPage(pid) { try { return (JSON.parse(localStorage.getItem(LP) || '{}')[pid] || [0])[0]; } catch { return 0; } }
const saveLastPage = debounce((pid, n) => {
  try {
    const m = JSON.parse(localStorage.getItem(LP) || '{}');
    m[pid] = [n, Date.now()];
    const keys = Object.keys(m);
    if (keys.length > 300) keys.sort((a, b) => m[a][1] - m[b][1]).slice(0, keys.length - 300).forEach((k) => delete m[k]);
    localStorage.setItem(LP, JSON.stringify(m));
  } catch { /* */ }
}, 800);

// 在任一容器裡掛一篇論文（整頁閱讀或閱讀桌的一格）。回傳 { destroy, page }。
// opts.desk = { onClose, onFocus }：在閱讀桌裡，返回鍵改成關閉，站內連結交給閱讀桌處理。
async function mountPaper(view, pid, { tab = '', params = new URLSearchParams(), desk = null } = {}) {
  let p = await api.get(`/api/papers/${pid}`);
  let anns = applyPending(await api.get(`/api/papers/${pid}/annotations`), pid);
  // 連線慢時 Service Worker 會先給這台裝置上的舊資料：稍後自動換成伺服器的最新標註
  if (anns._stale) setTimeout(async function again() {
    try { const fresh = await api.get(`/api/papers/${pid}/annotations`); if (fresh._stale) return setTimeout(again, 8000); anns = applyPending(fresh, pid); if (viewer.doc) viewer.setAnnotations(annsFor(fileId)); drawTabs(); if (tabState.t === 'ann') drawPanel(); } catch { setTimeout(again, 8000); }
  }, 3000);
  const mtab = h('nav', { class: 'mtabs' });
  const reader = h('section', { class: 'reader' });
  const panel = h('aside', { class: 'panel' });
  const root = h('div', { class: `paper ${panelOff() ? 'no-panel' : ''}`, dataset: { m: 'read' } });
  const onPanelPref = () => { root.classList.toggle('no-panel', panelOff()); if (panelOff() && root.dataset.m !== 'read') { root.dataset.m = 'read'; drawTabs(); } drawHead(); setTimeout(() => viewer.refit(), 60); };
  window.addEventListener('pl-panel', onPanelPref);
  const onAnnScope = () => { if (viewer.doc) viewer.setAnnotations(annsFor(fileId)); drawTabs(); if (tabState.t === 'ann') panelAnn(); if (viewer.inking) drawInkBar(); };
  window.addEventListener('pl-ann-global', onAnnScope);
  // 同一篇的筆記頁有新手寫：更新「標註」分頁
  const onNb = async (e) => {
    if (e.detail?.pid !== pid || e.detail?.from === 'paper') return;
    try { anns = applyPending(await api.get(`/api/papers/${pid}/annotations`), pid); drawTabs(); if (tabState.t === 'ann') redrawAnnSoon(); } catch { /* */ }
  };
  const onNbDebounced = debounce(onNb, 1500);
  window.addEventListener('pl-nb-changed', onNbDebounced);
  const onDeskNb = () => reader.querySelector('.nb-btn')?.classList.toggle('on', notebookOpenFor(pid));
  window.addEventListener('pl-desk-changed', onDeskNb);
  const onFocusAnn = (e) => { if (e.detail?.pid !== pid) return; const id = e.detail.ann; forceShow.add(id); if (viewer.doc) viewer.setAnnotations(annsFor(fileId)); switchTab('ann', 'keep'); setTimeout(() => focusAnnCard(id), 200); };
  window.addEventListener('pl-focus-ann', onFocusAnn);
  const head = h('div', { class: 'paper-head' });
  root.append(head, h('div', { class: 'paper-body' }, reader, panel), mtab);
  view.replaceChildren(root);
  const narrow = () => (root.clientWidth || window.innerWidth) < 900;
  if (desk) root.addEventListener('pointerdown', () => desk.onFocus && desk.onFocus(), true);

  // ---------- 標題列
  function drawHead() {
    head.replaceChildren(
      desk ? h('button', { class: 'icon-btn', 'aria-label': '關閉這篇', title: '從閱讀桌關閉', onclick: () => desk.onClose() }, icon('x'))
        : h('button', { class: 'icon-btn', 'aria-label': '返回', onclick: () => history.length > 1 ? history.back() : (location.hash = '#/') }, icon('back')),
      h('div', { class: 'ph-text' },
        h('div', { class: 'ph-title', title: p.title }, p.title),
        h('div', { class: 'ph-meta' }, [authorLine(p), p.year, p.venue].filter(Boolean).join(' · '),
          p.doi ? h('a', { href: `https://doi.org/${p.doi}`, target: '_blank', rel: 'noopener' }, ' DOI') : null),
        h('div', { class: 'ph-badges' },
          p.required ? h('span', { class: 'pill req', title: p.required_note || '實驗室每個人都該讀' }, icon('flag'), ' 全站必讀') : null,
          (p.assignments || []).length ? h('span', { class: 'assign-line' }, icon('at'), p.assignments.map((a) =>
            h('span', { class: `at-name ${a.done ? 'done' : ''}`, title: `${a.by || ''} 指派${a.note ? `：${a.note}` : ''}${a.done ? '（已讀完）' : ''}` },
              `@${a.name}`, a.done ? ' ✓' : '',
              (can('meeting') || a.user_id === S.user.id || a.assigned_by === S.user.id) ? h('button', { class: 'at-x', 'aria-label': `取消指派 ${a.name}`, onclick: async (e) => { e.stopPropagation(); if (await confirmBox(`取消指派給 ${a.name}？`, '取消指派')) { await api.del(`/api/papers/${pid}/assign/${a.user_id}`); p = await api.get(`/api/papers/${pid}`); drawHead(); refreshNav(); } } }, '×') : null))) : null,
          (p.paths || []).map((pt) => h('a', { class: 'pill path', href: `#/paths/${pt.id}`, title: pt.goal ? `目標：${pt.goal}` : pt.title }, icon('route'), ` ${pt.title} ${pt.step}/${pt.total}`)),
          (p.versions || []).some((v) => v.kind === 'published' && v.status === 'applied') ? h('span', { class: 'pill ok', title: '原本是預印本，系統偵測到正式發表並更新了書目' }, icon('check'), ' 已正式發表') : null,
          (p.versions || []).some((v) => v.status === 'new') ? h('a', { class: 'pill warn', href: '#/versions', title: '找到正式版或可能重複的論文' }, icon('merge'), ' 版本待確認') : null,
          (p.meetings || []).filter((m) => m.date >= new Date().toISOString().slice(0, 10)).map((m) =>
            h('a', { class: 'pill meet', href: '#/meetings' }, icon('calendar'), ` ${m.date.slice(5)} ${m.presenter || '未定'}報告`)))),
      h('button', { class: 'icon-btn panel-toggle', 'aria-label': '收起或展開右側欄', title: root.classList.contains('no-panel') ? '展開右側欄（重點、標註…）' : '收起右側欄，PDF 放大',
        onclick: () => setPanelOff(!root.classList.contains('no-panel')) },
        icon(root.classList.contains('no-panel') ? 'panelOpen' : 'panelClose')),
      h('select', { class: 'status-sel', 'aria-label': '我的閱讀狀態', title: '閱讀狀態只有你看得到', onchange: async (e) => { p = await api.patch(`/api/papers/${pid}`, { status: e.target.value || null }); toast('已更新你的閱讀狀態（只有你看得到）'); drawHead(); refreshNav(); } },
        h('option', { value: '' }, '未標記'), ...S.site.statuses.map((s) => h('option', { value: s, selected: p.status === s }, s))),
      h('button', { class: 'icon-btn', 'aria-label': '更多', onclick: (e) => menu(e.currentTarget, [
        { label: '複製 BibTeX', icon: 'copy', run: async () => { const t = await api.get(`/api/papers/${pid}/bibtex`); await navigator.clipboard.writeText(t); toast('已複製'); } },
        { label: '複製此頁連結', icon: 'link', run: async () => { await navigator.clipboard.writeText(location.href); toast('已複製連結'); } },
        { label: '在關聯圖中查看', icon: 'graph', run: () => go(`#/graph?focus=${pid}`) },
        !desk ? { label: '加入閱讀桌（多篇並排）', icon: 'stack', run: () => openInDesk(pid) } : null,
        can('meeting') ? { label: '指派給成員…', icon: 'at', run: () => assignDialog([pid], async () => { p = await api.get(`/api/papers/${pid}`); drawHead(); refreshNav(); }, (p.assignments || []).map((a) => a.user_id)) } : null,
        can('manage') ? { label: p.required ? '取消全站必讀' : '設為全站必讀', icon: 'flag', run: async () => { await api.post(`/api/papers/${pid}/required`, { required: !p.required }); p = await api.get(`/api/papers/${pid}`); drawHead(); refreshNav(); toast(p.required ? '已設為全站必讀，大家會收到通知' : '已取消'); } } : null,
        can('meeting') ? { label: '排進組會…', icon: 'calendar', run: () => scheduleDialog(p, async () => { p = await api.get(`/api/papers/${pid}`); drawHead(); refreshNav(); }) } : null,
        can('ai') && S.site.ai?.enabled ? { label: '問這篇（AI）', icon: 'spark', run: () => askDialog(p) } : null,
        { label: '產生報告投影片（.pptx）', icon: 'slides', run: () => slidesDialog({ paper: p }) },
        { label: '比較參數…', icon: 'table', run: () => go(`#/params?ids=${pid}`) },
        '-',
        'serviceWorker' in navigator ? { label: isOfflineSaved(pid) ? '✓ 已離線保存（再按一次取消）' : '離線保存（沒網路也能讀）', icon: 'offline', run: () => offlineSave(p, fileId) } : null,
        fileId ? { label: '下載帶劃線與筆記的 PDF', icon: 'download', run: () => { location.href = `/api/files/${fileId}/annotated`; } } : null,
        { label: '匯出筆記（Markdown）', icon: 'download', run: () => { location.href = `/api/papers/${pid}/notes.md`; } },
        can('delete', p) ? '-' : null,
        can('delete', p) ? { label: '刪除這篇（檔案移到回收區）', icon: 'trash', danger: true, run: async () => {
          if (await confirmBox(`確定刪除「${p.title}」？檔案會移到 NAS 的 trash 資料夾，不會直接消失。`, '刪除')) {
            await api.del(`/api/papers/${pid}`); toast('已刪除'); await refreshNav(); if (desk) desk.onClose(); else location.hash = '#/';
          } } } : null,
      ]) }, icon('more')));
  }
  drawHead();

  // ---------- 閱讀器
  const files = p.files;
  let fileId = +(params.get('file') || 0) || files.find((f) => f.role === 'main')?.id || files[0]?.id;
  const pageBox = h('input', { class: 'pagebox', inputmode: 'numeric', 'aria-label': '頁碼', onchange: (e) => viewer.goTo(+e.target.value || 1) });
  const pageTotal = h('span', { class: 'muted' });
  const fileSel = files.length > 1 ? h('select', { 'aria-label': '檔案', onchange: (e) => openFile(+e.target.value) },
    files.map((f) => h('option', { value: f.id, selected: f.id === fileId }, `${ROLE[f.role]}${f.label ? `（${f.label}）` : ''}`))) : null;
  const vroot = h('div', { class: 'pv' });
  reader.replaceChildren(
    h('div', { class: 'reader-bar' }, fileSel,
      h('span', { class: 'pagepos' }, pageBox, '／', pageTotal),
      h('span', { class: 'grow' }),
      can('annotate') && files.length ? h('button', { class: 'icon-btn ink-btn', 'aria-label': '手寫', title: '在論文上手寫、畫記（多種顏色、螢光筆、橡皮擦）', onclick: () => (viewer.inking ? stopInk() : startInk()) }, icon('draw')) : null,
      can('annotate') ? h('button', { class: `btn small nb-btn ${notebookOpenFor(pid) ? 'on' : ''}`, title: '筆記頁：在旁邊開一本空白筆記手寫（單篇時自動並排；已並排時開在另一側）', onclick: () => toggleNotebook(pid) }, icon('book'), h('span', { class: 'hide-narrow' }, '筆記頁')) : null,
      can('annotate') && files.length ? h('button', { class: 'icon-btn crop-btn', 'aria-label': '框選圖或公式存成圖卡', title: '框選圖、公式或表格，存成圖卡', onclick: () => startCrop() }, icon('crop')) : null,
      h('button', { class: 'icon-btn', 'aria-label': '縮小', onclick: () => viewer.zoom(1 / 1.15) }, icon('minus')),
      h('button', { class: 'icon-btn', 'aria-label': '符合寬度', onclick: () => viewer.fitWidth() }, icon('fit')),
      h('button', { class: 'icon-btn', 'aria-label': '放大', onclick: () => viewer.zoom(1.15) }, icon('plus')),
      h('a', { class: 'icon-btn hide-mobile', 'aria-label': '開新分頁', target: '_blank', href: fileId ? `/api/files/${fileId}/content` : '#' }, icon('external'))),
    files.length ? vroot : h('div', { class: 'empty' }, h('p', {}, '這篇還沒有 PDF。到「檔案」分頁上傳。')));
  const viewer = new Viewer(vroot, {
    canAnnotate: can('annotate'),
    onTranslate: can('translate') ? (sel) => showTranslation(sel) : null,
    onPage: (n) => { pageBox.value = n; saveLastPage(pid, n); },
    onSelect: async (sel) => {
      try {
        const a = await api.post(`/api/papers/${pid}/annotations`, { private: !annGlobal(), file_id: fileId, page: sel.page, kind: 'highlight', color: sel.color, quote: sel.quote, rects: sel.rects });
        anns.push(a); viewer.setAnnotations(annsFor(fileId)); drawTabs();
        toast(`已劃線（第 ${sel.page} 頁）`);
        if (sel.withNote) { switchTab('ann', 'keep'); editAnn(a.id); }
        else if (tabState.t === 'ann') drawPanel();
      } catch (e) { fail(e); }
    },
    onAnnClick: (id) => { switchTab('ann'); focusAnnCard(id); },
  });

  // ---------- 翻譯（選取文字 → 翻譯卡）
  const trBox = h('div', { class: 'trcard', hidden: true });
  reader.append(trBox);
  async function showTranslation(sel) {
    if (!S.site.translate.enabled) {
      trBox.hidden = false;
      trBox.replaceChildren(h('div', { class: 'tr-head' }, h('b', {}, '翻譯'), h('span', { class: 'grow' }), h('button', { class: 'icon-btn sm', 'aria-label': '關閉', onclick: () => { trBox.hidden = true; } }, icon('x'))),
        h('p', {}, isAdmin() ? '還沒有設定翻譯服務。' : '管理員還沒有設定翻譯服務。'), isAdmin() ? h('a', { class: 'btn small primary', href: '#/admin/settings' }, '前往設定') : null);
      return;
    }
    const out = h('div', { class: 'tr-out' }, h('div', { class: 'spinner sm' }), ' 翻譯中…');
    const src = h('details', { class: 'tr-src' }, h('summary', {}, sel.page ? `原文（第 ${sel.page} 頁）` : '原文'), h('p', {}, sel.quote));
    const actions = h('div', { class: 'row wrap' });
    trBox.hidden = false;
    trBox.replaceChildren(h('div', { class: 'tr-head' }, icon('globe'), h('b', {}, '翻譯'), h('span', { class: 'grow' }),
      h('button', { class: 'icon-btn sm', 'aria-label': '關閉', onclick: () => { trBox.hidden = true; } }, icon('x'))), out, src, actions);
    try {
      const r = await api.post('/api/translate', { text: sel.raw || sel.quote });
      out.replaceChildren(r.text);
      actions.replaceChildren(
        h('button', { class: 'btn small', onclick: async () => { await navigator.clipboard.writeText(r.text); toast('已複製譯文'); } }, icon('copy'), '複製'),
        can('annotate') ? h('button', { class: 'btn small primary', onclick: async (e) => {
          if (sel.noSave) {  // 摘要：存成整篇筆記
            try { const a = await api.post(`/api/papers/${pid}/annotations`, { private: !annGlobal(), file_id: null, page: null, kind: 'note', color: 'blue', quote: '', body: `【摘要譯文】${r.text}` }); anns.push(a); drawTabs(); toast('已存成整篇筆記'); trBox.hidden = true; } catch (err) { fail(err); }
            return;
          }
          e.currentTarget.disabled = true;
          try {
            const a = await api.post(`/api/papers/${pid}/annotations`, { private: !annGlobal(), file_id: fileId, page: sel.page, kind: 'highlight', color: 'blue', quote: sel.quote, rects: sel.rects, body: `【譯】${r.text}` });
            anns.push(a); viewer.setAnnotations(annsFor(fileId)); drawTabs(); if (tabState.t === 'ann') drawPanel();
            toast('已劃線並存成筆記'); trBox.hidden = true;
          } catch (err) { fail(err); e.target.closest('button').disabled = false; }
        } }, icon('pen'), sel.noSave ? '存成整篇筆記' : '劃線並存譯文') : null,
        h('span', { class: 'grow' }), h('span', { class: 'muted small' }, r.cached ? '（快取）' : ''));
    } catch (e) { out.replaceChildren(h('span', { class: 'err' }, e.message)); }
  }
  const forceShow = new Set(params.get('ann') ? [+params.get('ann')] : []);   // 從通知點進來的那則標註，個人模式也要顯示
  const visAnns = () => annGlobal() ? anns : anns.filter((a) => a.author_id === S.user.id || forceShow.has(a.id));
  const annsFor = (fid) => visAnns().filter((a) => !a.nb && (!a.file_id || a.file_id === fid));
  // ---------- 框選圖卡
  const cropHint = h('div', { class: 'crop-hint', hidden: true }, icon('crop'), '在頁面上拖出要存的範圍（Esc 取消）',
    h('button', { class: 'btn small', onclick: () => { viewer.stopCrop(); cropHint.hidden = true; reader.querySelector('.crop-btn')?.classList.remove('on'); } }, '取消'));
  reader.append(cropHint);
  function startCrop() {
    if (!fileId) return toast('這篇還沒有 PDF');
    if (narrow() && root.dataset.m !== 'read') { root.dataset.m = 'read'; drawTabs(); }
    if (viewer.cropping) { viewer.stopCrop(); cropHint.hidden = true; reader.querySelector('.crop-btn')?.classList.remove('on'); return; }
    cropHint.hidden = false;
    reader.querySelector('.crop-btn')?.classList.add('on');
    viewer.startCrop((sel) => {
      cropHint.hidden = true; reader.querySelector('.crop-btn')?.classList.remove('on');
      saveCrop(viewer, pid, fileId, sel, () => { p.n_figures = (p.n_figures || 0) + 1; drawTabs(); if (tabState.t === 'figs') drawPanel(); });
    }, () => { cropHint.hidden = true; reader.querySelector('.crop-btn')?.classList.remove('on'); });
  }
  // ---------- 手寫
  const INK_COLORS = [['#111111', '黑'], ['#e11d48', '紅'], ['#2563eb', '藍'], ['#16a34a', '綠'], ['#ea580c', '橘'], ['#7c3aed', '紫'], ['#facc15', '黃']];
  const INK_W = [[0.0015, '細'], [0.003, '中'], [0.006, '粗']];
  let inkPref = { tool: 'pen', color: '#e11d48', width: 0.003, penOnly: false };
  try { inkPref = { ...inkPref, ...JSON.parse(localStorage.getItem('pl-ink') || '{}') }; } catch { /* */ }
  const saveInkPref = () => { try { localStorage.setItem('pl-ink', JSON.stringify(inkPref)); } catch { /* */ } };
  const inkBar = h('div', { class: 'ink-bar', hidden: true, role: 'toolbar', 'aria-label': '手寫工具' });
  reader.append(inkBar);
  const inkEd = new InkEditor({ pid, nb: 0, anns: () => anns, fileId: () => fileId,
    // 存檔後只更新筆跡、分頁數字與「復原」按鈕；標註清單延後重畫並保留捲動位置，畫面不會跳
    refresh: () => {
      viewer.setAnnotations(annsFor(fileId)); drawTabs();
      const u = inkBar.querySelector('[aria-label="復原"]'); if (u) u.disabled = !inkEd.undo.length;
      if (tabState.t === 'ann') redrawAnnSoon();
    } });
  const redrawAnnSoon = debounce(() => {
    if (tabState.t !== 'ann') return;
    const keep = [panel, panelBody].map((x) => x && x.scrollTop);
    drawPanel();
    [panel, panelBody].forEach((x, i) => { if (x) x.scrollTop = keep[i]; });
  }, 800);
  const canEraseAnn = (a) => a.author_id === S.user.id || isAdmin();
  function drawInkBar() {
    const t = inkPref.tool;
    inkBar.replaceChildren(
      h('div', { class: 'seg' }, [['pen', '筆', 'draw'], ['hl', '螢光筆', 'marker'], ['eraser', '橡皮擦', 'eraser']].map(([k, l, ic]) =>
        h('button', { class: t === k ? 'on' : '', title: l, onclick: () => { inkPref.tool = k; if (k === 'hl' && inkPref.color === '#e11d48') inkPref.color = '#facc15'; applyInk(); } }, icon(ic), h('span', { class: 'hide-narrow' }, l)))),
      t !== 'eraser' ? h('div', { class: 'ink-colors' }, INK_COLORS.map(([c, l]) => h('button', { class: `ink-dot ${inkPref.color === c ? 'on' : ''}`, title: l, 'aria-label': l, style: { background: c }, onclick: () => { inkPref.color = c; applyInk(); } }))) : h('span', { class: 'muted small' }, '劃過筆跡就擦掉'),
      t !== 'eraser' ? h('div', { class: 'seg' }, INK_W.map(([w, l]) => h('button', { class: inkPref.width === w ? 'on' : '', onclick: () => { inkPref.width = w; applyInk(); } }, l))) : null,
      h('button', { class: 'icon-btn sm', title: '復原上一筆', 'aria-label': '復原', disabled: !inkEd.undo.length, onclick: () => inkEd.undoLast() }, icon('undo')),
      matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0 ? h('label', { class: 'check small', title: '開啟後只有觸控筆（Apple Pencil 等）會畫，手指照常捲動與縮放' },
        h('input', { type: 'checkbox', checked: inkPref.penOnly, onchange: (e) => { inkPref.penOnly = e.target.checked; applyInk(); } }), '只用觸控筆') : null,
      annGlobalSwitch(),
      inkStatusBadge(h),
      h('button', { class: 'btn small primary', onclick: () => stopInk() }, '完成'));
  }
  function applyInk() {
    saveInkPref();
    viewer.startInk({ ...inkPref, canErase: canEraseAnn, onStroke: (page, st) => inkEd.stroke(page, st), onErase: (changed) => inkEd.erase(changed) });
    drawInkBar();
  }
  function startInk() {
    if (!fileId) return toast('這篇還沒有PDF');
    if (narrow() && root.dataset.m !== 'read') { root.dataset.m = 'read'; drawTabs(); }
    cropHint.hidden = true; reader.querySelector('.crop-btn')?.classList.remove('on');
    inkEd.reset();
    inkBar.hidden = false;
    reader.querySelector('.ink-btn')?.classList.add('on');
    applyInk();
  }
  function stopInk() {
    if (!viewer || !viewer.inking) return;
    viewer.stopInk();
    inkBar.hidden = true;
    reader.querySelector('.ink-btn')?.classList.remove('on');
    if (Object.keys(inkEd.session).length) { toast('手寫已儲存（在「標註」分頁可以加文字、設為私人或刪除）'); if (tabState.t === 'ann') drawPanel(); }
    inkEd.reset();
  }
  const jumpFig = (f) => {
    const go2 = () => { if (narrow()) { root.dataset.m = 'read'; drawTabs(); } setTimeout(() => viewer.flashRect(f.page, f.rect), 60); };
    if (f.file_id && f.file_id !== fileId) { if (fileSel) fileSel.value = f.file_id; openFile(f.file_id).then(() => setTimeout(go2, 300)); } else go2();
  };
  let opened = false;
  const firstFile = fileId;
  async function openFile(fid) {
    fileId = fid;
    stopInk();
    reader.querySelector('a[aria-label="開新分頁"]').href = `/api/files/${fid}/content`;
    try {
      const n = await viewer.open(`/api/files/${fid}/content`);
      pageTotal.textContent = n; pageBox.value = 1;
      rememberPdf(fid);
      viewer.setAnnotations(annsFor(fid));
      let pg = +(params.get('page') || 0);
      if (!pg && !opened && fid === firstFile) {
        const last = lastPage(pid);
        if (last > 1 && last <= n) { pg = last; if (!desk) toast(`從上次讀到的第 ${last} 頁繼續`); }
      }
      opened = true;
      if (pg) setTimeout(() => viewer.goTo(pg), 50);
      const fg = +(params.get('fig') || 0);
      if (fg) api.get(`/api/figures?paper=${pid}&limit=300`).then((r) => { const f = r.items.find((x) => x.id === fg); if (f) setTimeout(() => viewer.flashRect(f.page, f.rect), 400); }).catch(() => {});
    } catch (e) { vroot.replaceChildren(h('div', { class: 'empty' }, h('p', {}, `無法開啟 PDF：${e.message}`), h('a', { class: 'btn', href: `/api/files/${fid}/content`, target: '_blank' }, '直接下載'))); }
  }
  if (fileId) openFile(fileId);

  // ---------- 右側分頁
  const tabState = { t: ['key', 'ann', 'figs', 'links', 'info', 'files'].includes(tab) ? tab : 'key' };
  const tabsEl = h('div', { class: 'tabs', role: 'tablist' });
  const panelBody = h('div', { class: 'panel-body' });
  panel.replaceChildren(tabsEl, panelBody);
  const TABS = () => [['key', '重點', null], ['ann', '標註', visAnns().length], ['figs', '圖卡', p.n_figures || null], ['links', '關聯', p.links_out.length + p.links_in.length], ['info', '書目', null], ['files', '檔案', p.files.length]];
  function drawTabs() {
    tabsEl.replaceChildren(...TABS().map(([k, label, n]) => h('button', { role: 'tab', class: `tab ${tabState.t === k ? 'on' : ''}`, onclick: () => switchTab(k) }, label, n ? h('span', { class: 'count' }, n) : null)));
    mtab.replaceChildren(h('button', { class: root.dataset.m === 'read' ? 'on' : '', onclick: () => { root.dataset.m = 'read'; drawTabs(); } }, icon('book'), '閱讀'),
      ...TABS().map(([k, label, n]) => h('button', { class: root.dataset.m === k ? 'on' : '', onclick: () => switchTab(k) }, icon({ key: 'star', ann: 'pen', figs: 'image', links: 'link', info: 'info', files: 'file' }[k]), label, n ? h('small', {}, n) : null)));
  }
  function switchTab(k, mobile = 'panel') {
    tabState.t = k;
    if (root.classList.contains('no-panel')) { root.classList.remove('no-panel'); drawHead(); setTimeout(() => viewer.refit(), 60); }
    if (narrow() && mobile === 'panel') root.dataset.m = k;
    drawTabs(); drawPanel();
  }
  function drawPanel() {
    ({ key: panelKey, ann: panelAnn, figs: panelFigs, links: panelLinks, info: panelInfo, files: panelFiles })[tabState.t]();
  }
  function panelFigs() {
    panelBody.replaceChildren(h('div', { class: 'spinner' }));
    figuresPanel(panelBody, p, { jump: jumpFig, startCrop }).then((n) => { if (n !== (p.n_figures || 0)) { p.n_figures = n; drawTabs(); } }).catch(fail);
  }

  // ----- 重點
  function panelKey() {
    const ki = { ...p.keyinfo };
    const keys = [...Object.keys(ki), ...KEYINFO_DEFAULT.filter((k) => !(k in ki))];
    const saved = h('span', { class: 'muted small' });
    const save = debounce(async () => {
      const obj = {};
      panelBody.querySelectorAll('.kv').forEach((row) => {
        const k = row.querySelector('.kv-k').value.trim(), v = row.querySelector('.kv-v').value;
        if (k && v.trim()) obj[k] = v;
      });
      try { p = await api.patch(`/api/papers/${pid}`, { keyinfo: obj }); saved.textContent = '已儲存'; setTimeout(() => (saved.textContent = ''), 1500); } catch (e) { fail(e); }
    }, 700);
    const ro = !can('edit_meta', p);
    const aiKeys = new Set(p.ai_keys || []);
    const row = (k, v) => h('div', { class: 'kv' },
      h('div', { class: 'kv-head' }, h('input', { class: 'kv-k', value: k, 'aria-label': '欄位名稱', oninput: save, readonly: ro }),
        aiKeys.has(k) && v ? h('span', { class: 'pill ai', title: 'AI 批次填寫，尚未有人修改；請對照原文確認' }, 'AI') : null),
      h('textarea', { class: 'kv-v', rows: 2, placeholder: '（空白）', readonly: ro, oninput: (e) => { autoGrow(e.target); save(); } }, v || ''));
    const list = h('div', { class: 'kvs' }, (ro ? keys.filter((k) => ki[k]) : keys).map((k) => row(k, ki[k])));
    panelBody.replaceChildren(
      ...(p.paths || []).filter((pt) => pt.goal).map((pt) => h('a', { class: 'notice path', href: `#/paths/${pt.id}` }, icon('route'),
        h('div', { class: 'grow small' }, h('b', {}, `入門路徑「${pt.title}」第 ${pt.step}/${pt.total} 步`), h('div', {}, `讀這篇要看懂：${pt.goal}`)))),
      h('div', { class: 'sub first' }, h('h3', {}, icon('folder'), '分類', h('small', { class: 'muted' }, ' 架設類型，決定論文放在哪一群')), catEditor()),
      h('div', { class: 'sub' }, h('h3', {}, icon('tag'), '標籤', h('small', { class: 'muted' }, ' 跨分類的自由註記：書單、專案、進度')), tagEditor()),
      h('div', { class: 'sub' }, h('h3', {}, icon('star'), '重點欄',
        !ro && can('ai') && S.site.ai?.enabled ? h('button', { class: 'btn small ghost h3-act', onclick: () => aiKeyinfoDialog(p, async (next) => { p = await api.patch(`/api/papers/${pid}`, { keyinfo: next }); panelKey(); }) }, icon('spark'), 'AI 預填') : null,
        can('ai') && S.site.ai?.enabled ? h('button', { class: 'btn small ghost', onclick: () => askDialog(p) }, '問這篇') : null),
        h('p', { class: 'hint' }, ro ? '你的帳號只能檢視重點欄。' : '這篇論文的重要資訊，全實驗室共用；打字後自動儲存。欄位名稱也可以改。', saved),
        ro && !list.children.length ? h('p', { class: 'muted small' }, '（還沒有人填寫）') : list,
        ro ? null : h('button', { class: 'btn small', onclick: () => { const r = row('', ''); list.append(r); r.querySelector('.kv-k').focus(); } }, icon('plus'), '新增欄位')),
      paramsBlock(p));
    panelBody.querySelectorAll('textarea').forEach(autoGrow);
  }
  function catEditor() {
    const wrap = h('div', { class: 'stack' });
    const chosen = new Set(p.categories);
    if (!can('categorize', p)) {
      wrap.append(p.categories.length ? h('div', { class: 'chips wrap' }, p.categories.map((id) => S.catById[id]).filter(Boolean).map((c) =>
        h('a', { class: 'chip', href: `#/c/${c.id}`, style: { '--c': c.color } }, h('span', { class: 'dot', style: { background: c.color } }), c.name)))
        : h('p', { class: 'muted small' }, '未歸檔'));
      return wrap;
    }
    const saveCats = async () => { p = await api.patch(`/api/papers/${pid}`, { categories: [...chosen] }); toast(chosen.size ? '已更新分類' : '已移回未歸檔待讀'); refreshNav(); };
    wrap.append(h('div', { class: 'chips wrap' }, S.cats.map((c) => h('button', {
      class: `chip toggle ${chosen.has(c.id) ? 'on' : ''}`, style: { '--c': c.color },
      onclick: (e) => { chosen.has(c.id) ? chosen.delete(c.id) : chosen.add(c.id); e.currentTarget.classList.toggle('on'); saveCats(); },
    }, h('span', { class: 'dot', style: { background: c.color } }), c.name))));
    if (!p.categories.length && p.suggested_category_id && S.catById[p.suggested_category_id]) {
      wrap.append(h('p', { class: 'hint' }, `建議分類：${S.catById[p.suggested_category_id].name}`));
    }
    return wrap;
  }
  function tagEditor() {
    if (!can('tag', p)) return p.tags.length ? h('div', { class: 'chips wrap' }, p.tags.map((t) => tagChip(t))) : h('p', { class: 'muted small' }, '沒有標籤');
    return tagInput(p.tags, async (tags) => { try { p = await api.patch(`/api/papers/${pid}`, { tags }); await refreshNav(); } catch (e) { fail(e); } });
  }

  // ----- 標註
  let annFilter = 'all';
  function panelAnn() {
    const mine = (a) => a.author_id === S.user.id;
    const g = annGlobal();
    const list = visAnns().filter((a) => !g || annFilter === 'all' || mine(a)).sort((a, b) => (a.page ?? 1e9) - (b.page ?? 1e9) || a.id - b.id);
    const others = anns.filter((a) => !mine(a)).length;
    panelBody.replaceChildren(
      h('div', { class: 'row wrap' },
        annGlobalSwitch(),
        g ? h('div', { class: 'seg' }, ['all', 'mine'].map((k) => h('button', { class: annFilter === k ? 'on' : '', onclick: () => { annFilter = k; panelAnn(); } }, k === 'all' ? '全部' : '我的'))) : null,
        h('span', { class: 'grow' }),
        can('annotate') ? h('button', { class: 'btn small', onclick: () => addNote(viewer.current || 1) }, icon('plus'), `第 ${viewer.current || 1} 頁筆記`) : null,
        can('annotate') ? h('button', { class: 'btn small ghost', onclick: () => addNote(null) }, '整篇筆記') : null),
      h('p', { class: 'hint ann-scope-hint' }, g ? '全域筆記：顯示所有人的公開標註；你新增的標註、手寫會公開（可在「⋯」改成私人）。'
        : `個人模式：只顯示你自己的標註；新增的標註、手寫只有你看得到。${others ? `其他人有 ${others} 則公開標註，打開「全域筆記」就看得到。` : ''}`),
      h('p', { class: 'hint' }, can('annotate') ? '在左側 PDF 選取文字即可劃線' : '你的帳號只能檢視標註', can('translate') ? '，也可以按「翻譯」。' : '。', '顏色：', Object.values(COLORS).map((c) => h('span', { class: 'legend', style: { '--c': c.css } }, c.name))),
      list.length ? h('div', { class: 'anns' }, list.map(annCard)) : h('div', { class: 'empty small' }, h('p', {}, '還沒有標註。')));
  }
  function annCard(a) {
    const c = COLORS[a.color] || COLORS.yellow;
    return h('div', { class: 'ann', id: `ann-${a.id}`, style: { '--c': c.css } },
      h('div', { class: 'ann-top' },
        a.nb ? h('button', { class: 'ann-page nb', title: '打開筆記頁', onclick: () => toggleNotebook(pid, { open: true, page: a.page }) }, `筆記頁 ${a.page}`)
          : h('button', { class: 'ann-page', onclick: () => { if (!a.page) return; if (narrow()) { root.dataset.m = 'read'; drawTabs(); } setTimeout(() => viewer.focusAnn(a.id), 30); } }, a.page ? `p.${a.page}` : '整篇'),
        h('span', { class: 'muted small' }, `${a.who || '已刪除的帳號'} · ${fmtDate(a.updated_at)}`, a.private ? ' · 私人' : ''),
        h('span', { class: 'grow' }),
        can('annotate') && (a.author_id === S.user.id || isAdmin()) ? h('button', { class: 'icon-btn sm', 'aria-label': '更多', onclick: (e) => isTemp(a) ? toast('這則手寫還在上傳，完成後就能編輯') : menu(e.currentTarget, [
          { label: '編輯筆記', icon: 'pen', run: () => editAnn(a.id) },
          ...(a.kind === 'ink' ? [] : Object.entries(COLORS)).map(([k, v]) => ({ label: `改成「${v.name}」`, run: async () => { await api.patch(`/api/annotations/${a.id}`, { color: k }); a.color = k; viewer.setAnnotations(annsFor(fileId)); panelAnn(); } })),
          { label: a.private ? '改為公開' : '改為私人（只有自己看得到）', icon: 'lock', run: async () => { await api.patch(`/api/annotations/${a.id}`, { private: !a.private }); a.private = a.private ? 0 : 1; panelAnn(); } },
          { label: '刪除', icon: 'trash', danger: true, run: async () => {
            if (a.kind === 'ink') pushInk({ op: 'delete', uid: uidOf(a), pid }); else await api.del(`/api/annotations/${a.id}`);
            anns = anns.filter((x) => x !== a); viewer.setAnnotations(annsFor(fileId)); drawTabs(); panelAnn(); window.dispatchEvent(new CustomEvent('pl-nb-changed', { detail: { pid } })); } },
        ]) }, icon('more')) : null),
      a.kind === 'ink' ? h('button', { class: 'ink-card', title: a.nb ? '打開筆記頁' : '跳到這頁的手寫', onclick: () => { if (a.nb) return toggleNotebook(pid, { open: true, page: a.page }); if (narrow()) { root.dataset.m = 'read'; drawTabs(); } setTimeout(() => viewer.focusAnn(a.id), 30); } },
        inkPreview(a.ink, 150), h('span', { class: 'muted small' }, `${a.nb ? '筆記頁手寫' : '手寫'} · ${a.ink?.strokes?.length || 0} 筆${isTemp(a) ? ' · 上傳中' : ''}`)) : null,
      a.quote ? h('blockquote', {}, a.quote) : null,
      a.kind === 'ink' && !a.body ? null : h('div', { class: 'ann-body', html: a.body ? esc(a.body).replace(/@(\S+)/g, '<span class="at">@$1</span>') : `<span class="muted">${a.quote ? '（沒有筆記）' : ''}</span>` }),
      (a.replies || []).length || can('annotate') ? h('details', { class: 'thread', open: (a.replies || []).length > 0 || null },
        h('summary', {}, (a.replies || []).length ? `討論 ${a.replies.length}` : '回覆'),
        repliesBlock(a, async () => { anns = await api.get(`/api/papers/${pid}/annotations`); panelAnn(); focusAnnCard(a.id); })) : null);
  }
  function focusAnnCard(id) {
    setTimeout(() => {
      const el = document.getElementById(`ann-${id}`);
      if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1400); }
      viewer.focusAnn(id);
    }, 60);
  }
  async function addNote(page) {
    try {
      const a = await api.post(`/api/papers/${pid}/annotations`, { private: !annGlobal(), file_id: fileId, page, kind: 'note', color: 'yellow', body: '' });
      anns.push(a); drawTabs(); viewer.setAnnotations(annsFor(fileId)); panelAnn(); editAnn(a.id);
    } catch (e) { fail(e); }
  }
  function editAnn(id) {
    const a = anns.find((x) => x.id === id);
    if (!a) return;
    const ta = h('textarea', { rows: 6, placeholder: '寫下重點、疑問、與我們實驗的關係…（用 @名字 提及別人）' }, a.body || '');
    setTimeout(() => mentionAssist(ta), 0);
    const priv = h('input', { type: 'checkbox', checked: !!a.private });
    const m = modal(a.page ? `第 ${a.page} 頁的筆記` : '整篇筆記', h('div', { class: 'stack' },
      a.quote ? h('blockquote', {}, a.quote) : null, ta,
      h('label', { class: 'check' }, priv, '私人（只有自己看得到）'),
      h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
        await api.patch(`/api/annotations/${id}`, { body: ta.value, private: priv.checked });
        a.body = ta.value; a.private = priv.checked ? 1 : 0; m.close(); if (tabState.t === 'ann') panelAnn(); toast('已儲存');
      } }, '儲存'))));
    setTimeout(() => ta.focus(), 50);
  }

  // ----- 關聯
  function panelLinks() {
    const item = (l, dir) => h('div', { class: 'link-item' },
      h('span', { class: 'rel' }, dir === 'out' ? `這篇 ${l.rel} →` : `← ${l.rel} 這篇`, l.auto ? h('span', { class: 'pill auto', title: l.note }, '自動') : null),
      h('a', { href: `#/p/${l.other_id}` }, l.title, l.year ? ` (${l.year})` : ''),
      l.note && !l.auto ? h('div', { class: 'muted small' }, l.note) : null,
      h('div', { class: 'muted small' }, l.auto ? '由參考文獻自動建立' : `${l.who || ''} · ${fmtDate(l.created_at)}`,
        can('link') && (l.auto || l.author_id === S.user.id || isAdmin()) && h('button', { class: 'linkbtn', onclick: async () => { if (await confirmBox('刪除這條關聯？', '刪除')) { try { await api.del(`/api/links/${l.id}`); p = await api.get(`/api/papers/${pid}`); drawTabs(); panelLinks(); refreshNav(); } catch (e) { fail(e); } } } }, '刪除')));
    panelBody.replaceChildren(
      h('p', { class: 'hint' }, '自己建立論文之間的依賴：例如「延伸自」「使用其方法」「實驗驗證其理論」。關係名稱可以自訂。標「自動」的是系統從參考文獻找到的引用。'),
      can('link') ? linkForm() : h('p', { class: 'muted small' }, '你的帳號只能檢視關聯。'),
      h('h3', {}, `這篇指向（${p.links_out.length}）`), ...(p.links_out.length ? p.links_out.map((l) => item(l, 'out')) : [h('p', { class: 'muted small' }, '無')]),
      h('h3', {}, `指向這篇（${p.links_in.length}）`), ...(p.links_in.length ? p.links_in.map((l) => item(l, 'in')) : [h('p', { class: 'muted small' }, '無')]),
      h('a', { class: 'btn small ghost', href: `#/graph?focus=${pid}` }, icon('graph'), '在關聯圖中查看'),
      similarBlock(pid, async () => { p = await api.get(`/api/papers/${pid}`); drawTabs(); panelLinks(); refreshNav(); }));
  }
  function linkForm() {
    let target = null, dir = 'out';
    const res = h('div', { class: 'lookup-res' });
    const q = h('input', { placeholder: '搜尋要連結的論文（標題、作者、citekey）', oninput: debounce(async (e) => {
      const v = e.target.value.trim();
      if (v.length < 2) return res.replaceChildren();
      const rows = await api.get(`/api/lookup?q=${encodeURIComponent(v)}&exclude=${pid}`);
      res.replaceChildren(...rows.map((r) => h('button', { class: 'lookup-item', onclick: () => { target = r; q.value = r.title; res.replaceChildren(); } },
        h('b', {}, r.title), h('span', { class: 'muted small' }, ` ${r.authors[0] || ''} ${r.year || ''} · ${r.citekey}`))));
    }, 250) });
    const dl = h('datalist', { id: 'rels' }, S.rels.map((r) => h('option', { value: r })));
    const rel = h('input', { list: 'rels', placeholder: '關係，例如：延伸自', value: '延伸自' });
    const note = h('input', { placeholder: '補充說明（可空白）' });
    const dirBtn = h('button', { class: 'btn small', onclick: () => { dir = dir === 'out' ? 'in' : 'out'; dirBtn.textContent = dir === 'out' ? '這篇 → 對方' : '對方 → 這篇'; } }, '這篇 → 對方');
    return h('div', { class: 'linkform' }, q, res, h('div', { class: 'row' }, dirBtn, rel, dl), note,
      h('button', { class: 'btn primary small', onclick: async () => {
        if (!target) return toast('先搜尋並點選一篇論文');
        try {
          await api.post('/api/links', dir === 'out' ? { src_id: pid, dst_id: target.id, rel: rel.value, note: note.value } : { src_id: target.id, dst_id: pid, rel: rel.value, note: note.value });
          p = await api.get(`/api/papers/${pid}`); drawTabs(); panelLinks(); toast('已建立關聯'); refreshNav();
        } catch (e) { fail(e); }
      } }, icon('link'), '建立關聯'));
  }

  // ----- 書目
  function panelInfo() {
    const ro = !can('edit_meta', p);
    const f = h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(f));
      d.authors_complete = f.querySelector('[name=authors_complete]').checked;
      try { p = await api.patch(`/api/papers/${pid}`, d); drawHead(); toast('已儲存'); } catch (err) { fail(err); }
    } },
    h('label', {}, '標題', h('textarea', { name: 'title', rows: 2 }, p.title)),
    h('label', {}, '作者（一行一位）', h('textarea', { name: 'authors', rows: 3 }, p.authors.join('\n'))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', name: 'authors_complete', checked: !!p.authors_complete }), '作者名單完整'),
    h('div', { class: 'grid2' },
      h('label', {}, '年份', h('input', { name: 'year', inputmode: 'numeric', value: p.year || '' })),
      h('label', {}, '類型', h('select', { name: 'kind' }, S.site.kinds.map((k) => h('option', { value: k, selected: p.kind === k }, KIND[k]))))),
    h('label', {}, '期刊／出處', h('input', { name: 'venue', value: p.venue })),
    h('div', { class: 'grid2' },
      h('label', {}, 'DOI', h('input', { name: 'doi', value: p.doi, autocapitalize: 'none' })),
      h('label', {}, 'arXiv', h('input', { name: 'arxiv', value: p.arxiv, autocapitalize: 'none' }))),
    h('label', {}, 'citekey', h('input', { name: 'citekey', value: p.citekey, autocapitalize: 'none' })),
    h('label', {}, h('span', { class: 'row' }, '摘要', h('span', { class: 'grow' }),
      p.abstract && can('translate') ? h('button', { class: 'linkbtn', type: 'button', onclick: (e) => { e.preventDefault(); showTranslation({ page: null, quote: p.abstract, raw: p.abstract, rects: [], noSave: true }); if (narrow()) { root.dataset.m = 'read'; drawTabs(); } } }, icon('globe'), '翻譯摘要') : null),
      h('textarea', { name: 'abstract', rows: 5 }, p.abstract)),
    ro ? h('p', { class: 'hint' }, '你的帳號只能檢視書目。') : h('div', { class: 'row wrap' },
      h('button', { class: 'btn primary', type: 'submit' }, '儲存'),
      h('button', { class: 'btn', type: 'button', onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { const r = await api.post(`/api/papers/${pid}/enrich`, {}); p = r.paper; drawHead(); panelInfo(); toast(r.changed.length ? `已補齊：${r.changed.join('、')}` : '書目已經完整'); } catch (err) { fail(err); }
        e.target.closest('button').disabled = false;
      } }, icon('refresh'), '用 DOI／arXiv 補齊')),
    h('p', { class: 'hint' }, `由 ${p.added_by_name} 於 ${fmtDate(p.added_at)} 加入`));
    if (ro) f.querySelectorAll('input, textarea, select').forEach((x) => { x.disabled = true; });
    const reloadInfo = async () => { p = await api.get(`/api/papers/${pid}`); drawHead(); panelInfo(); };
    const pubBtn = !ro && p.arxiv && (!p.doi || p.kind === 'preprint') ? h('button', { class: 'btn small ghost', onclick: async (e) => {
      e.currentTarget.disabled = true;
      try { const r = await api.post(`/api/papers/${pid}/check-published`); toast(r.msg); p = r.paper; drawHead(); panelInfo(); } catch (err) { fail(err); e.target.closest('button').disabled = false; }
    } }, icon('refresh'), '檢查是否已正式發表') : null;
    panelBody.replaceChildren(...versionNotes(p, reloadInfo), f, pubBtn);
  }

  // ----- 檔案
  function ocrLine(f) {
    const st = f.ocr_state;
    if (st === 'queued' || st === 'running') {
      const el = h('div', { class: 'small ocr-run' }, h('span', { class: 'spinner sm' }), ' OCR 辨識中…');
      setTimeout(async function poll() {
        if (!document.body.contains(el)) return;
        const q = await api.get(`/api/papers/${pid}`);
        const g = q.files.find((x) => x.id === f.id);
        if (g && !['queued', 'running'].includes(g.ocr_state)) { p = q; panelFiles(); if (g.id === fileId) openFile(fileId); toast(g.ocr_state === 'done' ? 'OCR 完成，現在可以搜尋、選字與翻譯了' : 'OCR 失敗', g.ocr_state === 'done' ? '' : 'err'); }
        else setTimeout(poll, 3000);
      }, 3000);
      return el;
    }
    if (st === 'done') return h('div', { class: 'small ok' }, icon('check'), ' 已 OCR（原始掃描檔保留在 trash）');
    if (f.has_text === 0) return h('div', { class: 'small warn' }, '掃描檔：沒有文字層，無法搜尋與選字', st === 'failed' ? '（上次 OCR 失敗）' : '',
      can('edit_meta', p) && S.site.ocr ? h('button', { class: 'btn small', onclick: async () => { try { await api.post(`/api/files/${f.id}/ocr`); p = await api.get(`/api/papers/${pid}`); panelFiles(); } catch (e) { fail(e); } } }, 'OCR 辨識文字') : null);
    return null;
  }
  function panelFiles() {
    const up = h('input', { type: 'file', accept: 'application/pdf,.pdf', hidden: true, onchange: async (e) => {
      const file = e.target.files[0]; if (!file) return;
      const fd = new FormData(); fd.append('file', file); fd.append('role', role.value); fd.append('label', '');
      try { await api.form(`/api/papers/${pid}/files`, fd); p = await api.get(`/api/papers/${pid}`); drawTabs(); panelFiles(); toast('已加入附件'); } catch (err) { fail(err); }
    } });
    const role = h('select', {}, Object.entries(ROLE).map(([k, v]) => h('option', { value: k, selected: k === 'sm' }, v)));
    panelBody.replaceChildren(
      ...p.files.map((f) => h('div', { class: 'file-item' },
        icon('file'),
        h('div', { class: 'grow' },
          h('div', {}, h('b', {}, ROLE[f.role]), f.label ? ` · ${f.label}` : ''),
          h('div', { class: 'muted small' }, `${f.pages || '?'} 頁 · ${fmtSize(f.size)} · ${f.filename}`),
          f.original_name && f.original_name !== f.filename ? h('div', { class: 'muted small' }, `原檔名：${f.original_name}`) : null,
          ocrLine(f)),
        h('button', { class: 'icon-btn sm', 'aria-label': '開啟', onclick: () => { if (fileSel) fileSel.value = f.id; openFile(f.id); if (narrow()) { root.dataset.m = 'read'; drawTabs(); } } }, icon('eye')),
        h('button', { class: 'icon-btn sm', 'aria-label': '下載', onclick: (e) => menu(e.currentTarget, [
          { label: '下載原檔', icon: 'download', run: () => { location.href = `/api/files/${f.id}/content?download=1`; } },
          { label: '下載帶劃線與筆記的 PDF', icon: 'pen', run: () => { location.href = `/api/files/${f.id}/annotated`; } },
        ]) }, icon('download')),
        can('edit_meta', p) || can('delete', p) ? h('button', { class: 'icon-btn sm', 'aria-label': '設定', onclick: (e) => menu(e.currentTarget, [
          ...(!can('edit_meta', p) ? [] : Object.entries(ROLE)).map(([k, v]) => ({ label: `設為${v}`, run: async () => { await api.patch(`/api/files/${f.id}`, { role: k }); p = await api.get(`/api/papers/${pid}`); panelFiles(); } })),
          can('edit_meta', p) && { label: '改標籤…', run: async () => { const l = prompt('檔案標籤（例如：arXiv 版、接受稿）', f.label || ''); if (l != null) { await api.patch(`/api/files/${f.id}`, { label: l }); p = await api.get(`/api/papers/${pid}`); panelFiles(); } } },
          can('delete', p) ? { label: '刪除檔案', icon: 'trash', danger: true, run: async () => { if (await confirmBox('刪除這個檔案？（移到回收區）', '刪除')) { await api.del(`/api/files/${f.id}`); p = await api.get(`/api/papers/${pid}`); drawTabs(); panelFiles(); } } } : null,
        ]) }, icon('more')) : null)),
      can('upload', p) ? h('div', { class: 'sub row wrap' }, h('span', {}, '加入附件：'), role, h('button', { class: 'btn small', onclick: () => up.click() }, icon('upload'), '選擇 PDF'), up) : null);
  }

  drawTabs();
  drawPanel();
  if (narrow() && tab) { root.dataset.m = tabState.t; drawTabs(); }
  if (params.get('ann')) setTimeout(() => { switchTab('ann', 'keep'); focusAnnCard(+params.get('ann')); }, 600);
  return { destroy: () => { window.removeEventListener('pl-panel', onPanelPref); window.removeEventListener('pl-ann-global', onAnnScope); window.removeEventListener('pl-nb-changed', onNbDebounced); window.removeEventListener('pl-desk-changed', onDeskNb); window.removeEventListener('pl-focus-ann', onFocusAnn); viewer.destroy(); }, get page() { return viewer.current || 1; }, get paper() { return p; }, refit: () => viewer.refit() };
}

function autoGrow(t) { t.style.height = 'auto'; t.style.height = `${Math.min(t.scrollHeight + 2, 420)}px`; }

function tagInput(tags, onChange) {
  const cur = [...tags];
  const box = h('div', { class: 'taginput' });
  const dl = h('datalist', { id: 'taglist' }, S.tags.map((t) => h('option', { value: t.name })));
  const inp = h('input', { list: 'taglist', placeholder: '輸入或挑選標籤，按 Enter', enterkeyhint: 'done', onkeydown: (e) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(inp.value); }
  }, onchange: () => add(inp.value) });
  function add(v) { v = v.trim(); inp.value = ''; if (v && !cur.includes(v)) { cur.push(v); draw(); onChange(cur); } }
  function draw() {
    box.replaceChildren(...cur.map((t) => tagChip(t, { onRemove: () => { cur.splice(cur.indexOf(t), 1); draw(); onChange(cur); } })), inp, dl);
  }
  draw();
  return box;
}

// ================================================================== 上傳
function openUpload() {
  const input = h('input', { type: 'file', accept: 'application/pdf,.pdf', multiple: true, hidden: true, onchange: (e) => analyze([...e.target.files]) });
  const drop = h('div', { class: 'drop', tabindex: 0, onclick: () => input.click(),
    ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); }, ondragleave: () => drop.classList.remove('over'),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); analyze([...e.dataTransfer.files].filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf')); } },
  icon('upload', 'big'), h('p', {}, h('b', {}, '點這裡選擇 PDF'), h('span', { class: 'hide-mobile' }, '，或把檔案拖進來')),
  h('p', { class: 'muted small' }, `可一次選多個檔案，單檔上限 ${S.site.max_upload_mb} MB。重複的檔案會自動略過。`));
  const area = h('div', { class: 'stack' }, drop, input);
  const m = modal('上傳論文', area, { wide: true });

  async function analyze(fileList) {
    if (!fileList.length) return;
    area.replaceChildren(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), h('p', {}, `上傳並分析 ${fileList.length} 個檔案…`)));
    const fd = new FormData();
    fileList.forEach((f) => fd.append('files', f));
    let items;
    try { items = await api.form('/api/upload', fd); } catch (e) { fail(e); area.replaceChildren(drop, input); return; }
    review(items);
  }

  function review(items) {
    const defaults = { cats: [], tags: [] };
    const rows = items.map((it) => {
      const st = { it, action: it.status !== 'ok' ? 'skip' : 'new', paper: null, role: 'main' };
      if (it.status === 'ok' && it.doi_match) { st.action = 'attach'; st.paper = { id: it.doi_match.paper_id, title: it.doi_match.title }; st.role = 'version'; }
      if (it.status === 'ok' && (it.is_supplement || it.is_peer_review) && it.title_match) { st.action = 'attach'; st.paper = { id: it.title_match.paper_id, title: it.title_match.title }; st.role = it.is_peer_review ? 'peer_review' : 'sm'; }
      return st;
    });
    const list = h('div', { class: 'up-list' });
    const catBtn = h('button', { class: 'btn small', onclick: (e) => pickCategories(e.currentTarget, defaults.cats, (ids) => { defaults.cats = ids; catBtn.textContent = ids.length ? `分類：${ids.map((i) => S.catById[i].name).join('、')}` : '分類：未歸檔待讀'; }) }, '分類：未歸檔待讀');
    function rowEl(st) {
      const it = st.it;
      if (it.status !== 'ok') {
        return h('div', { class: 'up-item muted' }, h('b', {}, it.filename), h('div', { class: 'small' },
          it.status === 'duplicate' ? ['已在論文庫：', h('a', { href: `#/p/${it.paper_id}`, onclick: () => m.close() }, it.title), '（略過）'] : it.reason));
      }
      const title = h('input', { value: it.title || '', 'aria-label': '標題', oninput: (e) => { it.title = e.target.value; } });
      const author = h('input', { value: it.author || '', placeholder: '第一作者', oninput: (e) => { it.author = e.target.value; } });
      const year = h('input', { value: it.year || '', placeholder: '年份', inputmode: 'numeric', oninput: (e) => { it.year = e.target.value; } });
      const attachBox = h('div', { class: 'row wrap' });
      const drawAttach = () => {
        attachBox.hidden = st.action !== 'attach';
        attachBox.replaceChildren(
          h('span', { class: 'small' }, '附加到：', st.paper ? h('b', {}, st.paper.title) : h('span', { class: 'muted' }, '（請搜尋論文）')),
          h('select', { onchange: (e) => { st.role = e.target.value; } }, Object.entries(ROLE).filter(([k]) => k !== 'main').map(([k, v]) => h('option', { value: k, selected: st.role === k }, v))),
          h('input', { placeholder: '搜尋論文…', oninput: debounce(async (e) => {
            const v = e.target.value.trim(); if (v.length < 2) return;
            const rs = await api.get(`/api/lookup?q=${encodeURIComponent(v)}`);
            res.replaceChildren(...rs.map((r) => h('button', { class: 'lookup-item', onclick: () => { st.paper = { id: r.id, title: r.title }; res.replaceChildren(); drawAttach(); } }, r.title, h('span', { class: 'muted small' }, ` ${r.year || ''}`))));
          }, 250) }));
        const res = h('div', { class: 'lookup-res' });
        attachBox.append(res);
      };
      drawAttach();
      const actSel = h('select', { onchange: (e) => { st.action = e.target.value; if (st.action === 'attach' && st.role === 'main') st.role = 'sm'; drawAttach(); newBox.hidden = st.action !== 'new'; } },
        h('option', { value: 'new', selected: st.action === 'new' }, '建立新論文'), h('option', { value: 'attach', selected: st.action === 'attach' }, '附加到既有論文'), h('option', { value: 'skip' }, '略過'));
      const newBox = h('div', { class: 'grid3', hidden: st.action !== 'new' }, h('label', { class: 'span3' }, '標題', title), h('label', {}, '第一作者', author), h('label', {}, '年份', year),
        h('label', {}, 'DOI', h('input', { value: it.doi || '', oninput: (e) => { it.doi = e.target.value; } })));
      const notes = [];
      if (it.doi_match) notes.push(`DOI 與「${it.doi_match.title}」相同，預設附加為其他版本。`);
      if (it.is_supplement) notes.push(it.title_match ? `看起來是「${it.title_match.title}」的補充資料。` : '看起來是補充資料，請指定要附加到哪一篇。');
      if (it.is_peer_review) notes.push('看起來是審稿意見。');
      if (!it.has_text) notes.push('這個 PDF 沒有文字層（可能是掃描檔），全文搜尋會找不到內容。');
      return h('div', { class: 'up-item' },
        h('div', { class: 'row' }, icon('file'), h('b', { class: 'grow ellipsis' }, it.filename), h('span', { class: 'muted small' }, `${it.pages} 頁`), actSel),
        notes.length ? h('div', { class: 'hint warn' }, notes.join(' ')) : null, newBox, attachBox);
    }
    list.replaceChildren(...rows.map(rowEl));
    const tagsBox = tagInput([], (t) => { defaults.tags = t; });
    area.replaceChildren(
      h('div', { class: 'up-defaults' }, h('span', { class: 'small' }, '新論文預設：'), can('categorize') ? catBtn : h('span', { class: 'muted small' }, '未歸檔待讀'), can('tag') ? tagsBox : null),
      list,
      h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => { api.post('/api/upload/commit', { items: rows.map((r) => ({ token: r.it.token, action: 'skip' })) }); m.close(); } }, '取消'),
        h('button', { class: 'btn primary', onclick: commit }, '確認入庫')));
    async function commit() {
      const payload = rows.filter((r) => r.it.token).map((r) => ({
        token: r.it.token, action: r.action === 'attach' && !r.paper ? 'skip' : r.action,
        paper_id: r.paper?.id, role: r.role, title: r.it.title, first_author: r.it.author || '', year: +r.it.year || null, doi: r.it.doi || '',
        category_ids: defaults.cats, tags: defaults.tags,
      }));
      try {
        const res = await api.post('/api/upload/commit', { items: payload });
        const made = res.filter((x) => x.status === 'created'), att = res.filter((x) => x.status === 'attached'), err = res.filter((x) => x.status === 'error');
        m.close();
        toast(`新增 ${made.length} 篇、附加 ${att.length} 個檔案${err.length ? `、失敗 ${err.length}` : ''}`, err.length ? 'err' : '');
        err.forEach((e) => toast(e.reason, 'err'));
        await refreshNav();
        if (made.length === 1) location.hash = `#/p/${made[0].paper_id}`;
        else if (made.length) location.hash = defaults.cats.length ? `#/c/${defaults.cats[0]}` : '#/inbox';
        else route();
      } catch (e) { fail(e); }
    }
  }
}

// ================================================================== 管理
async function viewAdmin(view, tab, sub = '') {
  const full = isAdmin();
  if (!full && !can('manage')) { view.replaceChildren(h('div', { class: 'empty' }, h('p', {}, '需要管理員權限'))); return; }
  const tabs = full ? [['users', '使用者與權限'], ['cats', '分類'], ['tags', '標籤'], ['settings', '網站、翻譯與 AI'], ['notify', '通知與摘要'], ['zotero', 'Zotero'], ['maint', '維護與備份'], ['log', '動態紀錄'], ['help', '使用說明']]
    : [['cats', '分類'], ['tags', '標籤'], ['help', '使用說明']];
  if (!tabs.some(([k]) => k === tab)) tab = tabs[0][0];
  view.classList.add('page');
  const body = h('div', {});
  view.replaceChildren(h('h1', {}, '管理'), h('div', { class: 'tabs flat' }, tabs.map(([k, l]) => h('a', { class: `tab ${tab === k ? 'on' : ''}`, href: `#/admin/${k}` }, l))), body);
  if (tab === 'help') return viewHelp(body, sub, '#/admin/help');

  if (tab === 'users') {
    const [users, regs, rs] = await Promise.all([api.get('/api/users'), api.get('/api/registrations'), api.get('/api/registrations/settings')]);
    const me = S.user;
    const canOwner = (t) => (rs.has_owner ? me.owner : isAdmin()) && !t.owner && !t.disabled;   // 可以把 t 設為站長
    const canUnOwner = (t) => me.owner && t.owner;
    const locked = (t) => t.owner && !me.owner;                                                 // 站長的帳號：只有站長能改
    const setOwner = async (t, on) => {
      const ok = await confirmBox(on ? `把「${t.display_name}」設為站長？\n站長擁有管理員的全部權限，負責審核註冊；設定後，只有站長可以變更站長身分。`
        : `取消「${t.display_name}」的站長身分？（他仍然是管理員）`, on ? '設為站長' : '取消站長');
      if (!ok) return;
      try { await api.patch(`/api/users/${t.id}`, { owner: on }); toast(on ? '已設為站長' : '已取消站長身分'); if (t.id === me.id) { S.site = await (await fetch('/api/site', { credentials: 'same-origin' })).json(); S.user = S.site.user; } route(); } catch (e) { fail(e); }
    };
    const regBox = rs.can_review ? h('div', { class: 'card-form stack reg-box' },
      h('div', { class: 'row wrap' }, h('h3', { class: 'grow' }, icon('user'), `註冊申請${regs.items.length ? `（${regs.items.length}）` : ''}`),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: rs.enabled, onchange: async (e) => { try { await api.put('/api/registrations/settings', { enabled: e.target.checked }); toast(e.target.checked ? '登入頁會顯示「申請註冊」' : '已關閉註冊'); } catch (x) { fail(x); } } }), '開放在登入頁申請帳號')),
      !rs.has_owner ? h('p', { class: 'hint' }, '目前還沒有站長，由管理員代為審核。在下方使用者列表按「設為站長」指定之後，就只有站長能審核與變更站長身分。') : null,
      regs.items.length ? h('table', { class: 'table' }, h('thead', {}, h('tr', {}, ['申請時間', '用戶名稱', '帳號', 'Email', '說明', ''].map((x) => h('th', {}, x)))),
        h('tbody', {}, regs.items.map((r) => {
          const role = h('select', { 'aria-label': '角色' }, h('option', { value: 'member' }, roleName('member')), h('option', { value: 'viewer' }, roleName('viewer')));
          return h('tr', {}, h('td', { class: 'small' }, fmtDate(r.created_at)), h('td', {}, r.display_name), h('td', {}, r.username), h('td', { class: 'small' }, r.email),
            h('td', { class: 'small' }, r.note || h('span', { class: 'muted' }, '—')),
            h('td', { class: 'actions' }, role,
              h('button', { class: 'btn small primary', onclick: async () => { try { await api.post(`/api/registrations/${r.id}/approve`, { role: role.value }); toast(`已開通 ${r.display_name}`); route(); refreshNav(); } catch (e) { fail(e); } } }, icon('check'), '通過'),
              h('button', { class: 'btn small ghost', onclick: async () => {
                const reason = prompt(`不通過「${r.display_name}」的申請。\n可以寫原因（會寄信告訴他；留空就不寄信）：`, '');
                if (reason === null) return;
                try { await api.post(`/api/registrations/${r.id}/reject`, { reason, notify: !!reason.trim() }); toast('已刪除這筆申請'); route(); refreshNav(); } catch (e) { fail(e); }
              } }, '不通過')));
        }))) : h('p', { class: 'muted small' }, '沒有待審核的申請。')) :
      h('div', { class: 'card-form' }, h('p', { class: 'muted small' }, '註冊申請由站長審核。'));
    const P = S.site.perm_names, RD = S.site.role_defaults;
    const roleSel = (name, val) => h('select', { name }, Object.keys(RD).map((r) => h('option', { value: r, selected: r === val }, roleName(r))));
    const f = h('form', { class: 'grid3 card-form', onsubmit: async (e) => {
      e.preventDefault();
      try { await api.post('/api/users', Object.fromEntries(new FormData(f))); toast('已新增'); route(); } catch (err) { fail(err); }
    } }, h('label', {}, '帳號', h('input', { name: 'username', required: true, autocapitalize: 'none' })),
    h('label', {}, '顯示名稱', h('input', { name: 'display_name' })),
    h('label', {}, '初始密碼', h('input', { name: 'password', required: true, minlength: 6 })),
    h('label', {}, '角色', roleSel('role', 'member')),
    h('button', { class: 'btn primary', type: 'submit' }, '新增使用者'));
    // 與角色預設不同的項目，顯示在表格裡
    const diff = (u) => Object.keys(P).filter((k) => u.role !== 'admin' && u.perms[k] !== RD[u.role][k]);
    const permDlg = (u) => {
      let role = u.role;
      const boxes = {};
      const grid = h('div', { class: 'permgrid' });
      const draw = (vals) => grid.replaceChildren(...Object.entries(P).map(([k, v]) => h('label', { class: `check ${k === 'own_only' ? 'limit' : ''}` },
        boxes[k] = h('input', { type: 'checkbox', checked: !!vals[k], disabled: role === 'admin' }), v)));
      draw(u.perms);
      const rs = h('select', { onchange: (e) => { role = e.target.value; draw(RD[role]); } }, Object.keys(RD).map((r) => h('option', { value: r, selected: r === role }, roleName(r))));
      const m = modal(`${u.display_name} 的權限`, h('div', { class: 'stack' },
        h('label', {}, '角色（切換會先套用該角色的預設，再逐項調整）', rs), grid,
        h('p', { class: 'hint' }, '管理員永遠擁有全部權限。「只能修改自己上傳的論文」是限制項：勾選後，這個帳號只能編輯、分類、加附件、刪除自己上傳的論文，其他論文只能看、劃線、翻譯。'),
        h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
          const perms = Object.fromEntries(Object.entries(boxes).map(([k, b]) => [k, b.checked]));
          try { await api.patch(`/api/users/${u.id}`, { role, perms }); m.close(); toast('已更新權限（對方重新整理頁面後生效）'); route(); } catch (e) { fail(e); }
        } }, '儲存'))));
    };
    const noEmail = users.filter((x) => !x.disabled && !(x.email || '').trim());
    const emailBox = noEmail.length ? h('div', { class: 'card-form row wrap email-remind' },
      icon('bell'), h('span', { class: 'grow' }, `${noEmail.length} 個帳號還沒填 Email：${noEmail.map((x) => x.display_name).join('、')}`),
      h('button', { class: 'btn small', onclick: async (e) => { const b = e.currentTarget; b.disabled = true; try { const r = await api.post('/api/admin/remind-email'); toast(`已通知 ${r.notified} 人（已經通知過還沒讀的不重複送）`); } catch (x) { fail(x); } b.disabled = false; } }, '提醒他們填寫')) : null;
    body.replaceChildren(
      regBox,
      emailBox,
      h('div', { class: 'card-form stack' }, h('h3', {}, icon('shield'), '角色預設'),
        h('table', { class: 'table perm-table' }, h('thead', {}, h('tr', {}, h('th', {}, ''), Object.keys(RD).map((r) => h('th', {}, roleName(r))))),
          h('tbody', {}, Object.entries(P).map(([k, v]) => h('tr', {}, h('td', {}, v), Object.keys(RD).map((r) => h('td', { class: RD[r][k] ? 'yes' : 'no' }, RD[r][k] ? '✓' : '—')))))),
        h('p', { class: 'muted small' }, '每個帳號可以在下方「權限」按鈕個別加開或收回。')),
      f,
      h('table', { class: 'table' }, h('thead', {}, h('tr', {}, ['帳號', '名稱', 'Email', '角色', '個別調整', '狀態', ''].map((x) => h('th', {}, x)))),
        h('tbody', {}, users.map((u) => h('tr', {}, h('td', {}, u.username), h('td', {}, u.display_name),
          h('td', {}, h('input', { class: 'email-in', type: 'email', value: u.email || '', placeholder: '（摘要用）', onchange: async (e) => { try { await api.patch(`/api/users/${u.id}`, { email: e.target.value }); toast('已儲存 Email'); } catch (err) { fail(err); } } })),
          h('td', {}, u.owner ? h('span', { class: 'pill owner', title: '站長：網站擁有者，負責審核註冊與指定站長' }, icon('star'), ' 站長') : roleName(u.role)),
          h('td', { class: 'small' }, diff(u).length ? diff(u).map((k) => h('span', { class: `pill ${u.perms[k] ? 'ok' : 'warn'}` }, `${u.perms[k] ? '+' : '−'}${P[k].replace(/（.*）/, '')}`)) : h('span', { class: 'muted' }, '—')),
          h('td', {}, u.disabled ? '停用' : '啟用', u.has_2fa ? h('span', { class: 'pill ok', title: '已開兩步驟驗證' }, '2FA') : null),
          h('td', { class: 'actions' },
            canOwner(u) ? h('button', { class: 'btn small', onclick: () => setOwner(u, true) }, icon('star'), '設為站長') : null,
            canUnOwner(u) ? h('button', { class: 'btn small ghost', onclick: () => setOwner(u, false) }, '取消站長') : null,
            locked(u) ? h('span', { class: 'muted small', title: '站長的帳號只有站長可以修改' }, icon('lock'), ' 只有站長能修改') : null,
            locked(u) ? null : h('button', { class: 'btn small', onclick: () => permDlg(u) }, icon('shield'), '權限'),
            locked(u) ? null : h('button', { class: 'btn small ghost', onclick: async () => { const pw = prompt(`${u.display_name} 的新密碼（至少 6 字元）`); if (pw) { try { await api.patch(`/api/users/${u.id}`, { password: pw }); toast('已重設'); } catch (e) { fail(e); } } } }, '重設密碼'),
            u.has_2fa && !locked(u) ? h('button', { class: 'btn small ghost', onclick: async () => { if (await confirmBox(`關閉 ${u.display_name} 的兩步驟驗證？（例如他換手機、驗證 App 不見了）`, '關閉')) { await api.patch(`/api/users/${u.id}`, { reset_2fa: true }); toast('已關閉'); route(); } } }, '重設 2FA') : null,
            u.id !== S.user.id && !locked(u) && !(u.owner && users.filter((x) => x.owner).length <= 1) ? h('button', { class: 'btn small ghost danger-text', onclick: () => {
              const purge = h('input', { type: 'checkbox' });
              const conf = h('input', { placeholder: u.username, autocapitalize: 'none' });
              const err = h('p', { class: 'form-err' });
              const m = modal(`刪除帳號：${u.display_name}`, h('div', { class: 'stack' },
                h('p', {}, '會一起刪除他的登入、閱讀狀態、被指派的論文、通知、入門路徑進度、筆記頁設定與私人筆記。他上傳的論文與檔案會保留。這個動作不能復原，可以先用「停用」代替。'),
                h('label', { class: 'check' }, purge, '連同他公開的標註、手寫、筆記頁與討論回覆一起刪除（不勾：保留，顯示為「已刪除的帳號」）'),
                h('label', {}, `請輸入帳號「${u.username}」確認`, conf), err,
                h('div', { class: 'row end' }, h('button', { class: 'btn danger', onclick: async () => {
                  if (conf.value.trim() !== u.username) { err.textContent = '輸入的帳號不對'; return; }
                  try { await api.del(`/api/users/${u.id}?purge=${purge.checked ? 1 : 0}`); m.close(); toast('已刪除帳號'); route(); } catch (e) { err.textContent = e.message; }
                } }, '刪除帳號'))));
            } }, '刪除') : null,
            u.id !== S.user.id && !u.owner ? h('button', { class: 'btn small ghost', onclick: async () => { try { await api.patch(`/api/users/${u.id}`, { disabled: !u.disabled }); route(); } catch (e) { fail(e); } } }, u.disabled ? '啟用' : '停用') : null))))));
  }
  if (tab === 'cats') {
    const draw = () => body.replaceChildren(
      h('p', { class: 'muted' }, '分類就是「架設類型大類」，每篇論文放在一到兩個分類裡。群組名稱決定側欄與總覽頁的分段；刪除分類不會刪除論文，沒有其他分類的論文會回到「未歸檔待讀」。跨分類的註記（書單、專案）請用「標籤」。'),
      ...S.cats.map((c, i) => h('div', { class: 'cat-edit' },
        h('input', { type: 'color', value: c.color, onchange: async (e) => { await api.patch(`/api/categories/${c.id}`, { color: e.target.value }); await refreshNav(); } }),
        h('input', { value: c.name, 'aria-label': '名稱', onchange: async (e) => { try { await api.patch(`/api/categories/${c.id}`, { name: e.target.value }); await refreshNav(); } catch (err) { fail(err); } } }),
        h('input', { value: c.grp, 'aria-label': '群組', list: 'grps', onchange: async (e) => { await api.patch(`/api/categories/${c.id}`, { grp: e.target.value }); await refreshNav(); draw(); } }),
        h('input', { class: 'grow', value: c.description, 'aria-label': '說明', onchange: async (e) => { await api.patch(`/api/categories/${c.id}`, { description: e.target.value }); await refreshNav(); } }),
        h('span', { class: 'muted small' }, `${c.count} 篇`),
        h('button', { class: 'icon-btn sm', 'aria-label': '上移', disabled: i === 0, onclick: async () => { const ids = S.cats.map((x) => x.id); [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; await api.post('/api/categories/order', { ids }); await refreshNav(); draw(); } }, icon('up')),
        h('button', { class: 'icon-btn sm', 'aria-label': '刪除', onclick: async () => { if (await confirmBox(`刪除分類「${c.name}」？論文不會被刪除。`, '刪除')) { await api.del(`/api/categories/${c.id}`); await refreshNav(); draw(); } } }, icon('trash')))),
      h('datalist', { id: 'grps' }, groups().map((g) => h('option', { value: g }))),
      h('button', { class: 'btn', onclick: async () => { const n = prompt('新分類名稱'); if (n) { try { await api.post('/api/categories', { name: n, grp: '架設類型' }); await refreshNav(); draw(); } catch (e) { fail(e); } } } }, icon('plus'), '新增分類'));
    draw();
  }
  if (tab === 'tags') {
    const draw = () => body.replaceChildren(
      h('p', { class: 'muted' }, '標籤是跨分類的自由註記（書單、專案、進度）。有「變更標籤」權限的人可以在論文上加任意標籤；這裡可以設定顏色、說明、順序。改名成已存在的標籤會合併；刪除標籤不會刪除論文。'),
      ...S.tags.map((t, i) => h('div', { class: 'cat-edit' },
        h('input', { type: 'color', value: t.color, onchange: async (e) => { await api.patch(`/api/tags/${t.id}`, { color: e.target.value }); await refreshNav(); } }),
        h('input', { value: t.name, 'aria-label': '名稱', onchange: async (e) => { try { await api.patch(`/api/tags/${t.id}`, { name: e.target.value }); await refreshNav(); draw(); } catch (err) { fail(err); } } }),
        h('input', { class: 'grow', value: t.description, placeholder: '說明（顯示在標籤頁）', 'aria-label': '說明', onchange: async (e) => { await api.patch(`/api/tags/${t.id}`, { description: e.target.value }); await refreshNav(); } }),
        h('a', { class: 'muted small', href: `#/t/${encodeURIComponent(t.name)}` }, `${t.count} 篇`),
        h('button', { class: 'icon-btn sm', 'aria-label': '上移', disabled: i === 0, onclick: async () => { const ids = S.tags.map((x) => x.id); [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]]; await api.post('/api/tags/order', { ids }); await refreshNav(); draw(); } }, icon('up')),
        h('button', { class: 'icon-btn sm', 'aria-label': '刪除', onclick: async () => { if (await confirmBox(`刪除標籤「${t.name}」？${t.count} 篇論文會移除這個標籤，論文本身不受影響。`, '刪除')) { await api.del(`/api/tags/${t.id}`); await refreshNav(); draw(); } } }, icon('trash')))),
      h('button', { class: 'btn', onclick: async () => { const n = prompt('新標籤名稱'); if (n) { try { await api.post('/api/tags', { name: n }); await refreshNav(); draw(); } catch (e) { fail(e); } } } }, icon('plus'), '新增標籤'));
    draw();
  }
  if (tab === 'settings') await adminSite(body);
  if (tab === 'notify') await adminNotify(body);
  if (tab === 'zotero') await adminZotero(body);
  if (tab === 'maint') {
    await adminMaint(body, [
      h('div', { class: 'card-form stack' }, h('h3', {}, '資料庫備份'),
        h('p', { class: 'muted' }, '把資料庫（書目、分類、標註、關聯）另存一份到 NAS 的 backups 資料夾，並下載到這台裝置。PDF 檔案請用 NAS 的快照或 Hyper Backup 備份整個資料夾。'),
        h('button', { class: 'btn primary', onclick: async () => { const r = await api.post('/api/admin/backup'); location.href = `/api/admin/backup/${r.file}`; toast('已建立備份'); } }, icon('download'), '建立並下載備份')),
      h('div', { class: 'card-form stack' }, h('h3', {}, '匯出'),
        h('div', { class: 'row wrap' }, h('a', { class: 'btn', href: '/api/export/bibtex', download: 'papers.bib' }, '全部 BibTeX'), h('a', { class: 'btn', href: '/api/export/csv' }, '全部 CSV（Excel 可開）'))),
      h('div', { class: 'card-form stack' }, h('h3', {}, '重建全文索引'),
        h('p', { class: 'muted' }, '搜尋結果怪怪的時候再用，論文多時需要幾分鐘。'),
        h('button', { class: 'btn', onclick: async (e) => { e.currentTarget.disabled = true; const r = await api.post('/api/admin/reindex'); toast(`已重建 ${r.papers} 篇`); e.target.closest('button').disabled = false; } }, '重建索引')),
    ]);
  }
  if (tab === 'log') {
    const rows = await api.get('/api/activity?limit=200');
    body.replaceChildren(h('table', { class: 'table' }, h('tbody', {}, rows.map((a) => h('tr', {}, h('td', { class: 'muted small' }, fmtDate(a.at)), h('td', {}, a.who || '系統'), h('td', {}, a.action),
      h('td', {}, a.paper_id ? h('a', { href: `#/p/${a.paper_id}` }, a.title || `#${a.paper_id}`) : ''), h('td', { class: 'muted small' }, a.detail))))));
  }
}

// ================================================================== 啟動
async function boot() {
  S.site = await (await fetch('/api/site', { credentials: 'same-origin' })).json();
  S.user = S.site.user;
  document.title = S.site.name;
  if (!S.user) return renderAuth();
  shell();
  await refreshNav();
  window.onhashchange = () => route();
  installLinkInterceptor();
  route();
  setTimeout(emailReminder, 800);
  watchEmail();
  window.addEventListener('pl-email-remind', () => emailReminder(true));
  // 管理員送出「請填 Email」通知：每則通知一定跳出一次（不管之前按過「下次再說」）
  window.addEventListener('pl-email-check', async (e) => {
    if (!S.user || S.user.has_email) return;
    let seen = ''; try { seen = localStorage.getItem('pl-email-notif') || ''; } catch { /* */ }
    const fresh = String(e.detail?.id || '') !== seen;
    if (!fresh && emailSnoozed()) return;
    try { const me = await api.get('/api/me'); S.user.has_email = !!me.email; } catch { return; }
    try { localStorage.setItem('pl-email-notif', String(e.detail?.id || '')); } catch { /* */ }
    emailReminder(fresh);
  });
}
boot();

export { mountPaper, openInDesk, go, isDeskWindow, themeButton, isDark, annGlobal, annGlobalSwitch };
export { h, $, icon, api, S, can, isAdmin, toast, fail, modal, menu, confirmBox, fmtDate, debounce, esc, autoGrow, refreshNav, route, pickCategories, tagInput, authorLine };
