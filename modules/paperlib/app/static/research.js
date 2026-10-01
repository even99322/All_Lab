// 研究工具的畫面：參數（比較表、名稱對照、預估值、qubit／cavity 對照）、圖表剪貼簿、相似論文、
// 預印本與重複論文、入門路徑、組會投影片、實驗室動態看板。
import { h, icon, api, S, can, isAdmin, toast, fail, modal, menu, confirmBox, fmtDate, debounce, esc, autoGrow, refreshNav, route } from './app.js';
import { watchJob, mdRender } from './more.js';

const canManage = () => isAdmin() || can('manage');
const aiOn = () => can('ai') && S.site.ai?.enabled;
// 符號顯示：ω_m/2π → ω<sub>m</sub>/2π、μ0H_0 → μ<sub>0</sub>H<sub>0</sub>
const symHtml = (t) => esc(t).replace(/_\{([^}]+)\}|_([A-Za-z0-9]+)/g, (m, a, b) => `<sub>${a || b}</sub>`).replace(/μ0/g, 'μ<sub>0</sub>').replace(/\^\{([^}]+)\}|\^([-+0-9]+)/g, (m, a, b) => `<sup>${a || b}</sup>`);
const sym = (t, pre = ' ') => (t ? h('span', { class: 'sym', html: pre + symHtml(t) }) : null);
const fmtNum = (v) => (v == null || !Number.isFinite(v) ? '' : Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0) ? v.toExponential(1) : String(Math.round(v * 100) / 100));

// 搜尋論文（標題、作者、citekey）後點選
export function lookupInput(onPick, { placeholder = '搜尋論文（標題、作者、citekey）', exclude = [] } = {}) {
  const res = h('div', { class: 'lookup-res' });
  const q = h('input', { placeholder, oninput: debounce(async (e) => {
    const v = e.target.value.trim();
    if (v.length < 2) return res.replaceChildren();
    const rows = (await api.get(`/api/lookup?q=${encodeURIComponent(v)}`)).filter((r) => !exclude.includes(r.id));
    res.replaceChildren(...rows.map((r) => h('button', { class: 'lookup-item', onclick: () => { res.replaceChildren(); q.value = ''; onPick(r); } },
      h('b', {}, r.title), h('span', { class: 'muted small' }, ` ${r.authors[0] || ''} ${r.year || ''} · ${r.citekey}`))));
  }, 250) });
  return h('div', { class: 'rel-wrap grow' }, q, res);
}

// 背景工作完成後下載檔案（投影片）
function jobDownload(jobId, el, label = '下載') {
  return watchJob(jobId, el, (j) => {
    if (j.state === 'done' && String(j.result).startsWith('FILE:')) {
      const name = j.result.slice(5);
      const a = h('a', { class: 'btn primary', href: `/api/exports/${encodeURIComponent(name)}`, download: name }, icon('download'), label);
      el.replaceChildren(icon('check'), ' 完成：', a);
      a.click();
    }
  });
}

// ================================================================== 參數
let REF = null;
async function ref(force = false) {
  if (!REF || force) REF = await api.get('/api/params/reference');
  return REF;
}
const defMap = () => Object.fromEntries((REF?.defs || []).map((d) => [d.key, d]));

// 論文頁「重點」分頁裡的參數區塊
export function paramsBlock(p, { onChange } = {}) {
  const box = h('div', { class: 'sub' });
  const editable = can('edit_meta', p);
  async function draw() {
    const [r] = await Promise.all([api.get(`/api/papers/${p.id}/params`), ref()]);
    const dm = Object.fromEntries(r.defs.map((d) => [d.key, d]));
    const rows = r.defs.filter((d) => r.params[d.key]);
    box.replaceChildren(
      h('h3', {}, icon('sliders'), '參數',
        editable ? h('button', { class: 'btn small ghost h3-act', onclick: () => editParams(p, r, draw) }, icon('pen'), '編輯') : null,
        editable && aiOn() ? h('button', { class: 'btn small ghost', onclick: () => aiParams(p, r, draw) }, icon('spark'), 'AI 抽取') : null,
        h('a', { class: 'btn small ghost', href: `#/params?ids=${p.id}` }, icon('table'), '比較')),
      h('p', { class: 'hint' }, '頻率與速率為 ω/2π，線寬統一為 HWHM；符號與其他寫法見', h('a', { href: '#/params?tab=defs' }, '名稱對照表'), '。'),
      rows.length ? h('table', { class: 'ptable' }, rows.map((d) => {
        const v = r.params[d.key];
        return h('tr', { title: [v.raw && `原文：${v.raw}`, v.note, v.page && `第 ${v.page} 頁`].filter(Boolean).join('\n') },
          h('th', {}, d.name, sym(d.symbol)),
          h('td', {}, h('b', {}, v.value), d.unit && v.num != null ? ` ${d.unit}` : '', v.source === 'ai' ? h('span', { class: 'pill ai', title: 'AI 抽取，尚未有人確認' }, 'AI') : null,
            v.note ? h('div', { class: 'muted small' }, v.note) : null));
      })) : h('p', { class: 'muted small' }, editable ? '還沒有參數。按「編輯」手動填，或「AI 抽取」從全文找。' : '還沒有參數。'));
    onChange && onChange(rows.length);
    void dm;
  }
  draw().catch(fail);
  return box;
}

function editParams(p, r, done) {
  let showAll = false;
  const inputs = {};
  const body = h('div', { class: 'stack' });
  function draw() {
    const groups = {};
    r.defs.filter((d) => showAll || d.in_compare || r.params[d.key]).forEach((d) => (groups[d.grp || '其他'] ||= []).push(d));
    body.replaceChildren(
      h('p', { class: 'hint' }, '數值換成右邊的單位填寫（頻率、速率用 ω/2π；線寬用 HWHM）。可以寫範圍或文字，例如「10–20」「YIG 球 1 mm」。清空就是刪除。'),
      ...Object.entries(groups).map(([g, ds]) => h('fieldset', { class: 'pgroup' }, h('legend', {}, g),
        ds.map((d) => {
          const cur = r.params[d.key];
          const inp = inputs[d.key] || h('input', { value: cur?.value || '', placeholder: d.aliases.split(',')[0] || '' });
          inputs[d.key] = inp;
          return h('label', { class: 'prow', title: `${d.definition}\n其他寫法：${d.aliases}${d.convention ? `\n注意：${d.convention}` : ''}` },
            h('span', { class: 'pname' }, d.name, sym(d.symbol), cur?.source === 'ai' ? h('span', { class: 'pill ai' }, 'AI') : null),
            h('span', { class: 'pval' }, inp, d.unit ? h('span', { class: 'unit' }, d.unit) : null));
        }))),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: showAll, onchange: (e) => { showAll = e.target.checked; draw(); } }), '顯示全部參數（含不放進比較表的）'),
      h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
        const values = {};
        for (const [k, inp] of Object.entries(inputs)) if ((r.params[k]?.value || '') !== inp.value.trim()) values[k] = inp.value.trim();
        if (!Object.keys(values).length) return md.close();
        try { await api.put(`/api/papers/${p.id}/params`, { values }); md.close(); toast('已儲存參數'); done(); } catch (e) { fail(e); }
      } }, '儲存')));
  }
  draw();
  const md = modal(`參數：${p.citekey}`, body, { wide: true });
}

async function aiParams(p, r, done) {
  const body = h('div', { class: 'stack' }, h('div', { class: 'empty' }, h('div', { class: 'spinner' }), h('p', {}, 'AI 正在讀全文找參數…（約 10–60 秒）')));
  const md = modal('AI 抽取參數', body, { wide: true });
  let found;
  try { found = (await api.post(`/api/papers/${p.id}/params/ai`)).found; } catch (e) { body.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
  const dm = Object.fromEntries(r.defs.map((d) => [d.key, d]));
  const rows = Object.entries(found).map(([k, v]) => {
    const cur = r.params[k];
    const cb = h('input', { type: 'checkbox', checked: !cur || cur.source === 'ai' });
    const inp = h('input', { value: v.value });
    return { k, v, cb, inp, el: h('div', { class: 'ai-row' },
      h('label', { class: 'check' }, cb, h('b', {}, dm[k]?.name || k), sym(dm[k]?.symbol),
        cur && cur.source !== 'ai' ? h('span', { class: 'pill warn' }, `會覆蓋人工填的：${cur.value}`) : null),
      h('div', { class: 'row' }, inp, h('span', { class: 'unit' }, dm[k]?.unit || '')),
      h('div', { class: 'muted small' }, [v.raw && `原文：${v.raw}`, v.page && `第 ${v.page} 頁`, v.note].filter(Boolean).join(' · '))) };
  });
  if (!rows.length) { body.replaceChildren(h('p', {}, 'AI 在這篇裡沒有找到對照表中的參數。')); return; }
  body.replaceChildren(h('p', { class: 'hint' }, 'AI 已把數值換成對照表的單位（ω/2π、HWHM）。請對照原文確認後套用；套用的值會標「AI」，有人修改後標記消失。'), ...rows.map((x) => x.el),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      const values = {}, meta = {};
      rows.filter((x) => x.cb.checked).forEach((x) => { values[x.k] = x.inp.value; meta[x.k] = x.v; });
      try { await api.put(`/api/papers/${p.id}/params`, { values, source: 'ai', meta }); md.close(); toast('已套用'); done(); } catch (e) { fail(e); }
    } }, '套用勾選的參數')));
}

// 耦合區間（用 g、κ_m、κ_c、ω_c 推算）
function derived(ps) {
  const n = (k) => ps[k]?.num;
  const g = n('g_mc'), km = n('kappa_m'), kc = n('kappa_c'), wc = n('omega_c');
  const out = {};
  if (g && km && kc) {
    out.C = (g * g) / (km * kc);
    out.regime = g > km && g > kc ? '強耦合' : kc > g && g > km ? 'Purcell 區' : km > g && g > kc ? '磁致透明（MIT）' : '弱耦合';
  }
  if (g && wc && g / (wc * 1000) >= 0.1) out.regime = `超強耦合（g/ω = ${(g / (wc * 1000)).toFixed(2)}）`;
  return out;
}

