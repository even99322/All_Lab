// 關聯圖：fcose 分群版面／時間軸版面，標籤依縮放顯示，滑過或點選時只凸顯相鄰論文。
import { h, icon, api, S, isDark, debounce } from './app.js';

let loaded = null;
function loadLibs() {
  const js = (src) => new Promise((res, rej) => { const s = h('script', { src }); s.onload = res; s.onerror = rej; document.head.append(s); });
  loaded ||= js('/static/vendor/cytoscape.min.js')
    .then(() => js('/static/vendor/fcose/layout-base.js'))
    .then(() => js('/static/vendor/fcose/cose-base.js'))
    .then(() => js('/static/vendor/fcose/cytoscape-fcose.js'));
  return loaded;
}

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const GREY = '#8a939b';

function styles() {
  const dark = isDark();
  const bg = css('--bg') || (dark ? '#14171a' : '#f6f5f1');
  const ink = css('--ink'), ink3 = css('--ink-3'), accent = css('--accent');
  const edge = dark ? '#56616b' : '#a9b3bb';
  return [
    { selector: 'node', style: {
      'background-color': 'data(color)', width: 'data(size)', height: 'data(size)', 'border-width': 1.5, 'border-color': bg,
      label: 'data(label)', 'font-size': 10, color: ink, 'text-valign': 'bottom', 'text-margin-y': 3,
      'text-outline-color': bg, 'text-outline-width': 2.5, 'text-max-width': 110, 'text-wrap': 'ellipsis',
      'min-zoomed-font-size': 9, 'transition-property': 'opacity', 'transition-duration': 150 } },
    { selector: 'node.hub', style: { 'font-size': 11, 'font-weight': 700, 'min-zoomed-font-size': 5 } },
    { selector: 'node.outside', style: { 'background-color': GREY, opacity: 0.75 } },
    { selector: ':parent', style: {
      'background-color': 'data(color)', 'background-opacity': dark ? 0.1 : 0.07, 'border-color': 'data(color)', 'border-width': 1.5,
      'border-opacity': 0.55, shape: 'round-rectangle', padding: 18, label: 'data(label)', 'text-max-width': 900, 'text-wrap': 'none', 'text-valign': 'top', 'text-halign': 'center',
      'font-size': 24, 'font-weight': 700, color: 'data(color)', 'text-outline-width': 0, 'text-margin-y': -6, 'min-zoomed-font-size': 3 } },
    { selector: 'node.tick', style: { 'background-opacity': 0, 'border-width': 0, width: 1, height: 1, label: 'data(label)', 'font-size': 20,
      color: ink3, 'text-valign': 'center', 'min-zoomed-font-size': 4, events: 'no' } },
    { selector: 'node.lane', style: { 'background-opacity': 0, 'border-width': 0, width: 1, height: 1, label: 'data(label)', 'font-size': 22,
      'font-weight': 700, color: 'data(color)', 'text-halign': 'left', 'text-valign': 'center', 'text-max-width': 260, 'min-zoomed-font-size': 4, events: 'no' } },
    { selector: 'edge', style: {
      width: 1.8, 'line-color': accent, 'target-arrow-color': accent, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.9,
      'curve-style': 'bezier', opacity: 0.85, label: '', 'font-size': 9, color: ink, 'text-outline-color': bg, 'text-outline-width': 2,
      'text-rotation': 'autorotate', 'transition-property': 'opacity', 'transition-duration': 150 } },
    { selector: 'edge[auto = 1]', style: { width: 0.9, 'line-color': edge, 'curve-style': 'haystack', 'haystack-radius': 0, opacity: dark ? 0.35 : 0.4,
      'target-arrow-shape': 'none' } },
    { selector: 'edge.cross', style: { opacity: dark ? 0.16 : 0.18 } },
    { selector: '.faded', style: { opacity: 0.1, 'text-opacity': 0 } },
    { selector: 'node.hl', style: { 'border-color': accent, 'border-width': 3, 'min-zoomed-font-size': 0, 'z-index': 10 } },
    { selector: 'node.center', style: { 'border-color': '#f59e0b', 'border-width': 4 } },
    { selector: 'edge.hl', style: { opacity: 1, width: 2.2, 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'line-color': accent,
      'target-arrow-color': accent, 'z-index': 9 } },
    { selector: 'edge.hl[auto = 0]', style: { label: 'data(rel)' } },
    { selector: 'edge.hl[auto = 1]', style: { 'line-style': 'dashed', 'line-dash-pattern': [5, 3] } },
    { selector: 'node:selected', style: { 'overlay-opacity': 0 } },
  ];
}

