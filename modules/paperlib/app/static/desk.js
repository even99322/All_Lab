// 閱讀桌：同時開好幾篇論文。
// - 電腦（Win／Mac）：論文庫視窗點論文 → 在獨立的「閱讀桌」視窗開；論文庫視窗留著繼續挑。
// - 「分頁」一次看一篇；「並排」同時看兩篇，視窗寬就左右、視窗高就上下（自動判斷）。
// - 並排有兩個固定位置（slots）：先點一側，再點上方分頁，就把那一側換成這篇；另一側不動。
//   換下來的論文不會關掉，頁數、分頁、捲動位置都保留，之後換回來接著讀。
// 閱讀桌的清單存在這台裝置的瀏覽器（localStorage），多個視窗之間用 BroadcastChannel 同步。
import { h, icon, api, S, toast, debounce, mountPaper, themeButton } from './app.js';
import { mountNotebook } from './notebook.js';

const KEY = 'pl-desk';
const MAX_OPEN = 8;
const chan = 'BroadcastChannel' in window ? new BroadcastChannel('pl-desk') : null;

export const isDeskWindow = () => window.name === 'qel-desk';
const isPhone = () => window.innerWidth < 900;

const defLayout = () => (isPhone() ? 'tabs' : 'split');   // 手機預設一次一篇，電腦預設並排
const horizontal = () => window.innerWidth >= window.innerHeight;   // 視窗寬＝左右並排；視窗高＝上下並排
function read() {
  try {
    const st = JSON.parse(localStorage.getItem(KEY) || 'null');
    if (st && Array.isArray(st.open)) return { layout: defLayout(), pages: {}, slots: [null, null], focusSlot: 0, ...st };
  } catch { /* 無痕模式等 */ }
  return { open: [], active: null, layout: defLayout(), pages: {}, slots: [null, null], focusSlot: 0 };
}
function write(st, msg = { type: 'state' }) {
  try { localStorage.setItem(KEY, JSON.stringify(st)); } catch { /* 空間不足時略過 */ }
  chan && chan.postMessage(msg);
  listeners.forEach((fn) => fn(st));
  window.dispatchEvent(new Event('pl-desk-changed'));
}
const listeners = new Set();
export function onDeskChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
chan && (chan.onmessage = () => { listeners.forEach((fn) => fn(read())); window.dispatchEvent(new Event('pl-desk-changed')); });
window.addEventListener('storage', (e) => { if (e.key === KEY) listeners.forEach((fn) => fn(read())); });

export const deskState = read;

export async function addToDesk(id, { activate = true, ann = 0 } = {}) {
  const st = read();
  if (ann) st.annFocus = { id, ann };      // 打開後捲到這則標註（例如從「我的筆記」點進來）
  let item = st.open.find((x) => x.id === id);
  if (!item) {
    let meta = { citekey: `#${id}`, title: '' };
    try { const p = await api.get(`/api/papers/${id}`); meta = { citekey: p.citekey, title: p.title }; } catch { /* 找不到就先用編號 */ }
    item = { id, ...meta };
    st.open.push(item);
    while (st.open.length > MAX_OPEN) {
      const drop = st.open.find((x) => x.id !== id && x.id !== st.active) || st.open[0];
      st.open = st.open.filter((x) => x !== drop);
      toast(`閱讀桌最多 ${MAX_OPEN} 篇，已關閉「${drop.citekey}」`);
    }
  }
  if (activate) {
    if (st.layout === 'split') {
      // 並排：新開的論文放到「另一側」，正在讀的這側不動
      const slots = st.slots || [null, null];
      if (!slots.includes(id)) {
        const f = st.focusSlot || 0;
        const empty = slots.indexOf(null);
        slots[empty >= 0 && empty !== f ? empty : 1 - f] = id;
        if (slots[f] == null) slots[f] = st.active && st.active !== id ? st.active : null;
      }
      st.slots = slots;
      if (!st.active) st.active = id;
    } else st.active = id;
  }
  write(st, { type: 'open', id });
  return st;
}

export function closeOnDesk(id) {
  const st = read();
  const i = st.open.findIndex((x) => x.id === id);
  if (i < 0) return;
  st.open.splice(i, 1);
  if (st.active === id) st.active = (st.open[i] || st.open[i - 1] || {}).id || null;
  st.slots = (st.slots || [null, null]).map((x) => (x === id ? null : x));
  delete st.pages[id];
  // 按「筆記頁」自動改成並排的，關掉筆記頁就回到單篇
  if (typeof id === 'string' && id.startsWith('n') && st.nbAuto) {
    const pid = +id.slice(1);
    st.layout = 'tabs'; st.nbAuto = false;
    if (st.open.some((x) => x.id === pid)) st.active = pid;
  }
  write(st);
}

