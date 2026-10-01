// 我的筆記：把所有論文裡的筆記頁、手寫、劃線、文字筆記集中在一頁看（可以篩選、搜尋、匯出 Markdown）
import { h, icon, api, S, fmtDate, debounce, esc, toast } from './app.js';
import { inkPreview, COLORS } from './viewer.js';
import { toggleNotebook } from './desk.js';
import { annGlobal } from './app.js';

const KINDS = [['all', '全部'], ['notebook', '筆記頁'], ['ink', '手寫'], ['highlight', '劃線'], ['note', '文字筆記']];
const SVGNS = 'http://www.w3.org/2000/svg';
const AR = 842 / 595;

// 筆記頁的縮圖：整頁 A4，含橫線／方格背景
function pageThumb(ink, bg) {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('viewBox', `0 0 1 ${AR}`);
  svg.setAttribute('class', `nb-thumb bg-${bg}`);
  const line = (x1, y1, x2, y2, c, w) => { const l = document.createElementNS(SVGNS, 'line'); Object.entries({ x1, y1, x2, y2, stroke: c, 'stroke-width': w }).forEach(([k, v]) => l.setAttribute(k, v)); svg.append(l); };
  if (bg === 'lined') for (let y = 24 / 595; y < AR; y += 24 / 595) line(0, y, 1, y, '#cdd8e4', 0.0015);
  if (bg === 'grid') { for (let y = 20 / 595; y < AR; y += 20 / 595) line(0, y, 1, y, '#e3e8ee', 0.001); for (let x = 20 / 595; x < 1; x += 20 / 595) line(x, 0, x, AR, '#e3e8ee', 0.001); }
  for (const st of ink?.strokes || []) {
    const p = document.createElementNS(SVGNS, 'polyline');
    p.setAttribute('points', st.p.map(([x, y]) => `${x},${(y * AR).toFixed(4)}`).join(' '));
    Object.entries({ fill: 'none', stroke: st.c || '#111', 'stroke-width': Math.max(0.002, st.w || 0.003), 'stroke-opacity': st.o ?? 1, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
      .forEach(([k, v]) => p.setAttribute(k, v));
    svg.append(p);
  }
  return svg;
}

export async function viewNotes(view, params) {
  view.classList.add('page', 'notes-page');
  let scope = params.get('scope') || (annGlobal() ? 'all' : 'mine');
  let kind = params.get('kind') || 'all';
  let q = params.get('q') || '';
  let offset = 0, items = [], more = false, data = null;
  const head = h('div', { class: 'notes-head' });
  const body = h('div', {});
  view.replaceChildren(
    h('h1', {}, '我的筆記'),
    h('p', { class: 'muted' }, '所有論文裡的筆記頁、手寫、劃線與文字筆記都集中在這裡。點一則會跳到論文的那個位置；點筆記頁會在論文旁邊打開。'),
    head, body);

  const setUrl = () => { const u = new URLSearchParams(); if (scope !== 'mine') u.set('scope', scope); if (kind !== 'all') u.set('kind', kind); if (q) u.set('q', q); history.replaceState(null, '', `#/notes${u.toString() ? `?${u}` : ''}`); };
  const search = h('input', { type: 'search', value: q, placeholder: '搜尋筆記內容、劃線文字、論文標題…', oninput: debounce((e) => { q = e.target.value.trim(); load(true); }, 300) });

  function drawHead() {
    const c = data?.counts || {};
    const total = (c.ink || 0) + (c.highlight || 0) + (c.note || 0);
    head.replaceChildren(
      h('div', { class: 'row wrap notes-bar' },
        h('div', { class: 'seg' },
          h('button', { class: scope === 'mine' ? 'on' : '', onclick: () => { scope = 'mine'; load(true); } }, '只有我的'),
          h('button', { class: scope === 'all' ? 'on' : '', onclick: () => { scope = 'all'; load(true); } }, '含所有人公開的')),
        h('label', { class: 'notes-search' }, icon('search'), search),
        h('span', { class: 'grow' }),
        h('button', { class: 'btn small', title: '把目前列出的劃線與文字筆記匯出成 Markdown', onclick: () => exportMd() }, icon('download'), '匯出 Markdown')),
      h('div', { class: 'chips wrap notes-kinds' }, KINDS.map(([k, l]) => {
        const n = k === 'all' ? total : k === 'notebook' ? (data?.notebooks?.length ?? c.notebook ?? 0) : c[k];
        return h('button', { class: `chip toggle ${kind === k ? 'on' : ''}`, style: { '--c': 'var(--accent)' }, onclick: () => { kind = k; load(true); } }, l, n != null ? h('span', { class: 'count' }, ` ${n}`) : null);
      })));
  }

  async function load(reset) {
    if (reset) { offset = 0; items = []; }
    setUrl();
    try {
      const u = new URLSearchParams({ scope, kind: kind === 'notebook' ? 'notebook' : kind, q, offset, limit: 120 });
      const d = await api.get(`/api/notes?${u}`);
      if (reset || !data) data = d; else data.counts = d.counts;
      items = items.concat(d.items); more = d.more; offset += d.items.length;
      if (reset) data.notebooks = d.notebooks;
      drawHead(); drawBody();
    } catch (e) { body.replaceChildren(h('div', { class: 'empty' }, h('p', {}, `無法載入：${e.message}`))); }
  }

  function notebookCard(nb) {
    return h('button', { class: 'nb-card', title: `打開「${nb.citekey}」的筆記頁`, onclick: () => toggleNotebook(nb.paper_id, { open: true, page: nb.preview_page }) },
      h('div', { class: 'nb-card-thumb' }, pageThumb(nb.preview, nb.bg)),
      h('div', { class: 'nb-card-text' },
        h('b', {}, nb.citekey),
        h('span', { class: 'muted small nb-card-title' }, nb.title),
        h('span', { class: 'muted small' }, `${nb.pages} 頁 · ${fmtDate(nb.updated_at)}`)));
  }

  function itemCard(a) {
    const c = a.kind === 'ink' ? { css: 'var(--ink-3)', name: '手寫' } : COLORS[a.color] || COLORS.yellow;
    const open = () => {
      if (a.nb) return toggleNotebook(a.paper_id, { open: true, page: a.page });
      location.hash = `#/p/${a.paper_id}?ann=${a.id}`;
    };
    const kindLabel = a.nb ? `筆記頁 ${a.page}` : a.kind === 'ink' ? '手寫' : a.kind === 'highlight' ? c.name : a.page ? '頁面筆記' : '整篇筆記';
    return h('div', { class: 'ann note-item', style: { '--c': c.css } },
      h('div', { class: 'ann-top' },
        h('button', { class: 'ann-page', onclick: open }, a.nb ? `筆記頁 ${a.page}` : a.page ? `p.${a.page}` : '整篇'),
        h('span', { class: 'muted small' }, [kindLabel, scope === 'all' ? a.who : null, fmtDate(a.updated_at)].filter(Boolean).join(' · '),
          a.private ? h('span', { title: '私人：只有你看得到' }, ' · ', icon('lock')) : null,
          a.n_replies ? ` · 討論 ${a.n_replies}` : ''),
        h('span', { class: 'grow' }),
        h('button', { class: 'icon-btn sm', 'aria-label': '打開', title: a.nb ? '打開筆記頁' : '跳到論文的這個位置', onclick: open }, icon('external'))),
      a.kind === 'ink' && a.ink?.strokes?.length ? h('button', { class: 'ink-card', onclick: open }, inkPreview(a.ink, 180),
        h('span', { class: 'muted small' }, `${a.ink.total} 筆`)) : null,
      a.quote ? h('blockquote', {}, a.quote) : null,
      a.body ? h('div', { class: 'ann-body', html: esc(a.body).replace(/@(\S+)/g, '<span class="at">@$1</span>') }) : null);
  }

  function drawBody() {
    const parts = [];
    if ((kind === 'all' || kind === 'notebook') && data.notebooks?.length) {
      parts.push(h('section', { class: 'notes-sec' },
        h('h2', {}, icon('book'), ' 筆記頁', h('span', { class: 'muted small' }, `（${data.notebooks.length} 本）`)),
        h('div', { class: 'nb-grid' }, data.notebooks.map(notebookCard))));
    }
    const list = items;
    if (list.length) {
      // 依論文分組（照最近修改的順序）
      const groups = new Map();
      for (const a of list) { if (!groups.has(a.paper_id)) groups.set(a.paper_id, { title: a.title, citekey: a.citekey, year: a.year, list: [] }); groups.get(a.paper_id).list.push(a); }
      parts.push(h('section', { class: 'notes-sec' },
        kind === 'notebook' ? h('h2', {}, '筆記頁的每一頁') : h('h2', {}, icon('pen'), ' 標註與手寫'),
        [...groups.entries()].map(([pid, g]) => h('div', { class: 'notes-group' },
          h('a', { class: 'notes-paper', href: `#/p/${pid}` }, h('b', {}, g.citekey), h('span', {}, ` ${g.title}`), g.year ? h('span', { class: 'muted' }, ` · ${g.year}`) : null,
            h('span', { class: 'count' }, g.list.length)),
          h('div', { class: 'anns' }, g.list.map(itemCard)))),
        more ? h('div', { class: 'row center' }, h('button', { class: 'btn', onclick: () => load(false) }, '載入更多')) : null));
    }
    if (!parts.length) {
      parts.push(h('div', { class: 'empty' }, icon('pen', 'big'),
        h('p', {}, q ? '找不到符合的筆記。' : scope === 'mine' ? '你還沒有筆記。在論文上劃線、手寫，或按閱讀器上方的「筆記頁」開始寫。' : '還沒有筆記。')));
    }
    body.replaceChildren(...parts);
  }

  function exportMd() {
    const text = items.filter((a) => a.kind !== 'ink');
    if (!text.length) return toast('目前沒有可以匯出的劃線或文字筆記');
    const groups = new Map();
    for (const a of text) { if (!groups.has(a.paper_id)) groups.set(a.paper_id, { t: `${a.citekey} — ${a.title}`, list: [] }); groups.get(a.paper_id).list.push(a); }
    let md = `# ${scope === 'mine' ? '我的筆記' : '筆記'}（${new Date().toLocaleDateString()}）\n`;
    for (const g of groups.values()) {
      md += `\n## ${g.t}\n`;
      for (const a of g.list.sort((x, y) => (x.page ?? 1e9) - (y.page ?? 1e9))) {
        md += `\n- **${a.page ? `p.${a.page}` : '整篇'}**${a.kind === 'highlight' ? `（${COLORS[a.color]?.name || '劃線'}）` : ''}${scope === 'all' && a.who ? ` · ${a.who}` : ''}\n`;
        if (a.quote) md += `  > ${a.quote.replace(/\n/g, ' ')}\n`;
        if (a.body) md += `  ${a.body.replace(/\n/g, '\n  ')}\n`;
      }
    }
    const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }));
    const aEl = h('a', { href: url, download: `notes-${new Date().toISOString().slice(0, 10)}.md` });
    document.body.append(aEl); aEl.click(); aEl.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  drawHead();
  body.replaceChildren(h('div', { class: 'empty' }, h('div', { class: 'spinner' })));
  await load(true);
}