export async function viewParams(view, params) {
  view.classList.add('page');
  const tab = params.get('tab') || 'compare';
  await ref(true);
  const tabs = [['compare', '比較表', 'table'], ['defs', '名稱對照', 'book'], ['typical', '預估值', 'sliders'], ['map', 'qubit／cavity 對照', 'sim']];
  const body = h('div', {});
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '參數'),
      h('p', { class: 'muted' }, '把論文裡的實驗參數整理成可以比較的表。各論文對同一個量的寫法不同（g、g_m、J…；κ、γ、線寬…），先用名稱對照表統一，再比較；預估值表給各平台的典型數量級，qubit／cavity 對照把 magnon 的描述換成 cQED 與腔 QED 的語言。'))),
    h('div', { class: 'seg tabs-seg' }, tabs.map(([k, l, ic]) => h('a', { class: `btn small ${tab === k ? 'on' : ''}`, href: `#/params?tab=${k}${k === 'compare' && params.get('ids') ? `&ids=${params.get('ids')}` : ''}` }, icon(ic), l))),
    body);
  if (tab === 'compare') await compareTab(body, params);
  else if (tab === 'defs') defsTab(body);
  else if (tab === 'typical') typicalTab(body);
  else mapTab(body);
}

async function compareTab(body, params) {
  let ids = (params.get('ids') || '').split(',').map(Number).filter(Boolean);
  let showAll = false;
  const out = h('div', {});
  const jobEl = h('div', { class: 'tr-test' });
  const setIds = (next) => { ids = next; history.replaceState(null, '', `#/params?tab=compare&ids=${ids.join(',')}`); draw(); };
  async function draw() {
    if (!ids.length) {
      out.replaceChildren(h('div', { class: 'empty' }, icon('table', 'big'), h('p', {}, '先加入要比較的論文。也可以在論文列表按「選取」勾幾篇，再按「比較參數」。')));
      return;
    }
    const t = await api.get(`/api/params/compare?ids=${ids.join(',')}`);
    const rows = t.defs.filter((d) => showAll || t.used.includes(d.key));
    const der = t.papers.map((p) => derived(p.params));
    const missing = t.papers.filter((p) => !Object.keys(p.params).length);
    const cell = (p, d) => {
      const v = p.params[d.key];
      const td = h('td', { class: v ? '' : 'empty-cell', title: v ? [v.raw && `原文：${v.raw}`, v.note, v.page && `第 ${v.page} 頁`, v.who && `${v.source === 'ai' ? 'AI' : v.who}`].filter(Boolean).join('\n') : '' },
        v ? [v.value, v.source === 'ai' ? h('sup', { class: 'ai-mark', title: 'AI 抽取' }, 'AI') : null] : '—');
      if (can('edit_meta', p)) {
        td.classList.add('editable');
        td.onclick = async () => {
          const nv = prompt(`${p.citekey}：${d.name}${d.unit ? `（${d.unit}）` : ''}\n清空＝刪除`, v?.value || '');
          if (nv == null) return;
          try { await api.put(`/api/papers/${p.id}/params`, { values: { [d.key]: nv } }); draw(); } catch (e) { fail(e); }
        };
      }
      return td;
    };
    out.replaceChildren(
      h('div', { class: 'table-wrap' }, h('table', { class: 'ctable' },
        h('thead', {}, h('tr', {}, h('th', { class: 'sticky' }, '參數'), t.papers.map((p) => h('th', {},
          h('a', { href: `#/p/${p.id}`, title: p.title }, p.citekey), h('div', { class: 'muted small' }, p.year || ''),
          h('button', { class: 'icon-btn sm', 'aria-label': '移除', onclick: () => setIds(ids.filter((x) => x !== p.id)) }, icon('x')))))),
        h('tbody', {},
          rows.map((d) => h('tr', {}, h('th', { class: 'sticky', title: `${d.definition}\n其他寫法：${d.aliases}` }, d.name, sym(d.symbol), d.unit ? h('span', { class: 'unit' }, ` [${d.unit}]`) : null),
            t.papers.map((p) => cell(p, d)))),
          der.some((x) => x.C != null || x.regime) ? [
            h('tr', { class: 'derived' }, h('th', { class: 'sticky', title: 'C = g²/(κ_m κ_c)，κ 為 HWHM' }, '協同度 C（計算）'), der.map((x) => h('td', {}, x.C != null ? fmtNum(x.C) : '—'))),
            h('tr', { class: 'derived' }, h('th', { class: 'sticky' }, '耦合區間（計算）'), der.map((x) => h('td', {}, x.regime || '—')))] : null))),
      h('p', { class: 'hint' }, '滑鼠移到格子上看原文寫法與換算說明；有編輯權限的人點格子可以修改。標 AI 的數值請對照原文確認。'),
      h('div', { class: 'row wrap' },
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: showAll, onchange: (e) => { showAll = e.target.checked; draw(); } }), '顯示空白的參數列'),
        h('span', { class: 'grow' }),
        aiOn() && missing.length ? h('button', { class: 'btn small', onclick: async () => {
          try { const r = await api.post('/api/params/batch', { ids: missing.map((p) => p.id) }); watchJob(r.job, jobEl, () => draw()); } catch (e) { fail(e); }
        } }, icon('spark'), `AI 抽取沒有參數的 ${missing.length} 篇`) : null,
        h('a', { class: 'btn small ghost', href: `/api/params/compare.csv?ids=${ids.join(',')}` }, icon('download'), 'CSV'),
        aiOn() ? h('button', { class: 'btn small', onclick: async (e) => {
          const b = e.currentTarget; b.disabled = true;
          const box = h('div', { class: 'ask-a' }, h('span', { class: 'spinner sm' }), ' AI 解讀中…');
          out.append(h('div', { class: 'card-form' }, h('h3', {}, icon('spark'), 'AI 解讀'), box));
          try { const r = await api.post('/api/params/interpret', { ids }); box.innerHTML = mdRender(r.text, t.papers.map((p) => ({ id: p.id, citekey: p.citekey, title: p.title }))); } catch (err) { box.replaceChildren(h('span', { class: 'err' }, err.message)); }
          b.disabled = false;
        } }, icon('spark'), 'AI 解讀這張表') : null),
      jobEl);
  }
  body.replaceChildren(h('div', { class: 'row wrap' }, lookupInput((r) => { if (!ids.includes(r.id)) setIds([...ids, r.id]); }, { placeholder: '加入論文到比較表…' })), out);
  await draw();
}

// 參考表的編輯對話框（三張表共用）
function refEditor(table, row, fields, done) {
  const ins = {};
  const md = modal(row ? '編輯' : '新增', h('div', { class: 'stack' },
    fields.map(([k, label, kind]) => {
      const v = row ? row[k] ?? '' : '';
      ins[k] = kind === 'area' ? h('textarea', { rows: 2 }, String(v)) : kind === 'check' ? h('input', { type: 'checkbox', checked: row ? !!row[k] : true })
        : kind === 'key' ? h('select', {}, REF.defs.map((d) => h('option', { value: d.key, selected: d.key === v }, `${d.name}（${d.key}）`)))
          : h('input', { value: String(v), readonly: kind === 'ro' && row ? true : null });
      return kind === 'check' ? h('label', { class: 'check' }, ins[k], label) : h('label', {}, label, ins[k]);
    }),
    h('div', { class: 'row' },
      row ? h('button', { class: 'btn danger ghost', onclick: async () => { if (await confirmBox('刪除這一列？', '刪除')) { await api.del(`/api/params/ref/${table}/${row.id}`); md.close(); done(); } } }, icon('trash'), '刪除') : null,
      h('span', { class: 'grow' }),
      h('button', { class: 'btn primary', onclick: async () => {
        const b = {};
        for (const [k, el] of Object.entries(ins)) b[k] = el.type === 'checkbox' ? (el.checked ? 1 : 0) : el.value;
        try { row ? await api.patch(`/api/params/ref/${table}/${row.id}`, b) : await api.post(`/api/params/ref/${table}`, b); md.close(); toast('已儲存'); done(); } catch (e) { fail(e); }
      } }, '儲存'))), { wide: true });
  md.el.querySelectorAll('textarea').forEach(autoGrow);
}

function searchBox(onInput) {
  return h('input', { type: 'search', class: 'grow', placeholder: '篩選…（中文、符號、英文寫法都可以）', oninput: debounce((e) => onInput(e.target.value.trim().toLowerCase()), 150) });
}

function defsTab(body) {
  const F = [['key', '代號（英文，建立後不能改）', 'ro'], ['name', '名稱'], ['symbol', '符號'], ['aliases', '其他寫法（逗號分隔）', 'area'], ['unit', '統一單位'], ['grp', '分群'],
    ['definition', '定義', 'area'], ['convention', '慣例與陷阱', 'area'], ['cqed', 'qubit／cavity 對應', 'area'], ['in_compare', '放進比較表與 AI 抽取', 'check']];
  let q = '';
  const tbl = h('div', {});
  function draw() {
    const rows = REF.defs.filter((d) => !q || Object.values(d).join(' ').toLowerCase().includes(q));
    const groups = {};
    rows.forEach((d) => (groups[d.grp || '其他'] ||= []).push(d));
    tbl.replaceChildren(...Object.entries(groups).map(([g, ds]) => h('section', {}, h('h2', {}, g),
      h('table', { class: 'rtable' }, h('thead', {}, h('tr', {}, ['名稱', '其他寫法', '單位', '定義', '慣例與陷阱', 'qubit／cavity'].map((x) => h('th', {}, x)))),
        h('tbody', {}, ds.map((d) => h('tr', { class: canManage() ? 'editable' : '', onclick: canManage() ? () => refEditor('defs', d, F, reload) : null },
          h('td', { 'data-label': '名稱' }, h('b', {}, d.name), d.symbol ? h('div', { class: 'sym big', html: symHtml(d.symbol) }) : null, !d.in_compare ? h('div', { class: 'muted small' }, '（不放進比較表）') : null),
          h('td', { 'data-label': '其他寫法' }, h('div', { class: 'chips wrap' }, d.aliases.split(/[,，]/).map((a) => a.trim()).filter(Boolean).map((a) => h('span', { class: 'chip mono' }, a)))),
          h('td', { 'data-label': '單位' }, d.unit || '—'),
          h('td', { 'data-label': '定義' }, d.definition),
          h('td', { 'data-label': '慣例與陷阱', class: 'warn-text' }, d.convention),
          h('td', { 'data-label': 'qubit／cavity' }, d.cqed))))))));
    if (!rows.length) tbl.replaceChildren(h('p', { class: 'muted' }, '沒有符合的參數。'));
  }
  const reload = async () => { await ref(true); draw(); };
  body.replaceChildren(
    h('div', { class: 'row wrap toolbar' }, searchBox((v) => { q = v; draw(); }),
      canManage() ? h('button', { class: 'btn small', onclick: () => refEditor('defs', null, F, reload) }, icon('plus'), '新增參數') : null,
      isAdmin() ? h('button', { class: 'btn small ghost', onclick: async () => { if (await confirmBox('把名稱對照表恢復成預設內容？你們的修改會消失（論文已填的參數不受影響）。', '恢復預設')) { await api.post('/api/params/reference/reset', { table: 'defs' }); reload(); } } }, '恢復預設') : null),
    h('p', { class: 'hint' }, 'AI 抽取參數時會照這張表認名稱、換單位；同一個量在不同論文的寫法列在「其他寫法」。', canManage() ? '點任一列可以修改。' : ''),
    tbl);
  draw();
}