// ------------------------------------------------------------------ 筆記頁
// 每篇論文一本空白筆記（每個人自己的）。閱讀桌上的編號是 "n<論文編號>"。
export function notebookOpenFor(pid) {
  const st = read(), nid = `n${pid}`;
  return st.layout === 'split' ? (st.slots || []).includes(nid) : st.active === nid;
}
export async function toggleNotebook(pid, { open = false, page = 0 } = {}) {
  const nid = `n${pid}`;
  let st = read();
  const inDesk = location.hash.startsWith('#/desk');
  if (!open && inDesk && notebookOpenFor(pid)) { closeOnDesk(nid); return; }
  let paper = st.open.find((x) => x.id === pid);
  let meta = paper;
  if (!meta) {
    meta = { citekey: `#${pid}`, title: '' };
    try { const p = await api.get(`/api/papers/${pid}`); meta = { citekey: p.citekey, title: p.title }; } catch { /* */ }
    st = read();
  }
  if (!st.open.some((x) => x.id === pid)) st.open.push({ id: pid, citekey: meta.citekey, title: meta.title });
  if (!st.open.some((x) => x.id === nid)) st.open.push({ id: nid, nb: pid, citekey: meta.citekey, title: `筆記頁：${meta.title || meta.citekey}` });
  while (st.open.length > MAX_OPEN) {
    const drop = st.open.find((x) => x.id !== pid && x.id !== nid && !(st.slots || []).includes(x.id)) || st.open.find((x) => x.id !== pid && x.id !== nid);
    if (!drop) break;
    st.open = st.open.filter((x) => x !== drop);
    toast(`閱讀桌最多 ${MAX_OPEN} 個，已關閉「${drop.citekey}」`);
  }
  if (page) st.pages[nid] = page;
  const slots = st.slots || [null, null];
  if (st.layout !== 'split') {
    // 單篇：改成並排，論文在一側、筆記頁在另一側
    st.layout = 'split'; st.nbAuto = true;
    st.slots = [pid, nid]; st.focusSlot = 1;
  } else if (!slots.includes(nid)) {
    // 已經並排：筆記頁開在這篇論文的另一側
    let k = slots.indexOf(pid);
    if (k < 0) { k = st.focusSlot || 0; slots[k] = pid; }
    slots[1 - k] = nid; st.slots = slots; st.focusSlot = 1 - k;
  } else st.focusSlot = slots.indexOf(nid);
  st.active = nid;
  write(st);
  if (page) window.dispatchEvent(new CustomEvent('pl-nb-goto', { detail: { pid, page } }));
  if (!inDesk) {
    if (isDeskWindow() || !wantsSeparateWindow()) location.hash = '#/desk';
    else openDeskWindow();
  }
}

// ------------------------------------------------------------------ 視窗
export function wantsSeparateWindow() {
  let v = null;
  try { v = localStorage.getItem('pl-deskwin'); } catch { /* */ }
  if (v != null) return v === '1';
  return matchMedia('(pointer: fine)').matches && screen.width >= 1024;   // 預設：電腦開、手機平板關
}
export function setSeparateWindow(on) { try { localStorage.setItem('pl-deskwin', on ? '1' : '0'); } catch { /* */ } }

function openNamed(name, hash, feat) {
  const w = window.open('', name, feat);
  if (!w) return null;
  try {
    if (w.location.href === 'about:blank') w.location.href = `/${hash}`;
    else if (hash && !w.location.hash.startsWith(hash.split('?')[0])) w.location.hash = hash;
  } catch { w.location.href = `/${hash}`; }
  w.focus();
  return w;
}

export function openDeskWindow() {
  if (!wantsSeparateWindow() || isDeskWindow()) { location.hash = '#/desk'; return; }
  const W = Math.min(screen.availWidth, 1680), H = screen.availHeight;
  const w = openNamed('qel-desk', '#/desk', `width=${W},height=${H},left=${screen.availWidth - W},top=0`);
  if (!w) { toast('瀏覽器擋下了新視窗，改在這個視窗開閱讀桌'); location.hash = '#/desk'; }
}

export async function openInDesk(id, opts = {}) {
  await addToDesk(id, opts);
  if (isDeskWindow()) return;
  openDeskWindow();
}