// 分群：每個分類各自排版，再把分類框依側欄順序排成幾列，保證框與框不重疊
async function packGroups(cy, cats, opts, aspect = 1.6) {
  const boxes = [];
  for (const c of cats) {
    const kids = cy.nodes(`[parent = "c${c}"]`);
    if (!kids.length) continue;
    const eles = kids.union(kids.edgesWith(kids));
    if (kids.length > 1) {
      const lay = eles.layout({ ...opts, fit: false, packComponents: true, tile: true });
      const done = lay.promiseOn('layoutstop');
      lay.run();
      await done;
    } else kids.position({ x: 0, y: 0 });
    const bb = kids.boundingBox({ includeLabels: true });
    boxes.push({ kids, bb, w: bb.w + 70, h: bb.h + 90 });
  }
  const total = boxes.reduce((a, b) => a + b.w * b.h, 0);
  // 依畫面長寬比決定每列寬度：寬螢幕排成扁長、手機排成直長
  const rowW = Math.max(Math.max(...boxes.map((b) => b.w)), Math.sqrt(total * aspect) * 1.15);
  let x = 0, y = 0, rowH = 0;
  for (const b of boxes) {
    if (x > 0 && x + b.w > rowW) { x = 0; y += rowH + 40; rowH = 0; }
    const dx = x + 35 - b.bb.x1, dy = y + 55 - b.bb.y1;
    b.kids.positions((n) => ({ x: n.position('x') + dx, y: n.position('y') + dy }));
    x += b.w + 40; rowH = Math.max(rowH, b.h);
  }
}