function typicalTab(body) {
  const F = [['platform', '平台／架設'], ['key', '參數', 'key'], ['range_text', '典型範圍（文字）'], ['lo', '下限（數字，統一單位）'], ['hi', '上限（數字，統一單位）'], ['note', '備註', 'area'], ['source', '來源', 'area']];
  const dm = defMap();
  let q = '';
  const tbl = h('div', {});
  const rng = (k) => { const r = REF.ranges[k]; return r ? `${fmtNum(r.min)}${r.max !== r.min ? `–${fmtNum(r.max)}` : ''}（${r.n} 篇）` : '—'; };
  function draw() {
    const rows = REF.typical.filter((t) => !q || [t.platform, t.range_text, t.note, dm[t.key]?.name, dm[t.key]?.symbol, t.key].join(' ').toLowerCase().includes(q));
    const groups = {};
    rows.forEach((t) => (groups[t.platform] ||= []).push(t));
    tbl.replaceChildren(...Object.entries(groups).map(([g, ts]) => h('section', {}, h('h2', {}, g),
      h('table', { class: 'rtable' }, h('thead', {}, h('tr', {}, ['參數', '典型範圍', '論文庫實測', '備註'].map((x) => h('th', {}, x)))),
        h('tbody', {}, ts.map((t) => { const d = dm[t.key]; return h('tr', { class: canManage() ? 'editable' : '', onclick: canManage() ? () => refEditor('typical', t, F, reload) : null },
          h('td', { 'data-label': '參數' }, h('b', {}, d?.name || t.key), sym(d?.symbol), d?.unit ? h('span', { class: 'unit' }, ` [${d.unit}]`) : null),
          h('td', { 'data-label': '典型範圍' }, t.range_text),
          h('td', { 'data-label': '論文庫實測', class: 'mono' }, rng(t.key)),
          h('td', { 'data-label': '備註' }, t.note, t.source ? h('div', { class: 'muted small' }, t.source) : null)); }))))));
  }
  const reload = async () => { await ref(true); draw(); };
  body.replaceChildren(
    h('div', { class: 'row wrap toolbar' }, searchBox((v) => { q = v; draw(); }),
      canManage() ? h('button', { class: 'btn small', onclick: () => refEditor('typical', null, F, reload) }, icon('plus'), '新增一列') : null,
      isAdmin() ? h('button', { class: 'btn small ghost', onclick: async () => { if (await confirmBox('把預估值表恢復成預設內容？', '恢復預設')) { await api.post('/api/params/reference/reset', { table: 'typical' }); reload(); } } }, '恢復預設') : null),
    h('p', { class: 'hint' }, `設計實驗前拿來估數量級。預設內容是常見的典型範圍，請以原文核對並依實驗室經驗修改；「論文庫實測」是從 ${REF.n_papers} 篇已填參數的論文算出的範圍。`),
    tbl);
  draw();
}

function mapTab(body) {
  const F = [['grp', '分群'], ['magnon', 'magnon（我們的描述）', 'area'], ['cqed', '超導電路（cQED）', 'area'], ['cavity', '原子腔／波導 QED', 'area'], ['formula', '公式或換算', 'area'], ['note', '備註', 'area']];
  let q = '';
  const tbl = h('div', {});
  function draw() {
    const rows = REF.map.filter((m) => !q || Object.values(m).join(' ').toLowerCase().includes(q));
    const groups = {};
    rows.forEach((m) => (groups[m.grp || '其他'] ||= []).push(m));
    tbl.replaceChildren(...Object.entries(groups).map(([g, ms]) => h('section', {}, h('h2', {}, g),
      h('table', { class: 'rtable map' }, h('thead', {}, h('tr', {}, ['magnon', '超導電路（cQED）', '原子腔／波導 QED', '公式或換算'].map((x) => h('th', {}, x)))),
        h('tbody', {}, ms.map((m) => h('tr', { class: canManage() ? 'editable' : '', onclick: canManage() ? () => refEditor('map', m, F, reload) : null },
          h('td', { 'data-label': 'magnon' }, h('b', {}, m.magnon)),
          h('td', { 'data-label': 'cQED' }, m.cqed),
          h('td', { 'data-label': '腔／波導 QED' }, m.cavity),
          h('td', { 'data-label': '公式' }, m.formula ? h('code', { class: 'formula' }, m.formula) : null, m.note ? h('div', { class: 'muted small' }, m.note) : null))))))));
  }
  const reload = async () => { await ref(true); draw(); };
  body.replaceChildren(
    h('div', { class: 'row wrap toolbar' }, searchBox((v) => { q = v; draw(); }),
      canManage() ? h('button', { class: 'btn small', onclick: () => refEditor('map', null, F, reload) }, icon('plus'), '新增一列') : null,
      isAdmin() ? h('button', { class: 'btn small ghost', onclick: async () => { if (await confirmBox('把對照表恢復成預設內容？', '恢復預設')) { await api.post('/api/params/reference/reset', { table: 'map' }); reload(); } } }, '恢復預設') : null),
    h('p', { class: 'hint' }, '讀 cQED 或波導 QED 論文時，用這張表把對方的語言換回 magnon 的描述（反之亦然）。注意各領域對線寬（HWHM／FWHM）與 2π 的慣例不同。'),
    tbl);
  draw();
}

// ================================================================== 圖表剪貼簿
const KINDS = { figure: '圖', equation: '公式', table: '表格' };

export function figCard(f, { onChange, showPaper = false, sel = null, onSelect, jump } = {}) {
  const url = `/api/figures/${f.id}/image`;
  const mine = f.author_id === S.user.id || isAdmin();
  const card = h('article', { class: `fig-card ${sel?.has(f.id) ? 'is-sel' : ''}` },
    sel ? h('input', { type: 'checkbox', class: 'sel', checked: sel.has(f.id), 'aria-label': '選取', onchange: (e) => { e.target.checked ? sel.add(f.id) : sel.delete(f.id); card.classList.toggle('is-sel', e.target.checked); onSelect && onSelect(); } }) : null,
    h('a', { class: 'fig-img', href: jump ? '#' : `#/p/${f.paper_id}/figs?page=${f.page}&fig=${f.id}`, onclick: jump ? (e) => { e.preventDefault(); jump(f); } : null },
      h('img', { src: url, alt: f.caption || KINDS[f.kind], loading: 'lazy' })),
    h('div', { class: 'fig-body' },
      h('div', { class: 'fig-top' }, h('span', { class: `pill k-${f.kind}` }, KINDS[f.kind] || f.kind),
        h('span', { class: 'muted small' }, showPaper ? h('a', { href: `#/p/${f.paper_id}`, title: f.paper_title }, f.citekey) : null, ` p.${f.page}`),
        h('span', { class: 'grow' }),
        h('button', { class: 'icon-btn sm', 'aria-label': '更多', onclick: (e) => menu(e.currentTarget, [
          { label: '複製圖片', icon: 'copy', run: async () => {
            try { await navigator.clipboard.write([new ClipboardItem({ 'image/png': fetch(url, { credentials: 'same-origin' }).then((r) => r.blob()) })]); toast('已複製，可以直接貼到投影片或筆記'); } catch { toast('這個瀏覽器不支援複製圖片，請用「下載」', 'err'); }
          } },
          { label: '下載 PNG', icon: 'download', run: () => { location.href = `${url}?download=1`; } },
          f.latex ? { label: '複製 LaTeX', icon: 'copy', run: async () => { await navigator.clipboard.writeText(f.latex); toast('已複製 LaTeX'); } } : null,
          aiOn() && f.kind !== 'figure' ? { label: f.latex ? '重新轉 LaTeX（AI）' : '轉成 LaTeX（AI）', icon: 'spark', run: async () => {
            toast('AI 辨識中…');
            try { const r = await api.post(`/api/figures/${f.id}/latex`); f.latex = r.latex; onChange && onChange(); toast('已轉成 LaTeX'); } catch (e) { fail(e); }
          } } : null,
          can('annotate') ? { label: '編輯圖說與筆記', icon: 'pen', run: () => editFig(f, onChange) } : null,
          mine && can('annotate') ? { label: '刪除', icon: 'trash', danger: true, run: async () => { if (await confirmBox('刪除這張圖卡？', '刪除')) { await api.del(`/api/figures/${f.id}`); onChange && onChange(); } } } : null,
        ]) }, icon('more'))),
      f.note ? h('div', { class: 'fig-note' }, f.note) : null,
      f.caption ? h('div', { class: 'fig-cap', title: f.caption }, f.caption) : null,
      f.latex ? h('code', { class: 'formula', title: '點一下複製', onclick: async () => { await navigator.clipboard.writeText(f.latex); toast('已複製 LaTeX'); } }, f.latex) : null,
      h('div', { class: 'muted small' }, `${f.who || ''} · ${fmtDate(f.created_at)}`)));
  return card;
}

function editFig(f, done) {
  const kind = h('select', {}, Object.entries(KINDS).map(([k, v]) => h('option', { value: k, selected: f.kind === k }, v)));
  const cap = h('textarea', { rows: 3 }, f.caption || '');
  const note = h('textarea', { rows: 2, placeholder: '這張圖的重點、和我們實驗的關係…' }, f.note || '');
  const tex = h('textarea', { rows: 2, class: 'mono', placeholder: '公式的 LaTeX（選填）' }, f.latex || '');
  const md = modal('編輯圖卡', h('div', { class: 'stack' }, h('img', { class: 'fig-preview', src: `/api/figures/${f.id}/image` }),
    h('label', {}, '類型', kind), h('label', {}, '圖說', cap), h('label', {}, '筆記', note), h('label', {}, 'LaTeX', tex),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try { await api.patch(`/api/figures/${f.id}`, { kind: kind.value, caption: cap.value, note: note.value, latex: tex.value }); md.close(); toast('已儲存'); done && done(); } catch (e) { fail(e); }
    } }, '儲存'))), { wide: true });
  md.el.querySelectorAll('textarea').forEach(autoGrow);
}

