// 筆記頁：每篇論文一本空白筆記（每個人自己的），直接手寫；在閱讀桌裡跟論文並排。
// 筆跡存成 nb=1 的手寫標註，跟 PDF 上的手寫一樣每一筆即時儲存（離線也先存在裝置上）。
import { h, icon, api, S, toast, fail, can, annGlobal } from './app.js';
import { Viewer } from './viewer.js';
import { InkEditor, applyPending, inkStatusBadge } from './ink.js';

const INK_COLORS = [['#111111', '黑'], ['#e11d48', '紅'], ['#2563eb', '藍'], ['#16a34a', '綠'], ['#ea580c', '橘'], ['#7c3aed', '紫'], ['#facc15', '黃']];
const INK_W = [[0.0015, '細'], [0.003, '中'], [0.006, '粗']];
const BG = [['lined', '橫線'], ['grid', '方格'], ['dots', '點陣'], ['blank', '空白']];

export async function mountNotebook(el, pid, { params = new URLSearchParams(), desk = null } = {}) {
  const [nb, all] = await Promise.all([api.get(`/api/papers/${pid}/notebook`), api.get(`/api/papers/${pid}/annotations`)]);
  const mine = (a) => a.nb && a.author_id === S.user.id;
  let anns = applyPending(all, pid).filter(mine);
  let pages = Math.max(nb.pages, ...anns.map((a) => a.page || 1));
  let bg = nb.bg;
  const editable = can('annotate');

  let pref = { tool: 'pen', color: '#111111', width: 0.0015, penOnly: false };
  try { pref = { ...pref, ...JSON.parse(localStorage.getItem('pl-nb-ink') || '{}') }; } catch { /* */ }
  const savePref = () => { try { localStorage.setItem('pl-nb-ink', JSON.stringify(pref)); } catch { /* */ } };

  const head = h('div', { class: 'paper-head nb-head' });
  const tools = h('div', { class: 'ink-bar nb-tools', role: 'toolbar', 'aria-label': '筆記頁工具' });
  const vroot = h('div', { class: 'pv' });
  const root = h('div', { class: 'paper nb-view', dataset: { m: 'read' } }, head, h('div', { class: 'nb-body' }, tools, h('section', { class: 'reader nb-reader' }, vroot)));
  el.replaceChildren(root);
  if (desk) root.addEventListener('pointerdown', () => desk.onFocus && desk.onFocus(), true);

  const pageInfo = h('span', { class: 'muted small nb-pageinfo' });
  const viewer = new Viewer(vroot, { canAnnotate: false, onPage: (n) => { pageInfo.textContent = `${n} / ${viewer.numPages}`; } });
  const ed = new InkEditor({ pid, nb: 1, anns: () => anns, fileId: () => null,
    // 只更新筆跡與「復原」按鈕；不重畫工具列（重畫會改變高度，讓頁面跳動）
    refresh: () => { viewer.setAnnotations(anns); syncUndo(); window.dispatchEvent(new CustomEvent('pl-nb-changed', { detail: { pid, from: 'notebook' } })); } });
  const syncUndo = () => { const b = tools.querySelector('[aria-label="復原"]'); if (b) b.disabled = !ed.undo.length; };

  function drawHead() {
    head.replaceChildren(
      desk ? h('button', { class: 'icon-btn', 'aria-label': '關閉筆記頁', title: '關閉筆記頁', onclick: () => desk.onClose() }, icon('x')) : null,
      h('div', { class: 'ph-text' },
        h('div', { class: 'ph-title nb-title' }, icon('book'), ` 筆記頁 · ${nb.paper.citekey}`),
        h('div', { class: 'ph-meta', title: nb.paper.title }, nb.paper.title)),
      pageInfo,
      h('select', { class: 'nb-bg', 'aria-label': '頁面樣式', title: '頁面樣式', onchange: async (e) => { bg = e.target.value; viewer.setBg(bg); saveMeta(); } },
        BG.map(([k, l]) => h('option', { value: k, selected: k === bg }, l))),
      editable ? h('button', { class: 'btn small', title: '在最後面加一頁', onclick: () => addPage() }, icon('plus'), h('span', { class: 'hide-narrow' }, '新增一頁')) : null,
      h('a', { class: 'icon-btn', title: '下載成 PDF', 'aria-label': '下載成 PDF', href: `/api/papers/${pid}/notebook.pdf`, download: '' }, icon('download')));
  }
  function drawTools() {
    if (!editable) { tools.replaceChildren(h('span', { class: 'muted small' }, '你的帳號只能檢視')); return; }
    const t = pref.tool;
    tools.replaceChildren(
      h('div', { class: 'seg' }, [['pen', '筆', 'draw'], ['hl', '螢光筆', 'marker'], ['eraser', '橡皮擦', 'eraser'], ['hand', '捲動', 'hand']].map(([k, l, ic]) =>
        h('button', { class: t === k ? 'on' : '', title: k === 'hand' ? '捲動與選取（手指滑動翻頁）' : l, onclick: () => { pref.tool = k; if (k === 'hl' && pref.color === '#111111') pref.color = '#facc15'; apply(); } }, icon(ic), h('span', { class: 'hide-narrow' }, l)))),
      t === 'pen' || t === 'hl' ? h('div', { class: 'ink-colors' }, INK_COLORS.map(([c, l]) => h('button', { class: `ink-dot ${pref.color === c ? 'on' : ''}`, title: l, 'aria-label': l, style: { background: c }, onclick: () => { pref.color = c; apply(); } }))) : null,
      t === 'pen' || t === 'hl' ? h('div', { class: 'seg' }, INK_W.map(([w, l]) => h('button', { class: pref.width === w ? 'on' : '', onclick: () => { pref.width = w; apply(); } }, l))) : null,
      h('button', { class: 'icon-btn sm', title: '復原上一筆', 'aria-label': '復原', disabled: !ed.undo.length, onclick: () => ed.undoLast() }, icon('undo')),
      matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0 ? h('label', { class: 'check small', title: '開啟後只有觸控筆會畫，手指照常捲動與縮放' },
        h('input', { type: 'checkbox', checked: pref.penOnly, onchange: (e) => { pref.penOnly = e.target.checked; apply(); } }), '只用觸控筆') : null,
      h('span', { class: 'muted small nb-priv', title: '跟「標註」分頁的「全域筆記」開關一致' }, annGlobal() ? '公開' : '只有你看得到'),
      inkStatusBadge(h));
  }
  function apply() {
    savePref();
    if (!editable || pref.tool === 'hand') viewer.stopInk();
    else viewer.startInk({ ...pref, canErase: (a) => a.author_id === S.user.id, onStroke: (page, st) => ed.stroke(page, st), onErase: (changed) => ed.erase(changed) });
    drawTools();
  }
  let metaT = null;
  function saveMeta() {
    clearTimeout(metaT);
    metaT = setTimeout(() => api.put(`/api/papers/${pid}/notebook`, { pages, bg }).catch(fail), 400);
  }
  function addPage() {
    pages = viewer.addPage();
    saveMeta();
    setTimeout(() => viewer.goTo(pages, 0, true), 50);
  }

  drawHead();
  viewer.openBlank({ pages, bg });
  viewer.setTail(editable ? h('button', { class: 'btn nb-add', onclick: () => addPage() }, icon('plus'), '新增一頁') : null);
  viewer.setAnnotations(anns);
  pageInfo.textContent = `1 / ${pages}`;
  apply();
  const start = +(params.get('page') || 0);
  if (start > 1) setTimeout(() => viewer.goTo(Math.min(start, pages)), 60);

  const onGoto = (e) => { if (e.detail?.pid === pid && e.detail.page) viewer.goTo(Math.min(e.detail.page, viewer.numPages), 0, true); };
  window.addEventListener('pl-nb-goto', onGoto);
  const onScope = () => drawTools();
  window.addEventListener('pl-ann-global', onScope);
  // 在論文那邊刪掉筆記頁的手寫：這裡也更新
  const onChanged = async (e) => {
    if (e.detail?.pid !== pid || e.detail?.from === 'notebook') return;
    try { anns = applyPending(await api.get(`/api/papers/${pid}/annotations`), pid).filter(mine); viewer.setAnnotations(anns); } catch { /* */ }
  };
  window.addEventListener('pl-nb-changed', onChanged);

  return {
    destroy: () => { window.removeEventListener('pl-nb-goto', onGoto); window.removeEventListener('pl-ann-global', onScope); window.removeEventListener('pl-nb-changed', onChanged); viewer.destroy(); },
    get page() { return viewer.current || 1; },
    refit: () => viewer.refit(),
  };
}