export async function viewGraph(view, params) {
  view.classList.add('graph-view');
  const get = (k, d = '') => params.get(k) ?? d;
  const cat = get('cat'), focus = get('focus'), all = get('all') === '1', noauto = get('noauto') === '1';
  const mode = get('layout', 'cluster');
  const set = (k, v) => { const q = new URLSearchParams(params); v ? q.set(k, v) : q.delete(k); location.hash = `#/graph?${q}`; };
  const box = h('div', { class: 'cy' });
  const info = h('aside', { class: 'g-info', hidden: true });
  let legendOpen = window.innerWidth >= 900;
  try { const v = localStorage.getItem('pl-glegend'); if (v != null) legendOpen = v !== '0'; } catch { /* */ }
  const legend = h('div', { class: `g-legend ${legendOpen ? '' : 'closed'}` });
  const findIn = h('input', { type: 'search', class: 'g-find', placeholder: '找論文（citekey／標題）', list: 'g-nodes' });
  const dl = h('datalist', { id: 'g-nodes' });
  view.replaceChildren(
    h('div', { class: 'toolbar pad g-bar' }, h('h1', { class: 'inline' }, '關聯圖'),
      h('select', { 'aria-label': '分類', onchange: (e) => set('cat', e.target.value) }, h('option', { value: '' }, '所有分類'),
        S.cats.map((c) => h('option', { value: c.id, selected: String(c.id) === cat }, c.name))),
      h('div', { class: 'seg' }, [['cluster', '分群'], ['time', '時間軸']].map(([k, l]) =>
        h('button', { class: mode === k ? 'on' : '', onclick: () => set('layout', k === 'cluster' ? '' : k) }, l))),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !noauto, onchange: (e) => set('noauto', e.target.checked ? '' : '1') }), '自動引用'),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: all, onchange: (e) => set('all', e.target.checked ? '1' : '') }), '沒有關聯的論文'),
      focus ? h('button', { class: 'btn small', onclick: () => set('focus', '') }, '顯示全部') : null,
      h('span', { class: 'grow' }), findIn, dl,
      h('button', { class: 'icon-btn', title: '重新置中', 'aria-label': '重新置中', onclick: () => cy && fitAll(true) }, icon('fit'))),
    h('div', { class: 'g-stage' }, box, legend, info));
  const q = new URLSearchParams(); if (cat) q.set('cat', cat); if (all) q.set('all_nodes', 1); if (focus) q.set('focus', focus); if (noauto) q.set('auto', 0);
  const [g] = await Promise.all([api.get(`/api/graph?${q}`), loadLibs()]);
  if (!g.nodes.length) { box.replaceChildren(h('div', { class: 'empty' }, icon('graph', 'big'), h('p', {}, '還沒有關聯。打開任一篇論文，在「關聯」分頁建立；上傳論文後系統也會從參考文獻自動找出引用。'))); return; }

  // ---------- 整理資料
  const indeg = {}, outdeg = {};
  g.edges.forEach((e) => { indeg[e.dst_id] = (indeg[e.dst_id] || 0) + 1; outdeg[e.src_id] = (outdeg[e.src_id] || 0) + 1; });
  const hubs = new Set([...g.nodes].sort((a, b) => (indeg[b.id] || 0) - (indeg[a.id] || 0)).slice(0, Math.max(6, Math.round(g.nodes.length * 0.08)))
    .filter((n) => (indeg[n.id] || 0) >= 2).map((n) => n.id));
  const catOf = (n) => n.cat && S.catById[n.cat] ? n.cat : 0;
  const usedCats = [...new Set(g.nodes.map(catOf))];
  const catName = (id) => (id ? S.catById[id].name : '未歸檔');
  const catColor = (id) => (id ? S.catById[id].color : GREY);
  const grouping = mode === 'cluster' && !cat && !focus && usedCats.length > 1;
  const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
  dl.replaceChildren(...g.nodes.map((n) => h('option', { value: n.citekey }, n.title)));

  const els = [];
  if (grouping) usedCats.forEach((c) => els.push({ data: { id: `c${c}`, label: catName(c), color: catColor(c) } }));
  for (const n of g.nodes) {
    const c = catOf(n);
    const outside = !n.in_cat;
    els.push({ data: { id: `n${n.id}`, pid: n.id, label: n.citekey, color: catColor(c), size: 12 + 5 * Math.sqrt(indeg[n.id] || 0),
      ...(grouping ? { parent: `c${c}` } : {}) }, classes: [hubs.has(n.id) ? 'hub' : '', outside ? 'outside' : '', String(n.id) === focus ? 'center' : ''].join(' ') });
  }
  // 跨分類的自動引用線畫得更淡，分類內的結構比較清楚
  for (const e of g.edges) els.push({ data: { id: `e${e.id}`, source: `n${e.src_id}`, target: `n${e.dst_id}`, rel: e.rel, auto: e.auto ? 1 : 0 },
    classes: e.auto && byId[e.src_id] && byId[e.dst_id] && catOf(byId[e.src_id]) !== catOf(byId[e.dst_id]) ? 'cross' : '' });

  // 時間軸：x＝年份、y＝分類泳道
  let layout;
  if (mode === 'time') {
    // 年份用「有論文的年份」依序排（序數軸），老論文不會把畫面拉得很寬
    const years = [...new Set(g.nodes.map((n) => n.year).filter(Boolean))].sort((a, b) => a - b);
    const colOf = Object.fromEntries(years.map((y, i) => [y, i]));
    const colW = 96;
    const lanes = [...usedCats].sort((a, b) => (a && b ? S.cats.indexOf(S.catById[a]) - S.cats.indexOf(S.catById[b]) : a ? -1 : 1));
    const laneH = {};
    const slot = {};
    for (const n of g.nodes) { const k = `${catOf(n)}:${n.year || 0}`; slot[k] = (slot[k] || 0) + 1; }
    lanes.forEach((c) => { laneH[c] = Math.max(1, ...g.nodes.filter((n) => catOf(n) === c).map((n) => slot[`${c}:${n.year || 0}`])) * 34 + 50; });
    const laneY = {}; let acc = 0; lanes.forEach((c) => { laneY[c] = acc; acc += laneH[c]; });
    const used = {};
    const pos = {};
    for (const n of g.nodes) {
      const c = catOf(n), k = `${c}:${n.year || 0}`;
      const i = used[k] = (used[k] || 0) + 1;
      const x = n.year ? colOf[n.year] * colW : -colW * 1.2;
      pos[`n${n.id}`] = { x: x + ((i % 2) ? 0 : colW * 0.28), y: laneY[c] + 30 + (i - 1) * 34 };
    }
    years.forEach((y, i) => els.push({ data: { id: `y${y}`, label: String(y) }, classes: 'tick', position: { x: i * colW, y: -40 } }));
    if (g.nodes.some((n) => !n.year)) els.push({ data: { id: 'y0', label: '年份不明' }, classes: 'tick', position: { x: -colW * 1.2, y: -40 } });
    lanes.forEach((c) => els.push({ data: { id: `l${c}`, label: catName(c), color: catColor(c) }, classes: 'lane', position: { x: -colW * 1.2 - 60, y: laneY[c] + laneH[c] / 2 - 10 } }));
    layout = { name: 'preset', positions: (node) => pos[node.id()] || node.position(), padding: 40 };
  } else {
    layout = { name: 'fcose', quality: 'proof', randomize: true, animate: false, nodeDimensionsIncludeLabels: false,
      nodeRepulsion: () => 9000, idealEdgeLength: (e) => (e.data('auto') ? 110 : 80), edgeElasticity: (e) => (e.data('auto') ? 0.25 : 0.45),
      nestingFactor: 0.12, gravity: 0.3, gravityRangeCompound: 1.6, gravityCompound: 1.4, numIter: 3000, tile: true, packComponents: true,
      nodeSeparation: 80, tilingPaddingVertical: 20, tilingPaddingHorizontal: 20, padding: 40, ...(window.PL_FCOSE || {}) };
  }

  const cy = window.cytoscape({ container: box, elements: els, style: styles(), layout: grouping ? { name: 'preset' } : layout,
    wheelSensitivity: 0.25, minZoom: 0.08, maxZoom: 2.5, boxSelectionEnabled: false, autoungrabify: mode === 'time' });
  if (grouping) await packGroups(cy, usedCats, layout, Math.max(0.4, box.clientWidth / Math.max(1, box.clientHeight)));
  // 置中時扣掉底部圖例的高度，避免蓋住論文
  const fitAll = (animate) => {
    const z0 = cy.zoom(), p0 = { ...cy.pan() };
    cy.fit(undefined, 40);
    if (!legend.classList.contains('closed')) {
      const L = legend.offsetHeight + 16, H = box.clientHeight;
      cy.zoom({ level: cy.zoom() * Math.max(0.5, (H - L) / H), renderedPosition: { x: box.clientWidth / 2, y: 20 } });
    }
    if (animate) { const z = cy.zoom(), p = { ...cy.pan() }; cy.zoom(z0); cy.pan(p0); cy.animate({ zoom: z, pan: p }, { duration: 250 }); }
  };
  fitAll();
  window.__cy = cy;
  if (focus) { const c = cy.$(`#n${focus}`); if (c.nonempty()) highlight(c, true); }

  // ---------- 凸顯與資訊欄
  let pinned = null;
  function clearHl() { cy.elements().removeClass('faded hl'); }
  function highlight(node, keep) {
    clearHl();
    const hood = node.closedNeighborhood();
    cy.elements().not(hood).not(hood.ancestors()).not(':parent').not('.tick, .lane').addClass('faded');
    hood.addClass('hl');
    if (keep) pinned = node;
  }
  cy.on('mouseover', 'node[pid]', (e) => { if (!pinned) highlight(e.target); });
  cy.on('mouseout', 'node[pid]', () => { if (!pinned) clearHl(); });
  cy.on('tap', 'node[pid]', (e) => { highlight(e.target, true); showInfo(e.target.data('pid')); });
  cy.on('dbltap', 'node[pid]', (e) => { location.hash = `#/p/${e.target.data('pid')}`; });
  cy.on('tap', (e) => { if (e.target === cy) { pinned = null; clearHl(); info.hidden = true; } });

  function showInfo(pid) {
    const n = byId[pid];
    const out = g.edges.filter((e) => e.src_id === pid), inn = g.edges.filter((e) => e.dst_id === pid);
    const row = (e, other) => h('button', { class: 'g-link', onclick: () => { const t = cy.$(`#n${other}`); highlight(t, true); showInfo(other); cy.animate({ center: { eles: t } }, { duration: 250 }); } },
      h('span', { class: `g-rel ${e.auto ? 'auto' : ''}` }, e.auto ? '引用' : e.rel), byId[other]?.citekey || other, h('small', { class: 'muted' }, ` ${byId[other]?.year || ''}`));
    info.hidden = false;
    info.replaceChildren(
      h('div', { class: 'row' }, h('span', { class: 'dot', style: { background: catColor(catOf(n)) } }), h('span', { class: 'muted small grow' }, catName(catOf(n))),
        h('button', { class: 'icon-btn sm', 'aria-label': '關閉', onclick: () => { info.hidden = true; pinned = null; clearHl(); } }, icon('x'))),
      h('b', { class: 'g-title' }, n.title), h('div', { class: 'muted small' }, [n.citekey, n.year, n.venue].filter(Boolean).join(' · ')),
      h('div', { class: 'row wrap' }, h('a', { class: 'btn primary small', href: `#/p/${pid}` }, '開啟'),
        h('button', { class: 'btn small', onclick: () => set('focus', String(pid)) }, '以這篇為中心')),
      inn.length ? h('div', { class: 'g-list' }, h('div', { class: 'muted small' }, `被 ${inn.length} 篇引用／連到`), inn.map((e) => row(e, e.src_id))) : null,
      out.length ? h('div', { class: 'g-list' }, h('div', { class: 'muted small' }, `引用／連到 ${out.length} 篇`), out.map((e) => row(e, e.dst_id))) : null);
  }

  // ---------- 圖例（點分類＝凸顯該分類）
  legend.replaceChildren(
    h('button', { class: 'g-leg-toggle', title: '顯示／收合圖例', onclick: () => { legend.classList.toggle('closed'); try { localStorage.setItem('pl-glegend', legend.classList.contains('closed') ? '0' : '1'); } catch { /* */ } } }, '圖例'),
    ...usedCats.map((c) => h('button', { class: 'g-leg', onclick: () => {
      pinned = null; info.hidden = true; clearHl();
      const nodes = cy.nodes('[pid]').filter((x) => catOf(byId[x.data('pid')]) === c);
      cy.elements().not(nodes).not(nodes.connectedEdges()).not(':parent').not('.tick, .lane').addClass('faded');
      cy.animate({ fit: { eles: nodes, padding: 60 } }, { duration: 300 });
    } }, h('span', { class: 'dot', style: { background: catColor(c) } }), catName(c))),
    h('div', { class: 'g-keys muted small' }, h('span', { class: 'k-solid' }), '手動關聯　', h('span', { class: 'k-auto' }), '自動引用　節點大小＝被引用數'));

  // ---------- 搜尋
  findIn.addEventListener('change', () => {
    const v = findIn.value.trim().toLowerCase(); if (!v) return;
    const n = g.nodes.find((x) => x.citekey.toLowerCase() === v) || g.nodes.find((x) => x.citekey.toLowerCase().includes(v) || x.title.toLowerCase().includes(v));
    if (!n) return;
    const t = cy.$(`#n${n.id}`); highlight(t, true); showInfo(n.id);
    cy.animate({ center: { eles: t }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: 300 });
  });

  const restyle = () => cy.style(styles());
  window.addEventListener('pl-theme', restyle);
  const onResize = debounce(() => cy.resize(), 150);
  window.addEventListener('resize', onResize);
  return () => { window.removeEventListener('pl-theme', restyle); window.removeEventListener('resize', onResize); cy.destroy(); };
}