// 框選完成 → 預覽、確認圖說 → 存成圖卡
export async function saveCrop(viewer, pid, fileId, sel, done) {
  const src = viewer.cropPreview(sel.page, sel.rect);
  const kind = h('select', {}, Object.entries(KINDS).map(([k, v]) => h('option', { value: k }, v)));
  const cap = h('textarea', { rows: 3, placeholder: '圖說（自動從 PDF 找「FIG. …」，找不到可以自己寫）' });
  const note = h('textarea', { rows: 2, placeholder: '筆記（選填）：這張圖要看什麼' });
  const saveBtn = h('button', { class: 'btn primary' }, icon('image'), '存成圖卡');
  const md = modal(`第 ${sel.page} 頁：存成圖卡`, h('div', { class: 'stack' },
    src ? h('img', { class: 'fig-preview', src, alt: '' }) : null,
    h('div', { class: 'grid2' }, h('label', {}, '類型', kind), h('span')),
    h('label', {}, '圖說', cap), h('label', {}, '筆記', note),
    h('p', { class: 'hint' }, '存檔時會用高解析度重新裁切，放大或放進投影片都清楚。'),
    h('div', { class: 'row end' }, saveBtn)), { wide: true });
  saveBtn.onclick = async () => {
    saveBtn.disabled = true;
    try {
      const f = await api.post(`/api/papers/${pid}/figures`, { file_id: fileId, page: sel.page, rect: sel.rect, kind: kind.value, caption: cap.value, note: note.value });
      md.close(); toast('已存成圖卡'); done && done(f);
    } catch (e) { fail(e); saveBtn.disabled = false; }
  };
  try {
    const pv = await api.post('/api/figures/preview', { file_id: fileId, page: sel.page, rect: sel.rect });
    if (!cap.value) { cap.value = pv.caption || ''; autoGrow(cap); }
    if (/^\s*(eq|equation)/i.test(pv.caption || '') || (sel.rect[3] < 0.08 && sel.rect[2] > 0.3)) kind.value = 'equation';
    if (/^\s*table/i.test(pv.caption || '')) kind.value = 'table';
  } catch { /* 只是猜圖說，失敗沒關係 */ }
}

// 論文頁的「圖卡」分頁
export async function figuresPanel(el, p, { jump, startCrop } = {}) {
  const r = await api.get(`/api/figures?paper=${p.id}&limit=200`);
  el.replaceChildren(
    h('div', { class: 'row wrap' },
      can('annotate') && startCrop ? h('button', { class: 'btn small primary', onclick: startCrop }, icon('crop'), '框選圖／公式') : null,
      h('span', { class: 'grow' }),
      r.items.length ? h('a', { class: 'btn small ghost', href: `/api/figures-zip?ids=${r.items.map((f) => f.id).join(',')}` }, icon('download'), '全部下載') : null),
    h('p', { class: 'hint' }, '在 PDF 上框出一張圖、一條公式或一個表格，存成圖卡（附頁碼與圖說）。圖卡可以複製貼上、做成投影片，全部圖卡在側欄「圖表剪貼簿」。'),
    r.items.length ? h('div', { class: 'fig-grid one' }, r.items.map((f) => figCard(f, { onChange: () => figuresPanel(el, p, { jump, startCrop }), jump })))
      : h('div', { class: 'empty small' }, icon('image', 'big'), h('p', {}, '還沒有圖卡。')));
  return r.items.length;
}

export async function viewFigures(view, params) {
  view.classList.add('page');
  const st = { kind: params.get('kind') || '', mine: params.get('mine') === '1', q: params.get('q') || '', sel: new Set(), selecting: false };
  const grid = h('div', { class: 'fig-grid' });
  const count = h('span', { class: 'muted' });
  const bar = h('div', { class: 'selbar', hidden: true });
  const jobEl = h('div', { class: 'tr-test' });
  async function load() {
    const q = new URLSearchParams({ limit: 200, kind: st.kind, mine: st.mine ? 1 : 0, q: st.q });
    const r = await api.get(`/api/figures?${q}`);
    count.textContent = `${r.total} 張`;
    grid.replaceChildren(...(r.items.length ? r.items.map((f) => figCard(f, { showPaper: true, onChange: load, sel: st.selecting ? st.sel : null, onSelect: drawBar }))
      : [h('div', { class: 'empty' }, icon('image', 'big'), h('p', {}, st.q || st.kind || st.mine ? '沒有符合的圖卡。' : '還沒有圖卡。打開一篇論文，按閱讀器上方的「框選」圖示，把圖或公式存下來。'))]));
    drawBar();
  }
  function drawBar() {
    bar.hidden = !st.selecting;
    const ids = [...st.sel];
    bar.replaceChildren(h('span', {}, `已選 ${ids.length} 張`),
      h('button', { class: 'btn small', onclick: () => { if (!ids.length) return toast('請先勾選'); location.href = `/api/figures-zip?ids=${ids.join(',')}`; } }, icon('download'), '下載 ZIP'),
      h('button', { class: 'btn small', onclick: async () => {
        if (!ids.length) return toast('請先勾選');
        const title = prompt('投影片標題', '圖表剪貼簿'); if (title == null) return;
        try { const r = await api.post('/api/figures/slides', { ids, title }); jobDownload(r.job, jobEl, '下載投影片'); } catch (e) { fail(e); }
      } }, icon('slides'), '做成投影片'),
      h('span', { class: 'grow' }), h('button', { class: 'btn small ghost', onclick: () => { st.selecting = false; st.sel.clear(); load(); } }, '完成'));
  }
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '圖表剪貼簿'),
      h('p', { class: 'muted' }, '大家從論文裡框下來的圖、公式與表格。點圖回到原文位置；勾選幾張可以一次下載或做成投影片。')), count),
    h('div', { class: 'toolbar' },
      h('div', { class: 'seg' }, [['', '全部'], ...Object.entries(KINDS)].map(([k, l]) => h('button', { class: st.kind === k ? 'on' : '', onclick: () => { st.kind = k; view.querySelectorAll('.toolbar .seg button').forEach((b, i) => b.classList.toggle('on', i === ['', ...Object.keys(KINDS)].indexOf(k))); load(); } }, l))),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: st.mine, onchange: (e) => { st.mine = e.target.checked; load(); } }), '只看我的'),
      h('input', { type: 'search', placeholder: '搜尋圖說、筆記、論文', value: st.q, oninput: debounce((e) => { st.q = e.target.value.trim(); load(); }, 250) }),
      h('span', { class: 'grow' }),
      h('button', { class: 'btn small', onclick: () => { st.selecting = !st.selecting; load(); } }, icon('check'), '選取')),
    jobEl, grid, bar);
  await load();
}

// ================================================================== 相似論文（關聯分頁）
export function similarBlock(pid, onLinked) {
  const box = h('div', { class: 'similar' }, h('h3', {}, icon('sim'), '相似的論文', h('small', { class: 'muted' }, ' 系統依內容推薦')), h('div', { class: 'muted small' }, h('span', { class: 'spinner sm' }), ' 計算中…'));
  api.get(`/api/papers/${pid}/similar`).then((rows) => {
    box.replaceChildren(h('h3', {}, icon('sim'), '相似的論文', h('small', { class: 'muted' }, rows[0]?.mode === 'embedding' ? ' 向量模型推薦' : ' 依內容關鍵詞推薦')),
      rows.length ? rows.map((r) => h('div', { class: 'link-item' },
        h('a', { href: `#/p/${r.id}` }, r.title, r.year ? ` (${r.year})` : ''),
        h('div', { class: 'muted small' }, r.why.length ? `共同：${r.why.join('、')}` : '', r.linked ? ' · 已有關聯' : '',
          can('link') && !r.linked ? h('button', { class: 'linkbtn', onclick: (e) => menu(e.currentTarget, S.rels.map((rel) => ({ label: `這篇 ${rel} → 對方`, run: async () => {
            try { await api.post('/api/links', { src_id: pid, dst_id: r.id, rel, note: '從相似論文建立' }); toast('已建立關聯'); onLinked && onLinked(); } catch (err) { fail(err); }
          } }))) }, '建立關聯') : null))) : h('p', { class: 'muted small' }, '論文庫裡還沒有夠相似的論文。'));
  }).catch(() => box.remove());
  return box;
}

// ================================================================== 預印本與重複論文
export function versionNotes(p, reload) {
  const out = [];
  for (const v of p.versions || []) {
    if (v.kind === 'published' && v.status === 'new') {
      out.push(h('div', { class: 'notice' }, icon('refresh'), h('div', { class: 'grow' }, h('b', {}, '找到正式發表版本'),
        h('div', { class: 'small' }, `${v.venue || ''} ${v.year || ''} · DOI ${v.doi}（${v.source}${v.score < 1 ? `，相似度 ${Math.round(v.score * 100)}%` : ''}）`)),
      can('edit_meta', p) ? h('button', { class: 'btn small primary', onclick: async () => { try { await api.post(`/api/versions/${v.id}/apply`); toast('已更新書目'); reload(); } catch (e) { fail(e); } } }, '更新書目') : null,
      h('button', { class: 'btn small ghost', onclick: async () => { await api.post(`/api/versions/${v.id}/dismiss`); reload(); } }, '不是這篇')));
    } else if (v.kind === 'published' && v.status === 'applied') {
      out.push(h('div', { class: 'notice ok' }, icon('check'), h('div', { class: 'grow small' }, `已正式發表：${v.venue || ''}（${fmtDate(v.resolved_at)} 自動更新，來源：${v.source}）`)));
    } else if (v.kind === 'duplicate' && v.status === 'new') {
      out.push(h('div', { class: 'notice warn' }, icon('merge'), h('div', { class: 'grow small' }, '論文庫裡可能有同一篇的另一個版本（預印本／正式版）。'),
        h('a', { class: 'btn small', href: '#/versions' }, '查看')));
    }
  }
  return out;
}