// 在閱讀桌視窗裡要去論文庫的其他頁：交給主視窗
export function go(hash) {
  if (!isDeskWindow()) { location.hash = hash; return; }
  if (window.opener && !window.opener.closed) {
    try { window.opener.location.hash = hash; window.opener.focus(); return; } catch { /* 跨來源時改用具名視窗 */ }
  }
  openNamed('qel-main', hash) || (location.hash = hash);
}

// 全站連結：主視窗點論文 → 閱讀桌；閱讀桌視窗裡的論文連結 → 開新一格；其他連結 → 主視窗
let installed = false;
export function installLinkInterceptor() {
  if (installed) return;
  installed = true;
  if (!isDeskWindow() && !window.name) window.name = 'qel-main';
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest('a[href^="#/"]');
    if (!a || a.target === '_blank') return;
    const href = a.getAttribute('href');
    const m = href.match(/^#\/p\/(\d+)/);
    const ann = +((href.match(/[?&]ann=(\d+)/) || [])[1] || 0);
    if (location.hash.startsWith('#/desk')) {
      e.preventDefault();
      if (m) addToDesk(+m[1], { ann });
      else if (!href.startsWith('#/desk')) go(href);
      return;
    }
    if (m && wantsSeparateWindow()) { e.preventDefault(); openInDesk(+m[1], { ann }); }
  });
}

// 頂列的「閱讀桌」按鈕（顯示開了幾篇）
export function deskButton() {
  const n = h('span', { class: 'desk-n' });
  const b = h('button', { class: 'btn desk-btn', title: '閱讀桌：同時開多篇論文', onclick: () => openDeskWindow() }, icon('stack'), h('span', { class: 'hide-mobile' }, '閱讀桌'), n);
  const upd = (st) => { n.textContent = st.open.length || ''; b.classList.toggle('has', !!st.open.length); };
  upd(read());
  onDeskChange(upd);
  return b;
}