export async function viewVersions(view) {
  view.classList.add('page');
  const r = await api.get('/api/versions');
  const jobEl = h('div', { class: 'tr-test' });
  const mini = (p, keep, name) => h('label', { class: `dup-side ${keep ? 'keep' : ''}` },
    h('input', { type: 'radio', name, value: p.id, checked: keep }),
    h('div', {}, h('a', { href: `#/p/${p.id}`, target: '_blank' }, h('b', {}, p.title)),
      h('div', { class: 'muted small' }, [p.citekey, p.year, p.venue].filter(Boolean).join(' · ')),
      h('div', { class: 'small' }, p.doi ? `DOI ${p.doi}` : '沒有 DOI', p.arxiv ? ` · arXiv ${p.arxiv}` : '', p.kind === 'preprint' ? ' · 預印本' : ''),
      h('div', { class: 'muted small' }, `${p.n_files} 個檔案 · ${p.n_ann} 則標註`)));
  const dupCard = (d, i) => {
    const name = `keep-${i}`;
    const el = h('div', { class: 'card-form dup' },
      h('div', { class: 'muted small' }, `${d.source}${d.score < 1 ? `（標題相似度 ${Math.round(d.score * 100)}%）` : ''}　·　選要保留為主的那篇：`),
      h('div', { class: 'dup-pair' }, mini(d.a, d.keep === d.a.id, name), mini(d.b, d.keep === d.b.id, name)),
      h('div', { class: 'row end' },
        h('button', { class: 'btn small ghost', onclick: async () => { await api.post(`/api/versions/${d.id}/dismiss`); route(); } }, '不是重複'),
        canManage() ? h('button', { class: 'btn small primary', onclick: async () => {
          const keep = +el.querySelector(`input[name=${name}]:checked`).value;
          const drop = keep === d.a.id ? d.b.id : d.a.id;
          if (!await confirmBox('合併後另一篇的 PDF 會變成「其他版本」，標註、圖卡、參數、關聯、分類、標籤、閱讀狀態、指派都會搬到保留的這篇。被合併那篇的資料備份在 NAS 的 trash 資料夾。', '合併')) return;
          try { await api.post('/api/papers/merge', { keep, drop }); toast('已合併'); refreshNav(); route(); } catch (e) { fail(e); }
        } }, icon('merge'), '合併') : null));
    return el;
  };
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '預印本與重複論文'),
      h('p', { class: 'muted' }, '系統每週檢查只有 arXiv 編號的論文是否已正式發表（arXiv 記錄的 DOI，或用標題＋作者在 Crossref 比對），也會找出論文庫裡重複的論文（同一篇的預印本與正式版、相同 DOI／arXiv、標題幾乎相同）。')),
      canManage() ? h('button', { class: 'btn small', onclick: async () => { const x = await api.post('/api/versions/check'); watchJob(x.job, jobEl, () => route()); } }, icon('refresh'), '立即檢查') : null),
    jobEl,
    h('h2', {}, `可能重複的論文（${r.duplicates.length}）`),
    r.duplicates.length ? r.duplicates.map(dupCard) : h('p', { class: 'muted' }, '沒有待處理的重複論文。'),
    h('h2', {}, `找到正式版、待確認（${r.published.length}）`),
    r.published.length ? r.published.map((v) => h('div', { class: 'card-form row wrap' },
      h('div', { class: 'grow' }, h('a', { href: `#/p/${v.paper.id}` }, h('b', {}, v.paper.title)), h('div', { class: 'small' }, `→ ${v.venue || ''} ${v.year || ''} · DOI ${v.doi}`),
        h('div', { class: 'muted small' }, `${v.source}${v.score < 1 ? `，標題相似度 ${Math.round(v.score * 100)}%` : ''}${v.title ? `；Crossref 標題：${v.title}` : ''}`)),
      h('button', { class: 'btn small primary', onclick: async () => { try { await api.post(`/api/versions/${v.id}/apply`); toast('已更新'); route(); } catch (e) { fail(e); } } }, '更新書目'),
      h('button', { class: 'btn small ghost', onclick: async () => { await api.post(`/api/versions/${v.id}/dismiss`); route(); } }, '不是這篇'))) : h('p', { class: 'muted' }, '沒有待確認的項目。'),
    r.applied.length ? h('details', { class: 'card-form' }, h('summary', {}, `最近自動更新（${r.applied.length}）`),
      r.applied.map((v) => h('div', { class: 'small pad-s' }, h('a', { href: `#/p/${v.paper.id}` }, v.paper.citekey), ` → ${v.venue}（${fmtDate(v.resolved_at)}，${v.source}）`))) : null);
}

// ================================================================== 入門路徑
const pbar = (n, t) => h('span', { class: 'prog' }, h('span', { style: { width: `${t ? (100 * n) / t : 0}%` } }));

export async function viewPaths(view) {
  view.classList.add('page');
  const list = await api.get('/api/paths');
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '入門路徑'),
      h('p', { class: 'muted' }, '把論文排成有順序的閱讀路線，每篇附上「讀這篇要看懂什麼」。加入路徑的人，路徑上的論文會列入他的待讀；進度用每個人自己的「已閱讀」計算，路徑成員的進度對大家公開。')),
      can('meeting') ? h('button', { class: 'btn primary small', onclick: () => newPath() }, icon('plus'), '新增路徑') : null),
    list.length ? h('div', { class: 'path-grid' }, list.map((p) => h('a', { class: 'path-card', href: `#/paths/${p.id}` },
      h('div', { class: 'row' }, icon('route'), h('b', { class: 'grow' }, p.title), p.joined ? h('span', { class: 'pill' }, '我在這條路徑') : null),
      p.description ? h('p', { class: 'muted small' }, p.description) : null,
      h('div', { class: 'row' }, pbar(p.my_done, p.n_items), h('span', { class: 'small' }, `我 ${p.my_done}/${p.n_items}`)),
      p.next && p.joined ? h('div', { class: 'small' }, '下一篇：', h('b', {}, p.next.title)) : null,
      p.members.length ? h('div', { class: 'chips wrap' }, p.members.map((m) => h('span', { class: 'chip' }, `${m.name} ${m.done}/${p.n_items}`))) : h('div', { class: 'muted small' }, '還沒有人加入')))) :
      h('div', { class: 'empty' }, icon('route', 'big'), h('p', {}, '還沒有入門路徑。'), can('meeting') ? h('button', { class: 'btn primary', onclick: () => newPath() }, '建立第一條') : null));
}

function newPath() {
  const title = h('input', { placeholder: '例如：新人第一個月、Level attraction 專題' });
  const desc = h('textarea', { rows: 2, placeholder: '說明（選填）：適合誰、預計多久讀完' });
  const cat = h('select', {}, h('option', { value: '' }, '整個論文庫'), S.cats.map((c) => h('option', { value: c.id }, c.name)));
  const draft = h('div', { class: 'stack' });
  let items = [];
  const drawDraft = () => draft.replaceChildren(...items.map((it, i) => h('div', { class: 'draft-item' }, h('span', { class: 'step-no' }, i + 1),
    h('div', { class: 'grow' }, h('b', {}, it.title), h('input', { value: it.goal, placeholder: '讀這篇要看懂什麼', oninput: (e) => { it.goal = e.target.value; } })),
    h('button', { class: 'icon-btn sm', 'aria-label': '移除', onclick: () => { items.splice(i, 1); drawDraft(); } }, icon('x')))));
  const md = modal('新增入門路徑', h('div', { class: 'stack' },
    h('label', {}, '名稱', title), h('label', {}, '說明', desc),
    aiOn() ? h('div', { class: 'card-form stack' }, h('b', {}, icon('spark'), ' 讓 AI 起草（選填）'),
      h('p', { class: 'muted small' }, 'AI 依重點欄從分類裡挑 4–8 篇、由淺入深排序並寫閱讀目標；之後可以再調整。'),
      h('div', { class: 'row' }, cat, h('button', { class: 'btn small', onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { items = await api.post('/api/paths/draft', { cat: +cat.value || null, topic: title.value }); drawDraft(); } catch (err) { fail(err); }
        e.target.closest('button').disabled = false;
      } }, '起草'))) : null,
    draft,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      if (!title.value.trim()) return toast('請填名稱');
      try { const p = await api.post('/api/paths', { title: title.value, description: desc.value, items }); md.close(); location.hash = `#/paths/${p.id}`; } catch (e) { fail(e); }
    } }, '建立'))), { wide: true });
}

export async function viewPath(view, id) {
  view.classList.add('page');
  const p = await api.get(`/api/paths/${id}`);
  const edit = can('meeting');
  const n = p.items.length;
  const me = p.members.find((m) => m.user_id === S.user.id);
  const myDone = p.items.filter((i) => i.my_status === '已閱讀').length;
  const reload = () => route();
  const setStatus = async (it, status) => { await api.patch(`/api/papers/${it.paper_id}`, { status: status || null }); refreshNav(); reload(); };
  const move = async (i, d) => { const ids = p.items.map((x) => x.id); const j = i + d; if (j < 0 || j >= ids.length) return; [ids[i], ids[j]] = [ids[j], ids[i]]; await api.post(`/api/paths/${id}/order`, { ids }); reload(); };
  const step = (it, i) => {
    const done = it.my_status === '已閱讀';
    return h('li', { class: `step ${done ? 'done' : ''} ${it.my_status === '閱讀中' ? 'doing' : ''}` },
      h('span', { class: 'step-no' }, done ? '✓' : i + 1),
      h('div', { class: 'grow' },
        h('a', { href: `#/p/${it.paper_id}` }, h('b', {}, it.title)), h('span', { class: 'muted small' }, ` ${it.citekey}${it.year ? ` · ${it.year}` : ''}`),
        h('div', { class: 'goal' }, icon('flag'), it.goal || h('span', { class: 'muted' }, '（還沒寫閱讀目標）'),
          edit ? h('button', { class: 'linkbtn', onclick: async () => { const g = prompt('讀這篇要看懂什麼？', it.goal || ''); if (g != null) { await api.patch(`/api/path-items/${it.id}`, { goal: g }); reload(); } } }, '修改') : null),
        h('div', { class: 'row wrap step-members' }, p.members.map((m) => h('span', { class: `at-name ${m.done.includes(it.paper_id) ? 'done' : m.reading.includes(it.paper_id) ? 'doing' : ''}`, title: m.done.includes(it.paper_id) ? '已讀完' : m.reading.includes(it.paper_id) ? '閱讀中' : '還沒讀' },
          m.display_name, m.done.includes(it.paper_id) ? ' ✓' : m.reading.includes(it.paper_id) ? ' …' : '')))),
      h('select', { class: 'status-sel', title: '你的閱讀狀態（完成＝已閱讀）', onchange: (e) => setStatus(it, e.target.value) },
        h('option', { value: '' }, '未標記'), S.site.statuses.map((s) => h('option', { value: s, selected: it.my_status === s }, s))),
      edit ? h('button', { class: 'icon-btn sm', 'aria-label': '更多', onclick: (e) => menu(e.currentTarget, [
        { label: '往前移', icon: 'up', run: () => move(i, -1) }, { label: '往後移', run: () => move(i, 1) },
        { label: '從路徑移除', icon: 'trash', danger: true, run: async () => { await api.del(`/api/path-items/${it.id}`); reload(); } }]) }, icon('more')) : null);
  };
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {},
      h('div', { class: 'muted small' }, h('a', { href: '#/paths' }, '入門路徑'), ' ／'),
      h('h1', {}, icon('route'), ' ', p.title), p.description ? h('p', { class: 'muted' }, p.description) : null,
      h('div', { class: 'row' }, pbar(myDone, n), h('span', { class: 'small' }, `我讀完 ${myDone}/${n}`))),
      h('div', { class: 'row wrap' },
        me ? h('button', { class: 'btn small ghost', onclick: async () => { if (await confirmBox('退出這條路徑？（已讀的狀態不會改變）', '退出')) { await api.del(`/api/paths/${id}/members/${S.user.id}`); reload(); } } }, '退出路徑')
          : h('button', { class: 'btn small primary', onclick: async () => { await api.post(`/api/paths/${id}/join`); toast('已加入，路徑上的論文列入你的待讀'); refreshNav(); reload(); } }, icon('plus'), '加入這條路徑'),
        edit ? h('button', { class: 'btn small', onclick: () => assignPath(p, reload) }, icon('at'), '安排成員') : null,
        edit ? h('button', { class: 'icon-btn', 'aria-label': '更多', onclick: (e) => menu(e.currentTarget, [
          { label: '改名稱／說明', icon: 'pen', run: async () => { const t = prompt('名稱', p.title); if (t == null) return; const d = prompt('說明', p.description || ''); await api.patch(`/api/paths/${id}`, { title: t, description: d ?? p.description }); reload(); } },
          { label: '刪除路徑', icon: 'trash', danger: true, run: async () => { if (await confirmBox(`刪除「${p.title}」？（論文和閱讀狀態不受影響）`, '刪除')) { await api.del(`/api/paths/${id}`); location.hash = '#/paths'; } } }]) }, icon('more')) : null)),
    n ? h('ol', { class: 'steps' }, p.items.map(step)) : h('p', { class: 'muted' }, '路徑上還沒有論文。'),
    edit ? h('div', { class: 'card-form row wrap' }, lookupInput(async (r) => {
      const goal = prompt(`「${r.title}」：讀這篇要看懂什麼？（可空白）`, '');
      if (goal == null) return;
      try { await api.post(`/api/paths/${id}/items`, { paper_id: r.id, goal }); reload(); } catch (e) { fail(e); }
    }, { placeholder: '加入論文到路徑最後…', exclude: p.items.map((i) => i.paper_id) })) : null,
    p.members.length ? h('section', {}, h('h2', {}, `成員進度（${p.members.length}）`),
      h('div', { class: 'stack' }, p.members.map((m) => h('div', { class: 'row member-prog' }, h('b', { class: 'req-name' }, m.display_name), pbar(m.done.length, n),
        h('span', { class: 'small' }, `${m.done.length}/${n}`),
        m.next ? h('span', { class: 'muted small grow' }, '下一篇：', p.items.find((x) => x.paper_id === m.next)?.citekey || '') : h('span', { class: 'ok small grow' }, '完成 ✓'),
        edit && m.user_id !== S.user.id ? h('button', { class: 'linkbtn', onclick: async () => { await api.del(`/api/paths/${id}/members/${m.user_id}`); reload(); } }, '移出') : null)))) : null);
}