// ------------------------------------------------------------------ 閱讀桌畫面
export async function viewDesk(view, params) {
  document.body.classList.add('desk-mode');
  view.classList.add('desk-view');
  if (params.get('add')) await addToDesk(+params.get('add'));
  const bar = h('div', { class: 'desk-bar' });
  const panesEl = h('div', { class: 'panes' });
  view.replaceChildren(bar, panesEl);
  const panes = new Map();   // id -> { el, handle, used }
  let st = read();
  let alive = true;

  const findBox = () => {
    const res = h('div', { class: 'lookup-res' });
    const q = h('input', { placeholder: '搜尋論文加入閱讀桌（標題、作者、citekey）', oninput: debounce(async (e) => {
      const v = e.target.value.trim(); if (v.length < 2) return res.replaceChildren();
      const rows = await api.get(`/api/lookup?q=${encodeURIComponent(v)}`);
      res.replaceChildren(...rows.map((r) => h('button', { class: 'lookup-item', onclick: () => { addToDesk(r.id); q.value = ''; res.replaceChildren(); document.querySelectorAll('.desk-find-pop').forEach((x) => x.remove()); } },
        h('b', {}, r.title), h('span', { class: 'muted small' }, ` ${r.year || ''} · ${r.citekey}`))));
    }, 250) });
    return h('div', { class: 'desk-find' }, q, res);
  };

  function drawBar() {
    const phone = isPhone();
    bar.replaceChildren(
      h('button', { class: 'icon-btn', title: '回論文庫', 'aria-label': '回論文庫', onclick: () => go('#/') }, icon(isDeskWindow() ? 'grid' : 'back')),
      h('div', { class: 'desk-tabs' }, st.open.map((x) => {
        const k = st.layout === 'split' ? st.slots.indexOf(x.id) : -1;
        const side = k < 0 ? '' : horizontal() ? (k ? '右' : '左') : (k ? '下' : '上');
        return h('div', { class: `desk-tab ${x.id === st.active ? 'on' : ''} ${k >= 0 ? 'in-slot' : ''}`,
          title: `${x.title}${st.layout === 'split' ? (k >= 0 ? `（在${side}側）` : `（點一下換到${horizontal() ? (st.focusSlot ? '右' : '左') : (st.focusSlot ? '下' : '上')}側）`) : ''}` },
        side ? h('span', { class: 'dt-side' }, side) : null,
        h('button', { class: `dt-name ${x.nb ? 'nb' : ''}`, onclick: () => activate(x.id) }, x.nb ? [icon('book'), ' 筆記 ', x.citekey] : x.citekey),
        h('button', { class: 'dt-x', 'aria-label': `關閉 ${x.citekey}`, onclick: () => closeOnDesk(x.id) }, '×')); })),
      h('button', { class: 'icon-btn', title: '加入論文', 'aria-label': '加入論文', onclick: (e) => {
        document.querySelectorAll('.desk-find-pop').forEach((x) => x.remove());
        const pop = h('div', { class: 'desk-find-pop menu' }, findBox());
        document.body.append(pop);
        const r = e.currentTarget.getBoundingClientRect();
        pop.style.top = `${r.bottom + 6}px`; pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 440))}px`;
        pop.querySelector('input').focus();
        setTimeout(() => document.addEventListener('click', function off(ev) { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('click', off); } }), 0);
      } }, icon('plus')),
      themeButton(),
      !phone ? h('button', { class: 'icon-btn', title: '收起／展開所有論文的右側欄', 'aria-label': '收起或展開右側欄', onclick: () => {
        let off = false; try { off = localStorage.getItem('pl-panel-off') === '1'; } catch { /* */ }
        try { localStorage.setItem('pl-panel-off', off ? '0' : '1'); } catch { /* */ }
        window.dispatchEvent(new Event('pl-panel')); drawBar();
        setTimeout(() => panes.forEach((p) => p.handle && p.handle.refit && p.handle.refit()), 80);
      } }, icon((() => { try { return localStorage.getItem('pl-panel-off') === '1'; } catch { return false; } })() ? 'panelOpen' : 'panelClose')) : null,
      h('div', { class: 'seg desk-layout' },
        h('button', { class: st.layout !== 'split' ? 'on' : '', title: '一次看一篇', onclick: () => setLayout('tabs') }, phone ? '單篇' : '分頁'),
        h('button', { class: st.layout === 'split' ? 'on' : '', title: '兩篇並排：視窗寬就左右、視窗高就上下。先點一側，再點上方分頁就換掉那一側', onclick: () => setLayout('split') },
          st.layout === 'split' ? (horizontal() ? '左右並排' : '上下並排') : '並排')));
  }
  function setLayout(l) { st.layout = l; st.nbAuto = false; if (l === 'split') st.slots = [st.active, null]; st.focusSlot = 0; normalize(); write(st); }
  // 點上方分頁：分頁模式＝切到這篇；並排＝已經在某一側就選那一側，不在就換掉目前選取的那一側
  function activate(id) {
    if (st.layout === 'split') {
      const k = st.slots.indexOf(id);
      if (k >= 0) st.focusSlot = k; else st.slots[st.focusSlot] = id;
    }
    st.active = id; write(st);
  }
  // 並排的兩個位置：整理成合法狀態（關掉的清空、空位補上其他論文、同一篇不會出現兩次）
  function normalize() {
    const ids = st.open.map((x) => x.id);
    let slots = [0, 1].map((k) => (ids.includes((st.slots || [])[k]) ? st.slots[k] : null));
    if (slots[0] != null && slots[0] === slots[1]) slots[1] = null;
    for (const k of [0, 1]) {
      if (slots[k] == null) slots[k] = (k === (st.focusSlot || 0) && st.active && !slots.includes(st.active) ? st.active : null)
        ?? ids.find((i) => !slots.includes(i)) ?? null;
    }
    st.slots = slots;
    st.focusSlot = st.focusSlot === 1 && slots[1] != null ? 1 : slots[0] != null ? 0 : 1;
    if (st.layout === 'split' && slots[st.focusSlot] != null) st.active = slots[st.focusSlot];
  }

  function visibleIds() {
    if (st.layout === 'split') return st.slots.filter((x) => x != null);
    return st.active ? [st.active] : [];
  }

  async function sync() {
    if (!alive) return;
    st = read();
    if (st.active && !st.open.some((x) => x.id === st.active)) st.active = st.open[0]?.id || null;
    if (!st.active && st.open.length) st.active = st.open[0].id;
    normalize();
    drawBar();
    const ids = st.open.map((x) => x.id);
    for (const [id, p] of panes) {           // 關掉的論文
      if (!ids.includes(id)) { try { p.handle && p.handle.destroy(); } catch { /* */ } p.el.remove(); panes.delete(id); }
    }
    const vis = visibleIds();
    const phone = isPhone();
    panesEl.className = `panes ${st.layout === 'split' && vis.length > 1 ? (horizontal() ? 'cols' : 'stack') : 'single'}`;
    panesEl.style.setProperty('--n', Math.max(1, vis.length));
    if (!st.open.length) {
      panesEl.replaceChildren(h('div', { class: 'empty desk-empty' }, icon('stack', 'big'),
        h('p', {}, '閱讀桌是空的。'),
        h('p', { class: 'muted' }, isDeskWindow() ? '在論文庫視窗點任何一篇論文，就會在這個視窗打開；可以同時開多篇並排對照。' : '在論文頁的選單按「加入閱讀桌」，或在下面搜尋。'),
        findBox()));
      return;
    }
    panesEl.querySelector('.desk-empty')?.remove();
    const maxMounted = phone ? 4 : MAX_OPEN;    // 換下來的論文保持開著（頁數、分頁都保留）；手機記憶體少，最多留 4 篇
    for (const id of ids) {
      let p = panes.get(id);
      if (!p) { p = { el: h('section', { class: 'pane', dataset: { id } }), handle: null, used: 0 }; panes.set(id, p); }
      // 已經在畫面上的論文不要重新插入：搬動 DOM 會讓捲動位置（頁數）歸零；排列順序用 CSS order
      if (p.el.parentNode !== panesEl) panesEl.append(p.el);
      const show = vis.includes(id);
      p.el.classList.toggle('hidden', !show);
      p.el.classList.toggle('focus', st.layout === 'split' && vis.length > 1 && id === st.slots[st.focusSlot]);
      p.el.style.order = String(show ? vis.indexOf(id) : 99);
      if (show) p.used = Date.now();
      if (show && !p.handle && !p.loading) {
        p.loading = true;
        p.el.replaceChildren(h('div', { class: 'empty' }, h('div', { class: 'spinner' })));
        const q = new URLSearchParams(); if (st.pages[id]) q.set('page', st.pages[id]);
        if (st.annFocus && st.annFocus.id === id) { q.set('ann', st.annFocus.ann); clearAnnFocus(); }
        try {
          const item = st.open.find((x) => x.id === id) || {};
          const dk = { onClose: () => closeOnDesk(id), onFocus: () => focusPane(id) };
          p.handle = item.nb ? await mountNotebook(p.el, item.nb, { params: q, desk: dk }) : await mountPaper(p.el, id, { params: q, desk: dk });
        } catch (e) { p.el.replaceChildren(h('div', { class: 'empty' }, h('p', {}, `無法開啟：${e.message}`), h('button', { class: 'btn', onclick: () => closeOnDesk(id) }, '關閉'))); }
        p.loading = false;
      } else if (show && p.handle) {
        requestAnimationFrame(() => p.handle.refit && p.handle.refit());
      }
    }
    // 已經開著的論文：直接捲到要看的那則標註
    if (st.annFocus && panes.get(st.annFocus.id)?.handle) {
      window.dispatchEvent(new CustomEvent('pl-focus-ann', { detail: { pid: st.annFocus.id, ann: st.annFocus.ann } }));
      clearAnnFocus();
    }
    // 手機記憶體有限：看不到的論文只保留最近幾篇，其餘先卸下（記住頁碼）
    const mounted = [...panes.entries()].filter(([, p]) => p.handle).sort((a, b) => b[1].used - a[1].used);
    mounted.slice(maxMounted).forEach(([id, p]) => {
      if (vis.includes(id)) return;
      savePage(id, p); p.handle.destroy(); p.handle = null; p.el.replaceChildren();
    });
  }
  // 點某一側的論文：只標記「目前選取這一側」，位置不變（不會上下或左右互換）
  function focusPane(id) {
    const s2 = read();
    if (s2.layout !== 'split') return;
    const k = (s2.slots || []).indexOf(id);
    if (k < 0 || (s2.focusSlot === k && s2.active === id)) return;
    s2.focusSlot = k; s2.active = id; write(s2);
  }
  function clearAnnFocus() {
    const s2 = read(); delete s2.annFocus; st.annFocus = null;
    try { localStorage.setItem(KEY, JSON.stringify(s2)); } catch { /* */ }
  }
  function savePage(id, p) {
    if (!p.handle) return;
    const s2 = read(); s2.pages[id] = p.handle.page;
    try { localStorage.setItem(KEY, JSON.stringify(s2)); } catch { /* */ }
  }
  const saveAll = () => panes.forEach((p, id) => savePage(id, p));
  const tick = setInterval(saveAll, 5000);
  const off = onDeskChange(() => sync());
  const onResize = debounce(() => sync(), 200);
  window.addEventListener('resize', onResize);
  window.addEventListener('beforeunload', saveAll);
  document.title = `閱讀桌｜${S.site.name}`;
  await sync();
  return () => {
    alive = false; saveAll(); clearInterval(tick); off();
    window.removeEventListener('resize', onResize); window.removeEventListener('beforeunload', saveAll);
    panes.forEach((p) => { try { p.handle && p.handle.destroy(); } catch { /* */ } });
    document.body.classList.remove('desk-mode'); document.title = S.site.name;
  };
}