async function assignPath(p, done) {
  const users = (await api.get('/api/users')).filter((u) => !u.disabled);
  const chosen = new Set();
  const inPath = new Set(p.members.map((m) => m.user_id));
  const md = modal(`安排成員：${p.title}`, h('div', { class: 'stack' },
    h('p', { class: 'muted small' }, '被安排的人會收到通知，路徑上的論文列入他的待讀。'),
    h('div', { class: 'chips wrap' }, users.map((u) => h('button', { class: `chip toggle at-chip ${inPath.has(u.id) ? 'on' : ''}`, disabled: inPath.has(u.id),
      onclick: (e) => { chosen.has(u.id) ? chosen.delete(u.id) : chosen.add(u.id); e.currentTarget.classList.toggle('on'); } }, `@${u.display_name}`))),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      if (!chosen.size) return toast('請至少選一位');
      try { await api.post(`/api/paths/${p.id}/members`, { user_ids: [...chosen] }); md.close(); toast('已安排'); done(); } catch (e) { fail(e); }
    } }, '安排'))));
}

// ================================================================== 組會投影片
export async function slidesDialog({ meeting = null, paper = null }) {
  const body = h('div', { class: 'stack' }, h('div', { class: 'spinner' }));
  const md = modal(meeting ? `產生投影片：${meeting.date} ${meeting.title}` : `產生投影片：${paper.citekey}`, body, { wide: true });
  let items;
  if (meeting) items = (await api.get(`/api/meetings/${meeting.id}/slides/options`)).items.filter((i) => i.paper_id);
  else items = [{ paper_id: paper.id, citekey: paper.citekey, title: paper.title, figures: (await api.get(`/api/figures?paper=${paper.id}&limit=200`)).items, has_keyinfo: Object.keys(paper.keyinfo || {}).length > 0 }];
  const picks = {};
  const mode = h('div', { class: 'seg' });
  let useAi = false;
  const drawMode = () => mode.replaceChildren(
    h('button', { class: !useAi ? 'on' : '', onclick: () => { useAi = false; drawMode(); } }, '直接用重點欄（立即、免費）'),
    aiOn() ? h('button', { class: useAi ? 'on' : '', onclick: () => { useAi = true; drawMode(); } }, icon('spark'), 'AI 精簡成要點') : null);
  drawMode();
  const jobEl = h('div', { class: 'tr-test' });
  body.replaceChildren(
    h('p', { class: 'hint' }, '每篇論文會有：標題頁（含第一頁預覽）、重點與架設、參數表、圖卡、可萃取特徵與和我們的關係、大家標成「疑問」的標註。產生的是 PowerPoint 檔，可以再自由修改；完整重點欄放在講者備忘稿。'),
    h('div', {}, h('b', {}, '內容來源 '), mode),
    ...items.map((it) => {
      picks[it.paper_id] = new Set(it.figures.filter((f) => f.kind === 'figure').slice(0, 3).map((f) => f.id));
      return h('div', { class: 'card-form' },
        h('div', { class: 'row' }, h('b', { class: 'grow' }, it.title), h('span', { class: 'muted small' }, it.presenter ? `報告：${it.presenter}` : ''),
          !it.has_keyinfo ? h('span', { class: 'pill warn', title: '沒有重點欄的論文，投影片會比較空' }, '重點欄空白') : null),
        it.figures.length ? h('div', { class: 'fig-pick' }, it.figures.map((f) => h('label', { class: 'fig-pick-item', title: f.caption },
          h('input', { type: 'checkbox', checked: picks[it.paper_id].has(f.id), onchange: (e) => { e.target.checked ? picks[it.paper_id].add(f.id) : picks[it.paper_id].delete(f.id); } }),
          h('img', { src: `/api/figures/${f.id}/image`, loading: 'lazy', alt: '' }), h('span', { class: 'small' }, `p.${f.page}`))))
          : h('p', { class: 'muted small' }, '這篇還沒有圖卡（在論文頁框選圖，就能放進投影片）。'));
    }),
    jobEl,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async (e) => {
      e.currentTarget.disabled = true;
      const figs = Object.fromEntries(Object.entries(picks).map(([k, v]) => [k, [...v]]));
      try {
        const r = meeting ? await api.post(`/api/meetings/${meeting.id}/slides`, { ai: useAi, figs })
          : await api.post(`/api/papers/${paper.id}/slides`, { ai: useAi, fig_ids: figs[paper.id] });
        jobDownload(r.job, jobEl, '下載投影片（.pptx）');
      } catch (err) { fail(err); }
    } }, icon('slides'), '產生投影片')));
  void md;
}

// ================================================================== 實驗室動態看板
function barChart(rows, { label, value, fmt = (v) => v, height = 150, unit = '' }) {
  // 單一數列的直條圖（一個顏色；滑鼠移上去顯示數值）
  const max = Math.max(1, ...rows.map(value));
  const W = Math.max(280, rows.length * 34), H = height, pad = 22;
  const bw = Math.min(26, (W - 10) / rows.length - 6);
  const tip = h('div', { class: 'chart-tip', hidden: true });
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H + pad}`);
  svg.setAttribute('class', 'chart');
  svg.setAttribute('role', 'img');
  const ticks = [0, Math.round(max / 2), max];
  let inner = ticks.map((t) => { const y = H - (t / max) * (H - 12); return `<line x1="0" x2="${W}" y1="${y}" y2="${y}" class="grid"/><text x="0" y="${y - 3}" class="axis">${t}</text>`; }).join('');
  rows.forEach((r, i) => {
    const v = value(r), x = 10 + i * ((W - 10) / rows.length) + ((W - 10) / rows.length - bw) / 2;
    const bh = (v / max) * (H - 12), y = H - bh;
    inner += `<rect x="${x}" y="${y}" width="${bw}" height="${Math.max(0, bh)}" rx="4" class="bar" data-i="${i}"/>`;
    inner += `<rect x="${x - 3}" y="0" width="${bw + 6}" height="${H}" fill="transparent" data-i="${i}" class="hit"/>`;
    if (rows.length <= 14 || i % 2 === 0) inner += `<text x="${x + bw / 2}" y="${H + 15}" text-anchor="middle" class="axis">${esc(label(r))}</text>`;
  });
  svg.innerHTML = inner;
  svg.addEventListener('pointermove', (e) => {
    const i = e.target.dataset?.i;
    if (i == null) { tip.hidden = true; return; }
    const r = rows[+i];
    tip.hidden = false;
    tip.textContent = `${label(r)}：${fmt(value(r))}${unit}`;
    const box = svg.getBoundingClientRect();
    tip.style.left = `${e.clientX - box.left + 10}px`; tip.style.top = `${e.clientY - box.top - 30}px`;
  });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; });
  return h('div', { class: 'chart-wrap' }, svg, tip);
}

function hbars(rows, { label, value, href, marker }) {
  const max = Math.max(1, ...rows.map(value));
  return h('div', { class: 'hbars' }, rows.map((r) => h('a', { class: 'hbar', href: href ? href(r) : null, title: `${label(r)}：${value(r)}` },
    h('span', { class: 'hb-label' }, marker ? marker(r) : null, label(r)),
    h('span', { class: 'hb-track' }, h('span', { class: 'hb-fill', style: { width: `${(100 * value(r)) / max}%` } })),
    h('span', { class: 'hb-val' }, value(r)))));
}

export async function viewDashboard(view, params) {
  view.classList.add('page');
  const days = +(params.get('days') || 30);
  const d = await api.get(`/api/dashboard?days=${days}`);
  const tile = (label, pair, href) => {
    const diff = pair.now - pair.prev;
    return h('a', { class: 'stat', href: href || null },
      h('div', { class: 'stat-label' }, label), h('div', { class: 'stat-num' }, pair.now),
      h('div', { class: `stat-delta ${diff > 0 ? 'up' : diff < 0 ? 'down' : ''}` }, diff === 0 ? '和前期相同' : `${diff > 0 ? '▲' : '▼'} ${Math.abs(diff)}（前期 ${pair.prev}）`));
  };
  const reqPct = d.required.papers && d.required.users ? Math.round((100 * d.required.done) / (d.required.papers * d.required.users)) : null;
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '實驗室動態'),
      h('p', { class: 'muted' }, '論文庫的活動統計。只計算公開的內容（上傳、公開標註、討論、圖卡、關聯）；個人閱讀狀態是私人的，不列入個人統計。')),
      h('div', { class: 'seg' }, [[30, '30 天'], [90, '90 天'], [365, '一年']].map(([k, l]) => h('a', { class: `btn small ${days === k ? 'on' : ''}`, href: `#/dashboard?days=${k}` }, l)))),
    h('div', { class: 'stats' },
      tile('新增論文', d.totals.papers, '#/all'), tile('公開標註', d.totals.annotations), tile('討論回覆', d.totals.replies),
      tile('圖卡', d.totals.figures, '#/figures'), tile('手動關聯', d.totals.links, '#/graph')),
    h('div', { class: 'dash-grid' },
      h('section', { class: 'dash-card' }, h('h3', {}, '每月新增論文'), barChart(d.months, { label: (r) => `${+r.month.slice(5)}月`, value: (r) => r.papers, unit: ' 篇' })),
      h('section', { class: 'dash-card' }, h('h3', {}, '每月公開標註'), barChart(d.months, { label: (r) => `${+r.month.slice(5)}月`, value: (r) => r.annotations, unit: ' 則' })),
      h('section', { class: 'dash-card' }, h('h3', {}, `成員貢獻（近 ${days} 天）`),
        h('table', { class: 'dtable' }, h('thead', {}, h('tr', {}, ['成員', '上傳', '標註', '回覆', '圖卡', '關聯'].map((x) => h('th', {}, x)))),
          h('tbody', {}, d.members.map((m) => h('tr', {}, h('td', {}, m.name), ...['uploads', 'annotations', 'replies', 'figures', 'links'].map((k) => h('td', { class: m[k] ? '' : 'muted' }, m[k]))))))),
      h('section', { class: 'dash-card' }, h('h3', {}, `最常被打開的論文（近 ${days} 天）`),
        d.top_opened.length ? h('ol', { class: 'rank' }, d.top_opened.map((p) => h('li', {}, h('a', { href: `#/p/${p.id}` }, p.title), h('span', { class: 'muted small' }, ` ${p.people} 人 · ${p.n} 次`))))
          : h('p', { class: 'muted small' }, '還沒有紀錄。')),
      h('section', { class: 'dash-card' }, h('h3', {}, '熱門討論'),
        d.hot.length ? d.hot.map((x) => h('a', { class: 'hot', href: `#/p/${x.paper_id}/ann?ann=${x.ann_id}` }, h('b', {}, `${x.n} 則回覆`), ' ', x.citekey, h('div', { class: 'muted small' }, (x.body || x.quote || '').slice(0, 90))))
          : h('p', { class: 'muted small' }, '這段期間沒有討論。')),
      h('section', { class: 'dash-card' }, h('h3', {}, '閱讀進度'),
        d.required.papers ? h('div', { class: 'stack' }, h('div', {}, h('b', {}, '全站必讀'), h('span', { class: 'muted small' }, ` ${d.required.papers} 篇 × ${d.required.users} 人`)),
          h('div', { class: 'row' }, pbar(reqPct, 100), h('span', { class: 'small' }, `${reqPct}%`))) : null,
        d.assign.total ? h('div', { class: 'stack' }, h('b', {}, '指派完成率'), h('div', { class: 'row' }, pbar(d.assign.done, d.assign.total), h('span', { class: 'small' }, `${d.assign.done}/${d.assign.total}`))) : null,
        d.paths.map((p) => h('div', { class: 'stack' }, h('a', { href: `#/paths/${p.id}` }, h('b', {}, p.title)),
          p.members.length ? p.members.map((m) => h('div', { class: 'row small' }, h('span', { class: 'req-name' }, m.name), pbar(m.done, p.n_items), `${m.done}/${p.n_items}`)) : h('span', { class: 'muted small' }, '沒有成員'))),
        !d.required.papers && !d.assign.total && !d.paths.length ? h('p', { class: 'muted small' }, '還沒有必讀、指派或入門路徑。') : null),
      h('section', { class: 'dash-card' }, h('h3', {}, '各分類論文數'),
        hbars(d.categories.filter((c) => c.n), { label: (c) => c.name, value: (c) => c.n, href: (c) => `#/c/${c.id}`, marker: (c) => h('span', { class: 'dot', style: { background: c.color } }) })),
      h('section', { class: 'dash-card' }, h('h3', {}, '論文發表年份'), barChart(d.years, { label: (r) => String(r.year).slice(2), value: (r) => r.n, unit: ' 篇', fmt: (v) => v })),
      h('section', { class: 'dash-card' }, h('h3', {}, '論文庫'),
        h('div', { class: 'kv-list' }, [['論文', d.library.papers], ['公開標註', d.library.annotations], ['圖卡', d.library.figures], ['關聯', d.library.links], ['已填參數的論文', d.library.with_params]].map(([k, v]) => h('div', {}, h('span', { class: 'muted' }, k), h('b', {}, v)))))));
}


// ================================================================== 期刊搜尋
const JS_KEY = 'pl-jsearch';
const RANGES = [[30, '近 1 個月'], [90, '近 3 個月'], [182, '近半年'], [365, '近 1 年'], [1095, '近 3 年'], [1825, '近 5 年'], [0, '自訂']];

export async function viewJournalSearch(view, params) {
  view.classList.add('page');
  const meta = await api.get('/api/journal-search/meta');
  let saved = {}; try { saved = JSON.parse(localStorage.getItem(JS_KEY) || '{}'); } catch { /* */ }
  const feedId = +(params.get('feed') || 0) || null;
  let feed = null;
  if (feedId) { feed = (await api.get('/api/feeds')).feeds.find((f) => f.id === feedId) || null; if (feed) saved = { ...feed.config }; }
  const st = { journals: new Set(saved.journals || meta.default), keywords: [...(saved.keywords || [])], mode: saved.mode === 'and' ? 'and' : 'or',
    exclude: saved.exclude || [], days: saved.days ?? 365, from: saved.from || '', to: saved.to || '', kwFilter: '', kwMore: false,
    results: null, sel: new Set(), hideLib: false };
  const persist = () => { try { localStorage.setItem(JS_KEY, JSON.stringify({ journals: [...st.journals], keywords: st.keywords, mode: st.mode, exclude: st.exclude, days: st.days, from: st.from, to: st.to })); } catch { /* */ } };
  const counts = Object.fromEntries(meta.keywords.map((k) => [k.kw, k.n]));
  const form = h('aside', { class: 'js-form' });
  const results = h('section', { class: 'js-results' });

  // ---- 期刊
  function journalBox() {
    const groups = {};
    meta.journals.forEach((j) => (groups[j.group] ||= []).push(j));
    return h('div', { class: 'js-sec' }, h('h3', {}, '期刊', h('span', { class: 'muted small' }, ` 已選 ${st.journals.size}`)),
      ...Object.entries(groups).map(([g, js]) => h('div', { class: 'js-group' },
        h('div', { class: 'js-ghead' }, h('b', {}, g),
          h('button', { class: 'linkbtn', onclick: () => { js.forEach((j) => st.journals.add(j.key)); draw(); } }, '全選'),
          h('button', { class: 'linkbtn', onclick: () => { js.forEach((j) => st.journals.delete(j.key)); draw(); } }, '清除')),
        h('div', { class: 'chips wrap' }, js.map((j) => h('button', { class: `chip toggle ${st.journals.has(j.key) ? 'on' : ''}`, title: `ISSN ${j.issn.join(', ')}`,
          onclick: () => { st.journals.has(j.key) ? st.journals.delete(j.key) : st.journals.add(j.key); draw(); } }, j.name,
          j.custom && canManage() ? h('span', { class: 'chip-x', title: '移除這本自訂期刊', onclick: async (e) => { e.stopPropagation(); if (await confirmBox(`移除自訂期刊「${j.name}」？`, '移除')) { await api.del(`/api/journal-search/journals/${j.key}`); route(); } } }, ' ×') : null))))),
      canManage() ? h('button', { class: 'btn small ghost', onclick: async () => {
        const name = prompt('期刊名稱（例如 Optics Express）'); if (!name) return;
        const issn = prompt('ISSN（印刷版與電子版都填更保險，用逗號分隔；可在期刊網站或 portal.issn.org 查）'); if (!issn) return;
        try { await api.post('/api/journal-search/journals', { name, issn }); toast('已新增'); route(); } catch (e) { fail(e); }
      } }, icon('plus'), '新增其他期刊') : null);
  }

  // ---- 關鍵字（論文庫裡出現的篇數，由多到少）
  function keywordBox() {
    const chosen = new Set(st.keywords);
    let list = meta.keywords.filter((k) => !st.kwFilter || k.kw.includes(st.kwFilter));
    const total = list.length;
    if (!st.kwMore && !st.kwFilter) list = list.slice(0, 40);
    const max = Math.max(1, ...meta.keywords.map((k) => k.n));
    const custom = h('input', { placeholder: '自己輸入關鍵字，按 Enter 加入', onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); const v = e.target.value.trim().toLowerCase(); if (v && !chosen.has(v)) { st.keywords.push(v); draw(); } } } });
    return h('div', { class: 'js-sec' }, h('h3', {}, '關鍵字', h('span', { class: 'muted small' }, ' 依論文庫中出現的篇數排序')),
      st.keywords.length ? h('div', { class: 'chips wrap js-chosen' }, st.keywords.map((k) => h('span', { class: 'chip on' }, k, counts[k] ? h('span', { class: 'muted' }, ` ${counts[k]}`) : null,
        h('button', { class: 'chip-x', 'aria-label': `移除 ${k}`, onclick: () => { st.keywords = st.keywords.filter((x) => x !== k); draw(); } }, '×'))),
        h('button', { class: 'linkbtn', onclick: () => { st.keywords = []; draw(); } }, '全部清除')) : h('p', { class: 'muted small' }, '勾選下面的關鍵字，或自己輸入。'),
      h('input', { type: 'search', placeholder: '篩選關鍵字…', value: st.kwFilter, oninput: debounce((e) => { st.kwFilter = e.target.value.trim().toLowerCase(); draw(true); }, 200) }),
      h('div', { class: 'kw-list' }, list.map((k) => h('label', { class: `kw-row ${chosen.has(k.kw) ? 'on' : ''}` },
        h('input', { type: 'checkbox', checked: chosen.has(k.kw), onchange: (e) => { if (e.target.checked) st.keywords.push(k.kw); else st.keywords = st.keywords.filter((x) => x !== k.kw); draw(true); } }),
        h('span', { class: 'kw-text' }, k.kw),
        h('span', { class: 'kw-bar' }, h('span', { style: { width: `${(100 * k.n) / max}%` } })),
        h('span', { class: 'kw-n' }, k.n)))),
      !st.kwFilter && total > 40 ? h('button', { class: 'linkbtn', onclick: () => { st.kwMore = !st.kwMore; draw(true); } }, st.kwMore ? '只顯示前 40 個' : `顯示全部 ${total} 個`) : null,
      custom);
  }

  // ---- 篩選方式、排除、時間
  function optionsBox() {
    const exclude = h('input', { value: st.exclude.join(', '), placeholder: '排除（選填，逗號分隔），例如 spintronic, antiferromagnet', onchange: (e) => { st.exclude = e.target.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean); persist(); } });
    const custom = st.days === 0;
    return h('div', { class: 'js-sec' },
      h('h3', {}, '篩選方式'),
      h('div', { class: 'seg' }, [['and', '交集（全部都要有）'], ['or', '聯集（有任一個就算）']].map(([k, l]) => h('button', { class: st.mode === k ? 'on' : '', onclick: () => { st.mode = k; draw(); } }, l))),
      h('p', { class: 'hint' }, st.mode === 'and' ? `標題或摘要要同時出現：${st.keywords.join(' 且 ') || '（選好的關鍵字）'}` : `標題或摘要出現任一個：${st.keywords.join(' 或 ') || '（選好的關鍵字）'}`),
      exclude,
      h('h3', {}, '時間範圍（發表日期）'),
      h('div', { class: 'chips wrap' }, RANGES.map(([d, l]) => h('button', { class: `chip toggle ${st.days === d ? 'on' : ''}`, onclick: () => { st.days = d; if (d === 0 && !st.from) { const x = new Date(); x.setFullYear(x.getFullYear() - 1); st.from = x.toISOString().slice(0, 10); } draw(); } }, l))),
      custom ? h('div', { class: 'grid2' },
        h('label', {}, '從', h('input', { type: 'date', value: st.from, onchange: (e) => { st.from = e.target.value; persist(); } })),
        h('label', {}, '到（留空＝今天）', h('input', { type: 'date', value: st.to, onchange: (e) => { st.to = e.target.value; persist(); } }))) : null);
  }

  function draw(keepScroll) {
    persist();
    const y = form.scrollTop;
    form.replaceChildren(
      journalBox(), keywordBox(), optionsBox(),
      h('div', { class: 'js-actions' },
        h('button', { class: 'btn primary', onclick: () => run() }, icon('search'), '搜尋'),
        can('manage') ? h('button', { class: 'btn', onclick: () => saveFeed() }, icon('rss'), feed ? `更新追蹤「${feed.name}」` : '存成自動追蹤') : null));
    if (keepScroll) form.scrollTop = y;
  }

  async function run() {
    if (!st.keywords.length) return toast('請至少選一個關鍵字');
    if (!st.journals.size) return toast('請至少選一本期刊');
    results.replaceChildren(h('div', { class: 'empty' }, h('div', { class: 'spinner' }), h('p', {}, '搜尋中…')));
    if (window.innerWidth < 900) results.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try {
      st.results = await api.post('/api/journal-search', { journals: [...st.journals], keywords: st.keywords, mode: st.mode, exclude: st.exclude,
        days: st.days || null, date_from: st.days ? null : st.from, date_to: st.days ? null : st.to });
      st.sel.clear();
      drawResults();
    } catch (e) { results.replaceChildren(h('div', { class: 'empty' }, h('p', { class: 'err' }, e.message))); }
  }

  function drawResults() {
    const r = st.results;
    if (!r) {
      results.replaceChildren(h('div', { class: 'empty' }, icon('search', 'big'), h('p', {}, '選好期刊、關鍵字與時間範圍後按「搜尋」。')));
      return;
    }
    const items = r.items.filter((x) => !st.hideLib || !x.paper_id);
    const nNew = r.items.filter((x) => !x.paper_id).length;
    const selBar = h('div', { class: 'row wrap js-rbar' },
      h('b', {}, `找到 ${r.total} 篇`), r.total > r.items.length ? h('span', { class: 'muted small' }, `（顯示最新 ${r.items.length} 篇）`) : null,
      h('span', { class: 'muted small' }, `${r.from} ～ ${r.to} · 論文庫已有 ${r.items.length - nNew} 篇`),
      h('span', { class: 'grow' }),
      h('label', { class: 'check small' }, h('input', { type: 'checkbox', checked: st.hideLib, onchange: (e) => { st.hideLib = e.target.checked; drawResults(); } }), '隱藏論文庫已有的'),
      h('button', { class: 'btn small', onclick: () => { items.filter((x) => !x.paper_id).forEach((x) => st.sel.add(x.uid)); drawResults(); } }, '全選'),
      can('upload') ? h('button', { class: 'btn small primary', onclick: (e) => addSel(e.currentTarget) }, icon('plus'), `加入論文庫（${st.sel.size}）`) : null);
    results.replaceChildren(
      selBar,
      h('p', { class: 'hint' }, r.engine === 'openalex' ? `OpenAlex 搜尋標題＋摘要：${r.query}` : `Crossref 搜尋（免金鑰，主要比對標題；Nature、Science 常沒有摘要）：${r.query}。設定免費的 OpenAlex 金鑰可以搜尋摘要、結果更完整。`),
      items.length ? h('div', { class: 'feed-list' }, items.map(resultItem)) : h('div', { class: 'empty' }, h('p', {}, '沒有找到。可以改用「聯集」、放寬時間範圍或多選幾本期刊。')));
  }

  function hl(text, kws) {
    let s = esc(text);
    kws.forEach((k) => { const re = new RegExp(`(${k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '[\\s-]+')}\\w*)`, 'gi'); s = s.replace(re, '<mark>$1</mark>'); });
    return s;
  }
  function resultItem(x) {
    const abs = h('p', { class: 'feed-abs clamp', html: hl(x.abstract || '', st.keywords) });
    return h('article', { class: 'feed-item' },
      x.paper_id ? h('span', { class: 'js-inlib', title: '論文庫已有' }, icon('check')) : h('input', { type: 'checkbox', checked: st.sel.has(x.uid), onchange: (e) => { e.target.checked ? st.sel.add(x.uid) : st.sel.delete(x.uid); drawResults(); } }),
      h('div', { class: 'grow' },
        h('div', { class: 'feed-title', html: hl(x.title, st.keywords) }),
        h('div', { class: 'muted small' }, [x.authors.slice(0, 4).join(', ') + (x.authors.length > 4 ? ' 等' : ''), x.venue, x.published, x.cited ? `被引用 ${x.cited}` : ''].filter(Boolean).join(' · ')),
        h('div', { class: 'chips wrap' }, x.matched.map((k) => h('span', { class: 'chip kw-hit' }, k))),
        x.abstract ? abs : null,
        x.abstract && x.abstract.length > 260 ? h('button', { class: 'linkbtn', onclick: (e) => { abs.classList.toggle('clamp'); e.target.textContent = abs.classList.contains('clamp') ? '展開摘要' : '收合'; } }, '展開摘要') : null,
        h('div', { class: 'row wrap' },
          x.paper_id ? h('a', { class: 'btn small', href: `#/p/${x.paper_id}` }, '開啟論文') : can('upload') ? h('button', { class: 'btn small primary', onclick: (e) => add([x], e.currentTarget) }, icon('plus'), x.pdf_url ? '加入論文庫（含開放取用 PDF）' : '加入論文庫') : null,
          x.url ? h('a', { class: 'btn small ghost', href: x.url, target: '_blank', rel: 'noopener' }, icon('external'), '原文') : null)));
  }
  async function add(list, btn) {
    if (!list.length) return toast('請先勾選');
    if (btn) { btn.disabled = true; btn.replaceChildren(h('span', { class: 'spinner sm' }), ' 加入中…'); }
    try {
      const out = await api.post('/api/journal-search/add', { items: list, tags: [] });
      const ok = out.filter((y) => y.paper_id);
      toast(`已加入 ${ok.length} 篇到「未歸檔待讀」${ok.filter((y) => y.pdf).length ? `（${ok.filter((y) => y.pdf).length} 篇含 PDF）` : ''}`);
      out.filter((y) => y.error).forEach((y) => toast(y.error, 'err'));
      const byUid = Object.fromEntries(list.map((y, i) => [y.uid, out[i]?.paper_id]));
      st.results.items.forEach((y) => { if (byUid[y.uid]) y.paper_id = byUid[y.uid]; });
      st.sel.clear(); refreshNav(); drawResults();
    } catch (e) { fail(e); if (btn) btn.disabled = false; }
  }
  function addSel(btn) { add(st.results.items.filter((x) => st.sel.has(x.uid)), btn); }

  async function saveFeed() {
    if (!st.keywords.length) return toast('請先選關鍵字');
    const days = st.days && st.days <= 90 ? st.days : 30;
    const config = { journals: [...st.journals], keywords: st.keywords, mode: st.mode, exclude: st.exclude, days };
    try {
      if (feed) { await api.patch(`/api/feeds/${feed.id}`, { config }); toast('已更新這個追蹤'); return; }
      const name = prompt('追蹤名稱', `期刊：${st.keywords.slice(0, 3).join(st.mode === 'and' ? ' 且 ' : '／')}`);
      if (!name) return;
      await api.post('/api/feeds', { kind: 'journal', name, config });
      toast(`已存成自動追蹤：每天檢查最近 ${days} 天發表的論文，新的會出現在「新論文追蹤」`);
    } catch (e) { fail(e); }
  }

  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '期刊搜尋'),
      h('p', { class: 'muted' }, '在 Physical Review、Nature、Science 等期刊裡，依論文庫常出現的關鍵字與時間範圍找論文。可以存成自動追蹤，每天把新發表的論文放進「新論文追蹤」。')),
      h('a', { class: 'btn small ghost', href: '#/feeds' }, icon('rss'), '新論文追蹤')),
    meta.engine === 'crossref' ? h('div', { class: 'notice warn' }, icon('info'), h('div', { class: 'small' },
      '目前用 Crossref 搜尋（不需金鑰，但主要只能比對標題）。', isAdmin() ? '建議到「管理 → 維護與備份 → 期刊搜尋」填入免費的 OpenAlex 金鑰，才能搜尋摘要、交集／聯集也更準確。' : '請管理員設定免費的 OpenAlex 金鑰，才能搜尋摘要。')) : null,
    feed ? h('div', { class: 'notice' }, icon('rss'), h('div', { class: 'small' }, `正在編輯自動追蹤「${feed.name}」：調整後按「更新追蹤」。`)) : null,
    h('div', { class: 'js-layout' }, form, results));
  draw();
  drawResults();
}
