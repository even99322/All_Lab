// v1.2 新功能的畫面：新論文追蹤、組會／閱讀清單、問論文庫（AI）、通知、回覆討論、各項設定。
import { h, $, icon, api, S, can, isAdmin, toast, fail, modal, menu, confirmBox, fmtDate, debounce, esc, autoGrow, refreshNav, route, tagInput } from './app.js';

// ================================================================== 小工具
export function watchJob(id, el, done) {
  // 背景工作進度：el 顯示狀態，完成後呼叫 done(job)
  let stop = false;
  (async function tick() {
    if (stop) return;
    try {
      const j = await api.get(`/api/jobs/${id}`);
      if (el) el.replaceChildren(...(j.state === 'done' ? [icon('check'), ` ${j.result}`] : j.state === 'failed' ? [h('span', { class: 'err' }, `失敗：${j.result}`)]
        : [h('span', { class: 'spinner sm' }), ` ${j.label}${j.progress ? `：${j.progress}` : j.state === 'queued' ? '（排隊中）' : '…'}`]));
      if (j.state === 'done' || j.state === 'failed') { done && done(j); return; }
    } catch (e) { if (el) el.textContent = e.message; return; }
    setTimeout(tick, 1200);
  })();
  return () => { stop = true; };
}

// 簡易 Markdown（AI 回答用）：標題、條列、粗體、表格、[citekey] 連到論文
export function mdRender(text, refs = []) {
  const byKey = Object.fromEntries(refs.map((r) => [r.citekey, r]));
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([A-Za-z][\w\-]+(?:[,;]\s*[A-Za-z][\w\-]+)*)\]/g, (m, keys) => keys.split(/[,;]\s*/).map((k) =>
      byKey[k] ? `<a class="cite" href="#/p/${byKey[k].id}" title="${esc(byKey[k].title)}">${esc(k)}</a>` : `[${esc(k)}]`).join(' '));
  const lines = String(text || '').split('\n');
  const out = [];
  let list = null, table = null;
  const flush = () => { if (list) { out.push(`<${list.t}>${list.items.map((x) => `<li>${x}</li>`).join('')}</${list.t}>`); list = null; }
    if (table) { out.push(`<table class="table md">${table.map((r, i) => `<tr>${r.map((c) => i ? `<td>${inline(c)}</td>` : `<th>${inline(c)}</th>`).join('')}</tr>`).join('')}</table>`); table = null; } };
  for (const raw of lines) {
    const l = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(l)) {
      if (list) flush();
      if (/^\s*\|[\s:\-|]+\|\s*$/.test(l)) continue;
      (table ||= []).push(l.trim().slice(1, -1).split('|').map((c) => c.trim()));
      continue;
    }
    if (table) flush();
    let m;
    if ((m = l.match(/^\s*[-*•]\s+(.*)/))) { if (!list || list.t !== 'ul') { flush(); list = { t: 'ul', items: [] }; } list.items.push(inline(m[1])); continue; }
    if ((m = l.match(/^\s*\d+[.)、]\s+(.*)/))) { if (!list || list.t !== 'ol') { flush(); list = { t: 'ol', items: [] }; } list.items.push(inline(m[1])); continue; }
    flush();
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) out.push(`<h4>${inline(m[2])}</h4>`);
    else if (l.trim()) out.push(`<p>${inline(l)}</p>`);
  }
  flush();
  return out.join('');
}

// ================================================================== 通知
export function notifBell() {
  const badge = h('span', { class: 'badge-dot', hidden: true });
  const btn = h('button', { class: 'icon-btn bell', 'aria-label': '通知', onclick: () => openNotifs(btn) }, icon('bell'), badge);
  async function poll() {
    if (!document.body.contains(btn)) return;
    try {
      const r = await api.get('/api/notifications');
      S.notifs = r;
      badge.hidden = !r.unread; badge.textContent = r.unread > 9 ? '9+' : r.unread;
      const en = (r.items || []).find((n) => n.kind === 'email' && !n.read);
      if (en) window.dispatchEvent(new CustomEvent('pl-email-check', { detail: { id: en.id } }));
    } catch { /* 離線時略過 */ }
  }
  poll();
  clearInterval(S.notifTimer);
  S.notifTimer = setInterval(poll, 60000);
  S.pollNotifs = poll;
  return btn;
}

async function openNotifs(anchor) {
  document.querySelectorAll('.notif-pop').forEach((x) => x.remove());
  const r = await api.get('/api/notifications');
  const KIND = { mention: '提到你', reply: '回覆', assign: '指派', done: '讀完', required: '必讀', register: '註冊申請', email: '填寫 Email' };
  const pop = h('div', { class: 'notif-pop menu' },
    h('div', { class: 'row' }, h('b', { class: 'grow' }, '通知'),
      r.unread ? h('button', { class: 'linkbtn', onclick: async () => { await api.post('/api/notifications/read', { all: true }); pop.remove(); S.pollNotifs && S.pollNotifs(); } }, '全部標為已讀') : null),
    r.items.length ? r.items.map((n) => h('button', { class: `notif ${n.read ? '' : 'unread'}`, onclick: async () => {
      pop.remove();
      if (!n.read) { await api.post('/api/notifications/read', { ids: [n.id] }); S.pollNotifs && S.pollNotifs(); }
      if (n.kind === 'register') location.hash = '#/admin/users';
      else if (n.kind === 'email') window.dispatchEvent(new Event('pl-email-remind'));
      else if (n.meeting_id && !n.ann_id) location.hash = '#/meetings';
      else if (n.kind === 'required' && !n.paper_id) location.hash = '#/required';
      else if (n.paper_id) location.hash = `#/p/${n.paper_id}/ann${n.ann_id ? `?ann=${n.ann_id}` : ''}`;
    } }, h('span', { class: `pill k-${n.kind}` }, KIND[n.kind] || n.kind), h('span', { class: 'grow notif-t' }, n.text, h('small', { class: 'muted' }, fmtDate(n.created_at)))))
      : h('p', { class: 'muted small pad-s' }, '沒有通知。別人在筆記中 @你、回覆你的討論、或排你報告時會出現在這裡。'));
  document.body.append(pop);
  const rc = anchor.getBoundingClientRect();
  pop.style.top = `${rc.bottom + 6}px`;
  pop.style.left = `${Math.max(8, Math.min(rc.right - 360, innerWidth - 368))}px`;
  setTimeout(() => document.addEventListener('click', function off(e) { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener('click', off); } }), 0);
}

// ================================================================== 我的帳號
export async function myAccount() {
  const me = await api.get('/api/me');
  const P = S.site.perm_names;
  const name = h('input', { value: me.display_name });
  const email = h('input', { type: 'email', value: me.email, placeholder: 'name@example.com', autocapitalize: 'none' });
  const dig = h('input', { type: 'checkbox', checked: !!me.digest });
  const m = modal('我的帳號', h('div', { class: 'stack' },
    h('label', {}, '顯示名稱（別人用 @這個名字 提到你）', name),
    h('label', {}, 'Email（收每週摘要）', email),
    h('label', { class: 'check' }, dig, '接收每週摘要 Email'),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try { await api.patch('/api/me', { display_name: name.value, email: email.value, digest: dig.checked }); S.user.display_name = name.value; S.user.has_email = !!email.value.trim(); if (S.user.has_email) document.querySelectorAll('.email-banner').forEach((x) => x.remove()); m.close(); toast('已儲存'); } catch (e) { fail(e); }
    } }, '儲存')),
    h('div', { class: 'card-form stack' }, h('b', {}, '兩步驟驗證（建議從外網登入時開啟）'),
      me.has_2fa ? h('p', { class: 'small ok' }, '已開啟：登入時除了密碼，還要輸入手機驗證 App 上的 6 位數。') : h('p', { class: 'muted small' }, '用 Google Authenticator、Microsoft Authenticator 或 1Password 等 App 掃描 QR code。'),
      h('div', { class: 'row wrap' },
        me.has_2fa ? h('button', { class: 'btn small', onclick: async () => { const pw = prompt('輸入密碼以關閉兩步驟驗證'); if (!pw) return; try { await api.post('/api/me/2fa/disable', { password: pw }); toast('已關閉'); m.close(); } catch (e) { fail(e); } } }, '關閉兩步驟驗證')
          : h('button', { class: 'btn small primary', onclick: () => { m.close(); setup2fa(); } }, '開啟兩步驟驗證'),
        h('button', { class: 'btn small ghost', onclick: async () => { const r = await api.post('/api/me/logout-others'); toast(`已登出其他 ${r.count} 個裝置`); } }, '登出其他裝置'))),
    h('details', {}, h('summary', {}, `我的權限（${S.site.role_names[S.user.role]}）`),
      h('ul', { class: 'permlist' }, Object.entries(P).map(([k, v]) => h('li', { class: S.user.perms[k] ? 'yes' : 'no' }, S.user.perms[k] ? '✓ ' : '✕ ', v))),
      h('p', { class: 'muted small' }, '需要更多權限請洽管理員。'))));
}

async function setup2fa() {
  const r = await api.post('/api/me/2fa/setup');
  const code = h('input', { inputmode: 'numeric', maxlength: 6, placeholder: '6 位數驗證碼', autocomplete: 'one-time-code' });
  const md = modal('開啟兩步驟驗證', h('div', { class: 'stack' },
    h('p', {}, '1. 用手機的驗證 App 掃描下面的 QR code（或手動輸入金鑰）。'),
    r.qr ? h('div', { class: 'qr', html: r.qr }) : null,
    h('code', { class: 'secret' }, r.secret.replace(/(.{4})/g, '$1 ').trim()),
    h('p', {}, '2. 輸入 App 顯示的 6 位數，確認設定成功。'), code,
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try { await api.post('/api/me/2fa/enable', { secret: r.secret, code: code.value }); md.close(); toast('已開啟兩步驟驗證'); } catch (e) { fail(e); }
    } }, '確認開啟'))));
  setTimeout(() => code.focus(), 50);
}

// ================================================================== 標註討論（回覆）
export function repliesBlock(a, reload) {
  const wrap = h('div', { class: 'replies' });
  const list = (a.replies || []).map((r) => h('div', { class: 'reply' },
    h('div', { class: 'muted small' }, h('b', {}, r.who || ''), ` · ${fmtDate(r.created_at)}`,
      (r.author_id === S.user.id || isAdmin()) ? h('button', { class: 'linkbtn', onclick: async () => { await api.del(`/api/replies/${r.id}`); reload(); } }, '刪除') : null),
    h('div', { html: esc(r.body).replace(/@(\S+)/g, '<span class="at">@$1</span>') })));
  wrap.append(...list);
  if (can('annotate') && !(a.private && a.author_id !== S.user.id)) {
    const ta = h('textarea', { rows: 1, placeholder: '回覆…（用 @名字 提及別人）', onfocus: (e) => { e.target.rows = 3; } });
    wrap.append(h('div', { class: 'reply-box' }, ta, h('button', { class: 'btn small', onclick: async () => {
      if (!ta.value.trim()) return;
      try { await api.post(`/api/annotations/${a.id}/replies`, { body: ta.value }); reload(); } catch (e) { fail(e); }
    } }, '送出')));
    mentionAssist(ta);
  }
  return wrap;
}

let mentionNames = null;
export async function mentionAssist(ta) {
  mentionNames ||= await api.get('/api/mention-names').catch(() => []);
  const box = h('div', { class: 'mention-box', hidden: true });
  ta.after(box);
  ta.addEventListener('input', () => {
    const before = ta.value.slice(0, ta.selectionStart);
    const m = before.match(/@([^\s@]*)$/);
    if (!m) { box.hidden = true; return; }
    const hits = mentionNames.filter((n) => n.toLowerCase().includes(m[1].toLowerCase())).slice(0, 6);
    box.hidden = !hits.length;
    box.replaceChildren(...hits.map((n) => h('button', { type: 'button', onmousedown: (e) => {
      e.preventDefault();
      const pos = ta.selectionStart - m[1].length;
      ta.value = ta.value.slice(0, pos) + n + ' ' + ta.value.slice(ta.selectionStart);
      box.hidden = true; ta.focus();
    } }, `@${n}`)));
  });
}

// ================================================================== 新論文追蹤
export async function viewFeeds(view, params) {
  view.classList.add('page');
  const st = { status: params.get('s') || 'new', selected: new Set(), feed: +(params.get('feed') || 0) || null };
  const list = h('div', { class: 'feed-list' });
  const jobEl = h('span', { class: 'muted small' });
  const head = h('div', {});
  const tabs = h('div', { class: 'seg' });
  const selbar = h('div', { class: 'selbar', hidden: true });
  let data = null, feeds = null;
  view.replaceChildren(head, h('div', { class: 'toolbar' }, tabs, h('span', { class: 'grow' }), jobEl,
    h('button', { class: 'btn small', onclick: async (e) => {
      const b = e.currentTarget; b.disabled = true;
      const r = await api.post('/api/feeds/check', {});
      watchJob(r.job, jobEl, () => { b.disabled = false; load(); refreshNav(); });
    } }, icon('refresh'), '立即檢查'),
    h('a', { class: 'btn small', href: '#/jsearch' }, icon('search'), '期刊搜尋'),
    can('manage') ? h('button', { class: 'btn small', onclick: () => feedSettings(feeds, load) }, icon('gear'), '追蹤設定') : null), list, selbar);

  async function load() {
    const q = new URLSearchParams({ status: st.status }); if (st.feed) q.set('feed', st.feed);
    [data, feeds] = await Promise.all([api.get(`/api/feed-items?${q}`), api.get('/api/feeds')]);
    head.replaceChildren(h('h1', {}, '新論文追蹤'),
      h('p', { class: 'muted' }, `每天 ${S.site.feed_hour || 7}:00 自動檢查 arXiv 與期刊 RSS。按「加入論文庫」才會建立論文（arXiv 會一併下載 PDF），放進「未歸檔待讀」。`,
        feeds.last_job ? ` 上次檢查：${fmtDate(feeds.last_job.created_at)}（${feeds.last_job.result || feeds.last_job.state}）` : ''),
      h('div', { class: 'chips wrap' }, h('button', { class: `chip toggle ${!st.feed ? 'on' : ''}`, onclick: () => { st.feed = null; load(); } }, '全部來源'),
        feeds.feeds.map((f) => h('button', { class: `chip toggle ${st.feed === f.id ? 'on' : ''}`, title: f.last_error || '', onclick: () => { st.feed = f.id; load(); } },
          f.name, f.n_new ? h('span', { class: 'count' }, ` ${f.n_new}`) : null, f.last_error ? h('span', { class: 'err' }, ' ⚠') : null, !f.enabled ? '（停用）' : null))));
    const c = data.counts;
    tabs.replaceChildren(...[['new', `待看 ${c.new || 0}`], ['added', `已加入 ${c.added || 0}`], ['dismissed', `已略過 ${c.dismissed || 0}`], ['inlib', `論文庫已有 ${c.inlib || 0}`]]
      .map(([k, l]) => h('button', { class: st.status === k ? 'on' : '', onclick: () => { st.status = k; st.selected.clear(); load(); } }, l)));
    list.replaceChildren(...(data.items.length ? data.items.map(item) : [h('div', { class: 'empty' }, icon('rss', 'big'), h('p', {}, st.status === 'new' ? '沒有待看的新論文。按「立即檢查」或等每天自動檢查。' : '這裡是空的。'))]));
    drawSel();
  }
  function item(it) {
    const abs = h('p', { class: 'feed-abs clamp' }, it.abstract);
    const sel = h('input', { type: 'checkbox', checked: st.selected.has(it.id), onchange: (e) => { e.target.checked ? st.selected.add(it.id) : st.selected.delete(it.id); drawSel(); } });
    return h('article', { class: 'feed-item' },
      st.status === 'new' ? sel : null,
      h('div', { class: 'grow' },
        h('div', { class: 'feed-title' }, it.title),
        h('div', { class: 'muted small' }, [it.authors.slice(0, 4).join(', ') + (it.authors.length > 4 ? ' 等' : ''), it.arxiv ? `arXiv:${it.arxiv}` : it.venue, (it.published || '').slice(0, 10), it.feed_name].filter(Boolean).join(' · ')),
        it.abstract ? abs : null,
        it.abstract && it.abstract.length > 260 ? h('button', { class: 'linkbtn', onclick: (e) => { abs.classList.toggle('clamp'); e.target.textContent = abs.classList.contains('clamp') ? '展開摘要' : '收合'; } }, '展開摘要') : null,
        h('div', { class: 'row wrap' },
          st.status === 'new' && can('upload') ? h('button', { class: 'btn small primary', onclick: (e) => add([it.id], e.currentTarget) }, icon('plus'), it.pdf_url ? '加入論文庫（含 PDF）' : '加入論文庫') : null,
          st.status === 'new' ? h('button', { class: 'btn small ghost', onclick: () => dismiss([it.id]) }, '略過') : null,
          st.status === 'dismissed' ? h('button', { class: 'btn small ghost', onclick: async () => { await api.post('/api/feed-items/restore', { ids: [it.id] }); load(); } }, '移回待看') : null,
          it.paper_id ? h('a', { class: 'btn small', href: `#/p/${it.paper_id}` }, '開啟論文') : null,
          it.url ? h('a', { class: 'btn small ghost', href: it.url, target: '_blank', rel: 'noopener' }, icon('external'), '原文') : null)));
  }
  function drawSel() {
    selbar.hidden = st.status !== 'new' || !data?.items.length;
    selbar.replaceChildren(h('span', {}, `已選 ${st.selected.size} 篇`),
      h('button', { class: 'btn small', onclick: () => { data.items.forEach((x) => st.selected.add(x.id)); load(); } }, '全選'),
      can('upload') ? h('button', { class: 'btn small primary', onclick: (e) => st.selected.size ? add([...st.selected], e.currentTarget) : toast('請先勾選') }, '加入論文庫') : null,
      h('button', { class: 'btn small', onclick: () => st.selected.size ? dismiss([...st.selected]) : toast('請先勾選') }, '略過'),
      h('span', { class: 'grow' }));
  }
  async function add(ids, btn) {
    const go = async (cats = [], tags = []) => {
      if (btn) { btn.disabled = true; btn.replaceChildren(h('span', { class: 'spinner sm' }), ' 下載中…'); }
      try {
        const r = await api.post('/api/feed-items/add', { ids, categories: cats, tags });
        const ok = r.filter((x) => x.paper_id), bad = r.filter((x) => x.error || x.pdf_error);
        toast(`已加入 ${ok.length} 篇${ok.filter((x) => x.pdf).length ? `（${ok.filter((x) => x.pdf).length} 篇含 PDF）` : ''}`);
        bad.forEach((x) => toast(x.error || `PDF 下載失敗：${x.pdf_error}`, 'err'));
        st.selected.clear(); await refreshNav(); load();
      } catch (e) { fail(e); if (btn) btn.disabled = false; }
    };
    go();
  }
  async function dismiss(ids) { await api.post('/api/feed-items/dismiss', { ids }); st.selected.clear(); refreshNav(); load(); }
  await load();
}

function feedSettings(feeds, reload) {
  const body = h('div', { class: 'stack' });
  const m = modal('追蹤設定', body, { wide: true, onClose: reload });
  const presets = feeds.presets;
  async function draw() {
    const fs = (await api.get('/api/feeds')).feeds;
    body.replaceChildren(
      h('p', { class: 'muted small' }, 'arXiv：關鍵字用逗號分隔，會搜尋標題與摘要；分類可限制在 cond-mat.mes-hall、quant-ph 等。RSS：貼上期刊的 RSS 網址，關鍵字留空則全部收錄。每個追蹤可以指定加入論文庫時自動加的標籤。'),
      ...fs.map(row), h('div', { class: 'row wrap' },
        h('button', { class: 'btn', onclick: async () => { await api.post('/api/feeds', { kind: 'arxiv', name: 'arXiv 新追蹤', keywords: 'magnon', categories: 'cond-mat.mes-hall, quant-ph' }); draw(); } }, icon('plus'), '新增 arXiv 追蹤'),
        h('button', { class: 'btn', onclick: async () => { await api.post('/api/feeds', { kind: 'rss', name: 'Phys. Rev. Lett.', url: presets['Phys. Rev. Lett.'], keywords: 'magnon, magnonic' }); draw(); } }, icon('plus'), '新增期刊 RSS')));
  }
  function row(f) {
    const save = (k) => async (e) => { try { await api.patch(`/api/feeds/${f.id}`, { [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }); toast('已儲存'); } catch (err) { fail(err); } };
    const urlIn = h('input', { value: f.url, list: 'rss-presets', placeholder: 'https://…/rss.xml', onchange: save('url') });
    return h('div', { class: 'card-form feed-edit' },
      h('div', { class: 'row' }, h('span', { class: 'pill' }, { arxiv: 'arXiv', rss: 'RSS', journal: '期刊搜尋' }[f.kind] || f.kind),
        h('input', { class: 'grow', value: f.name, onchange: save('name'), 'aria-label': '名稱' }),
        h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!f.enabled, onchange: save('enabled') }), '啟用'),
        h('button', { class: 'icon-btn sm', 'aria-label': '刪除', onclick: async () => { if (await confirmBox(`刪除「${f.name}」？已找到的論文清單也會刪除。`, '刪除')) { await api.del(`/api/feeds/${f.id}`); draw(); } } }, icon('trash'))),
      f.kind === 'journal' ? h('div', { class: 'small' },
        h('div', {}, `關鍵字（${f.config.mode === 'and' ? '交集' : '聯集'}）：${(f.config.keywords || []).join('、')}`),
        f.config.exclude?.length ? h('div', {}, `排除：${f.config.exclude.join('、')}`) : null,
        h('div', { class: 'muted' }, `${(f.config.journals || []).length} 本期刊 · 每次找最近 ${f.config.days || 30} 天發表的論文`),
        h('a', { class: 'btn small', href: `#/jsearch?feed=${f.id}`, onclick: () => m.close() }, icon('pen'), '在期刊搜尋中編輯')) :
      h('label', {}, '關鍵字（逗號分隔）', h('input', { value: f.keywords, onchange: save('keywords') })),
      f.kind === 'journal' ? null : f.kind === 'arxiv' ? h('label', {}, 'arXiv 分類（逗號分隔，留空＝不限）', h('input', { value: f.categories, onchange: save('categories') }))
        : h('label', {}, 'RSS 網址（可從清單挑選）', urlIn, h('datalist', { id: 'rss-presets' }, Object.entries(presets).map(([k, v]) => h('option', { value: v }, k)))),
      h('label', {}, '加入時自動加的標籤', tagInput(f.tags, async (t) => { await api.patch(`/api/feeds/${f.id}`, { tags: t }); })),
      f.last_error ? h('p', { class: 'hint warn' }, `上次錯誤：${f.last_error}`) : f.last_run ? h('p', { class: 'muted small' }, `上次檢查：${fmtDate(f.last_run)}`) : null);
  }
  draw();
  return m;
}

// ================================================================== 組會／閱讀清單
export async function viewMeetings(view, params) {
  view.classList.add('page');
  const scope = params.get('scope') || 'upcoming';
  const [ms, users] = await Promise.all([api.get(`/api/meetings?scope=${scope}`), api.get('/api/users')]);
  const canM = can('meeting');
  const userSel = (val, onchange) => h('select', { onchange, disabled: !canM }, h('option', { value: '' }, '（未定）'), users.map((u) => h('option', { value: u.id, selected: u.id === val }, u.display_name)));
  const reload = () => route();
  const card = (m) => h('section', { class: `meeting card-form ${m.date === new Date().toISOString().slice(0, 10) ? 'today' : ''}` },
    h('div', { class: 'row wrap' },
      h('div', { class: 'meet-date' }, h('b', {}, m.date.slice(5).replace('-', '/')), h('small', {}, '週' + '日一二三四五六'[new Date(m.date + 'T00:00').getDay()])),
      h('div', { class: 'grow' }, h('h3', {}, m.title), m.note ? h('p', { class: 'muted small' }, m.note) : null),
      m.items.some((it) => it.paper_id) ? h('button', { class: 'btn small ghost', title: '把這場的論文、重點欄、參數、圖卡做成 PowerPoint', onclick: async () => (await import('./research.js')).slidesDialog({ meeting: m }) }, icon('slides'), h('span', { class: 'hide-mobile' }, '投影片')) : null,
      canM ? h('button', { class: 'icon-btn sm', 'aria-label': '更多', onclick: (e) => menu(e.currentTarget, [
        { label: '改日期／標題…', icon: 'pen', run: () => editMeeting(m, reload) },
        { label: '刪除這場', icon: 'trash', danger: true, run: async () => { if (await confirmBox(`刪除 ${m.date}「${m.title}」？`, '刪除')) { await api.del(`/api/meetings/${m.id}`); reload(); } } },
      ]) }, icon('more')) : null),
    h('div', { class: 'meet-items' }, m.items.length ? m.items.map((it) => h('div', { class: `meet-item ${it.presenter_id === S.user.id ? 'mine' : ''}` },
      userSel(it.presenter_id, async (e) => { await api.patch(`/api/meeting-items/${it.id}`, { presenter_id: +e.target.value || null }); toast('已更新報告人'); }),
      it.paper_id ? h('a', { class: 'grow', href: `#/p/${it.paper_id}` }, it.title, h('span', { class: 'muted small' }, ` ${it.citekey}`)) : h('span', { class: 'grow muted' }, it.note || '（自由主題）'),
      it.paper_id && it.note ? h('span', { class: 'muted small' }, it.note) : null,
      canM ? h('button', { class: 'icon-btn sm', 'aria-label': '移除', onclick: async () => { await api.del(`/api/meeting-items/${it.id}`); reload(); } }, icon('x')) : null))
      : h('p', { class: 'muted small' }, '還沒有排論文。')),
    canM ? addItemForm(m, users, reload) : null);
  view.replaceChildren(
    h('div', { class: 'list-head' }, h('div', {}, h('h1', {}, '組會／閱讀清單'),
      h('p', { class: 'muted' }, '排定每場組會要報告的論文與報告人。被排到的人會收到通知，每週摘要也會提醒。論文頁的選單也可以「排進組會」。'))),
    h('div', { class: 'toolbar' }, h('div', { class: 'seg' }, [['upcoming', '接下來'], ['past', '過去']].map(([k, l]) => h('a', { class: `btn small ${scope === k ? 'on' : ''}`, href: `#/meetings?scope=${k}` }, l))),
      h('span', { class: 'grow' }), canM ? h('button', { class: 'btn primary small', onclick: () => editMeeting(null, reload) }, icon('plus'), '新增組會') : null),
    ...(ms.length ? ms.map(card) : [h('div', { class: 'empty' }, icon('calendar', 'big'), h('p', {}, scope === 'upcoming' ? '還沒有排定的組會。' : '沒有過去的紀錄。'))]));
}

function nextWeekday(dow = 3) {
  const d = new Date(); d.setDate(d.getDate() + ((dow - d.getDay() + 7) % 7 || 7));
  return d.toISOString().slice(0, 10);
}

function editMeeting(m, done) {
  const date = h('input', { type: 'date', value: m?.date || nextWeekday() });
  const title = h('input', { value: m?.title || '組會', placeholder: '例如：第 5 週組會、Journal Club' });
  const note = h('textarea', { rows: 2, placeholder: '地點、備註（可空白）' }, m?.note || '');
  const md = modal(m ? '編輯組會' : '新增組會', h('div', { class: 'stack' }, h('label', {}, '日期', date), h('label', {}, '標題', title), h('label', {}, '備註', note),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try { m ? await api.patch(`/api/meetings/${m.id}`, { date: date.value, title: title.value, note: note.value }) : await api.post('/api/meetings', { date: date.value, title: title.value, note: note.value }); md.close(); done && done(); } catch (e) { fail(e); }
    } }, '儲存'))));
}

function addItemForm(m, users, reload) {
  let paper = null;
  const res = h('div', { class: 'lookup-res' });
  const q = h('input', { placeholder: '搜尋論文加入（或直接寫主題）', oninput: debounce(async (e) => {
    paper = null;
    const v = e.target.value.trim(); if (v.length < 2) return res.replaceChildren();
    const rows = await api.get(`/api/lookup?q=${encodeURIComponent(v)}`);
    res.replaceChildren(...rows.map((r) => h('button', { class: 'lookup-item', onclick: () => { paper = r; q.value = r.title; res.replaceChildren(); } }, h('b', {}, r.title), h('span', { class: 'muted small' }, ` ${r.year || ''} · ${r.citekey}`))));
  }, 250) });
  const who = h('select', {}, h('option', { value: '' }, '報告人'), users.map((u) => h('option', { value: u.id }, u.display_name)));
  return h('div', { class: 'meet-add' }, h('div', { class: 'grow rel-wrap' }, q, res), who,
    h('button', { class: 'btn small', onclick: async () => {
      if (!paper && !q.value.trim()) return toast('先搜尋論文或輸入主題');
      try { await api.post(`/api/meetings/${m.id}/items`, { paper_id: paper?.id || null, presenter_id: +who.value || null, note: paper ? '' : q.value.trim() }); reload(); } catch (e) { fail(e); }
    } }, icon('plus'), '加入'));
}

export async function scheduleDialog(p, done) {
  const [ms, users] = await Promise.all([api.get('/api/meetings?scope=upcoming'), api.get('/api/users')]);
  const sel = h('select', {}, ms.map((m) => h('option', { value: m.id }, `${m.date} ${m.title}（已排 ${m.items.length}）`)), h('option', { value: 'new' }, '＋ 新的一場…'));
  const date = h('input', { type: 'date', value: nextWeekday() });
  const newBox = h('label', { hidden: ms.length > 0 }, '新組會日期', date);
  sel.onchange = () => { newBox.hidden = sel.value !== 'new'; };
  if (!ms.length) sel.value = 'new';
  const who = h('select', {}, h('option', { value: '' }, '（未定）'), users.map((u) => h('option', { value: u.id, selected: u.id === S.user.id }, u.display_name)));
  const md = modal('排進組會', h('div', { class: 'stack' }, h('p', { class: 'muted small' }, p.title), h('label', {}, '組會', sel), newBox, h('label', {}, '報告人', who),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      try {
        let mid = sel.value;
        if (mid === 'new') mid = (await api.post('/api/meetings', { date: date.value, title: '組會' })).id;
        await api.post(`/api/meetings/${mid}/items`, { paper_id: p.id, presenter_id: +who.value || null });
        md.close(); toast('已排進組會'); done && done();
      } catch (e) { fail(e); }
    } }, '排入'))));
}

// ================================================================== AI
export async function aiKeyinfoDialog(p, apply) {
  const body = h('div', { class: 'stack' }, h('div', { class: 'empty' }, h('div', { class: 'spinner' }), h('p', {}, 'AI 正在讀這篇論文…（約 10–60 秒）')));
  const md = modal('AI 預填重點欄', body, { wide: true });
  let sug;
  try { sug = (await api.post(`/api/papers/${p.id}/ai/keyinfo`)).suggest; } catch (e) { body.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
  const cur = p.keyinfo || {};
  const rows = Object.entries(sug).map(([k, v]) => {
    const cb = h('input', { type: 'checkbox', checked: !cur[k] });
    const ta = h('textarea', { rows: 2 }, v);
    return { k, cb, ta, el: h('div', { class: 'ai-row' }, h('label', { class: 'check' }, cb, h('b', {}, k), cur[k] ? h('span', { class: 'pill warn' }, '會覆蓋現有內容') : null),
      cur[k] ? h('div', { class: 'muted small' }, `現在：${cur[k]}`) : null, ta) };
  });
  body.replaceChildren(h('p', { class: 'hint' }, '勾選要套用的欄位，內容可以先修改。AI 可能出錯，請對照原文確認數值。'), ...rows.map((r) => r.el),
    h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: async () => {
      const next = { ...cur };
      rows.forEach((r) => { if (r.cb.checked && r.ta.value.trim()) next[r.k] = r.ta.value.trim(); });
      try { await apply(next); md.close(); toast('已套用'); } catch (e) { fail(e); }
    } }, '套用勾選的欄位')));
  body.querySelectorAll('textarea').forEach(autoGrow);
}

function askBox({ paperId = null, compact = false } = {}) {
  const out = h('div', { class: 'ask-out' });
  const scope = !paperId ? h('select', { 'aria-label': '範圍' }, h('option', { value: '' }, '整個論文庫'), S.cats.map((c) => h('option', { value: c.id }, `只找「${c.name}」`))) : null;
  const q = h('textarea', { rows: compact ? 2 : 3, placeholder: paperId ? '例如：這篇的耦合強度怎麼量的？和我們的 mirror 架設差在哪？' : '例如：哪些論文量過 level attraction？各用什麼架設、耦合強度多少？',
    onkeydown: (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) go(); } });
  const btn = h('button', { class: 'btn primary', onclick: () => go() }, icon('spark'), '問');
  async function go() {
    if (!q.value.trim()) return;
    btn.disabled = true;
    const card = h('div', { class: 'ask-card' }, h('div', { class: 'ask-q' }, q.value), h('div', { class: 'ask-a' }, h('span', { class: 'spinner sm' }), paperId ? ' 閱讀全文中…' : ' 搜尋論文庫、整理回答中…'));
    out.prepend(card);
    try {
      const r = await api.post('/api/ai/ask', { question: q.value, paper_id: paperId, cat: scope && +scope.value || null });
      card.querySelector('.ask-a').innerHTML = mdRender(r.answer, r.refs);
      if (r.refs.length && !paperId) card.append(h('div', { class: 'ask-refs' }, h('span', { class: 'muted small' }, '參考：'), r.refs.map((x) => h('a', { class: 'chip', href: `#/p/${x.id}`, title: x.title }, x.citekey))));
      q.value = '';
    } catch (e) { card.querySelector('.ask-a').replaceChildren(h('span', { class: 'err' }, e.message)); }
    btn.disabled = false;
  }
  return { el: h('div', { class: 'ask' }, h('div', { class: 'ask-in' }, q, h('div', { class: 'row' }, scope, h('span', { class: 'muted small grow' }, 'Ctrl／⌘＋Enter 送出'), btn)), out), out };
}

// ================================================================== AI 綜述（整個論文庫）
export async function viewReports(view, params) {
  view.classList.add('page');
  if (!S.site.ai?.enabled) {
    view.replaceChildren(h('h1', {}, 'AI 綜述'), h('div', { class: 'empty' }, icon('spark', 'big'), h('p', {}, isAdmin() ? '還沒有設定 AI 服務。' : '管理員還沒有設定 AI 服務。'),
      isAdmin() ? h('a', { class: 'btn primary', href: '#/admin/settings' }, '前往設定') : null));
    return;
  }
  const canBatch = isAdmin() || (can('manage') && can('ai'));
  const jobEl = h('div', { class: 'tr-test' });
  const reader = h('article', { class: 'report', hidden: true });
  const list = h('div', { class: 'report-list' });
  let stopWatch = null;
  const mode = h('select', {}, h('option', { value: 'missing' }, '只補還沒填的欄位（建議）'), h('option', { value: 'empty' }, '只處理重點欄完全空白的論文'),
    h('option', { value: 'all' }, '全部重新產生（仍不會覆蓋人寫的內容）'));
  const scopeSel = h('select', {}, h('option', { value: '' }, '整個論文庫'), S.cats.map((c) => h('option', { value: c.id }, `只做「${c.name}」`)));
  const est = h('span', { class: 'muted small' });
  const updEst = async () => {
    const r = await api.get(`/api/ai/estimate?mode=${mode.value}${scopeSel.value ? `&cat=${scopeSel.value}` : ''}`);
    est.textContent = r.papers ? `要處理 ${r.papers} 篇，約 ${r.minutes} 分鐘${r.usd != null ? `、約 US$${r.usd}` : ''}` : '沒有需要處理的論文';
  };
  mode.onchange = updEst; scopeSel.onchange = updEst;
  const run = async (url, body, after) => {
    try {
      const r = await api.post(url, body);
      stopWatch && stopWatch();
      stopWatch = watchJob(r.job, jobEl, () => { after && after(); });
    } catch (e) { fail(e); }
  };
  async function drawList() {
    const rs = await api.get('/api/ai/reports');
    const by = Object.fromEntries(rs.map((r) => [r.scope, r]));
    const item = (scope, title, sub, color) => {
      const r = by[scope];
      return h('div', { class: 'report-item' },
        color ? h('span', { class: 'dot', style: { background: color } }) : icon(scope === 'all' ? 'grid' : 'inbox'),
        h('div', { class: 'grow' }, h('b', {}, title), h('div', { class: 'muted small' }, r ? `${fmtDate(r.created_at)} · ${r.n_papers} 篇 · ${r.model}` : sub)),
        r ? h('button', { class: 'btn small', onclick: () => openReport(r.id) }, '閱讀') : null,
        scope === 'all' && !canBatch ? null : h('button', { class: 'btn small ghost', onclick: () => run('/api/ai/review', { scope: scope === 'all' ? 'all' : scope.slice(4) }, drawList) }, r ? '重新產生' : '產生'));
    };
    list.replaceChildren(
      item('all', '整個論文庫總覽', '彙整各分類綜述（沒有的會先產生）'),
      ...S.cats.filter((c) => c.count).map((c) => item(`cat:${c.id}`, c.name, `${c.count} 篇，還沒有綜述`, c.color)),
      item('cat:0', '未歸檔待讀', '還沒分類的論文', null));
    const want = params.get('cat');
    if (want != null && by[`cat:${want}`]) openReport(by[`cat:${want}`].id);
    else if (want != null) toast('這個分類還沒有綜述，按「產生」');
  }
  async function openReport(id) {
    const r = await api.get(`/api/ai/reports/${id}`);
    reader.hidden = false;
    reader.replaceChildren(
      h('div', { class: 'row' }, h('h2', { class: 'grow' }, r.title),
        h('a', { class: 'btn small ghost', href: `/api/ai/reports/${id}/md` }, icon('download'), 'Markdown'),
        h('button', { class: 'icon-btn sm', 'aria-label': '關閉', onclick: () => { reader.hidden = true; } }, icon('x'))),
      h('p', { class: 'hint' }, `${fmtDate(r.created_at)} 由 ${r.who || '系統'} 產生 · 根據 ${r.n_papers} 篇的重點欄 · ${r.model}。AI 整理的內容可能有誤，數值與結論請點 citekey 回原文確認。`),
      h('div', { class: 'ask-a', html: mdRender(r.content, r.refs) }));
    reader.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  view.replaceChildren(
    h('h1', {}, 'AI 綜述'),
    h('p', { class: 'muted' }, '讓 AI 把整個論文庫整理成重點。分兩步：先逐篇讀全文填好「重點欄」，再依分類寫綜述（研究脈絡、架設與參數比較表、未解問題、建議閱讀順序），最後彙整成整個論文庫的總覽。'),
    canBatch ? h('div', { class: 'card-form stack' },
      h('h3', {}, '① 逐篇填寫重點欄'),
      h('p', { class: 'muted small' }, 'AI 讀每篇全文，填重點、架設／平台、關鍵參數、可萃取特徵、與我們實驗的關係。已經有人寫的欄位不會被覆蓋；AI 填的欄位會標「AI」，有人修改後標記就消失。掃描檔請先 OCR。'),
      h('div', { class: 'row wrap' }, scopeSel, mode, est),
      h('div', { class: 'row wrap' },
        h('button', { class: 'btn primary', onclick: () => run('/api/ai/batch', { mode: mode.value, cat: +scopeSel.value || null }, drawList) }, icon('spark'), '開始填寫'),
        h('button', { class: 'btn', onclick: async () => { await run('/api/ai/batch', { mode: mode.value, cat: +scopeSel.value || null }); await run('/api/ai/review', { scope: 'cats' }, drawList); toast('已排入：先填重點欄，再產生各分類綜述與總覽'); } }, '一鍵：填重點欄 → 分類綜述 → 總覽')),
      h('h3', {}, '② 分類綜述與總覽'),
      h('p', { class: 'muted small' }, '綜述是根據各篇的重點欄寫的，所以先做 ①，綜述品質會好很多。每份綜述只要幾分錢。'),
      h('div', { class: 'row wrap' }, h('button', { class: 'btn', onclick: () => run('/api/ai/review', { scope: 'cats' }, drawList) }, '重新產生所有分類綜述＋總覽')),
      jobEl) : h('p', { class: 'hint' }, '整批處理由管理員執行；你可以閱讀已產生的綜述，或對單一分類按「產生」。'),
    list, reader);
  if (canBatch) updEst();
  await drawList();
  const running = (await api.get('/api/jobs')).find((j) => ['ai_batch', 'ai_review'].includes(j.kind) && ['queued', 'running'].includes(j.state));
  if (running && canBatch) stopWatch = watchJob(running.id, jobEl, drawList);
  return () => stopWatch && stopWatch();
}

export async function viewAsk(view) {
  view.classList.add('page');
  if (!S.site.ai?.enabled) {
    view.replaceChildren(h('h1', {}, '問論文庫'), h('div', { class: 'empty' }, icon('spark', 'big'), h('p', {}, isAdmin() ? '還沒有設定 AI 服務。' : '管理員還沒有設定 AI 服務。'),
      isAdmin() ? h('a', { class: 'btn primary', href: '#/admin/settings' }, '前往設定') : null));
    return;
  }
  const box = askBox();
  const hist = await api.get('/api/ai/history');
  view.replaceChildren(h('h1', {}, '問論文庫'),
    h('p', { class: 'muted' }, 'AI 會先在論文庫裡搜尋相關論文（全文、重點欄、公開筆記），再根據找到的段落回答並標出處。回答只根據論文庫的內容，請點出處核對。'),
    box.el,
    hist.length ? h('details', { class: 'ask-hist' }, h('summary', {}, `我之前問過的（${hist.length}）`),
      hist.map((x) => h('div', { class: 'ask-card' }, h('div', { class: 'ask-q' }, x.question, h('small', { class: 'muted' }, ` · ${fmtDate(x.created_at)}`)),
        h('div', { class: 'ask-a', html: mdRender(x.answer, x.refs) })))) : null);
}

export function askDialog(p) {
  const box = askBox({ paperId: p.id, compact: true });
  modal(`問這篇：${p.citekey}`, box.el, { wide: true });
  api.get(`/api/ai/history?paper_id=${p.id}`).then((hs) => hs.reverse().forEach((x) => box.out.prepend(h('div', { class: 'ask-card' }, h('div', { class: 'ask-q' }, x.question),
    h('div', { class: 'ask-a', html: mdRender(x.answer, x.refs) }))))).catch(() => {});
}

// ================================================================== 管理：各項設定
function field(label, el, hint) { return h('label', {}, label, el, hint ? h('small', { class: 'muted' }, hint) : null); }
function secretInput(has, ph = '貼上金鑰') { return h('input', { type: 'password', autocomplete: 'off', placeholder: has ? '已設定（留空＝沿用）' : ph }); }

async function saveSettings(obj) {
  const r = await api.patch('/api/admin/settings', obj);
  S.site = await (await fetch('/api/site', { credentials: 'same-origin' })).json(); S.user = S.site.user;
  document.title = S.site.name; const b = $('.brand-name'); if (b) b.textContent = S.site.name;
  return r;
}

export async function adminSite(body) {
  const st = await api.get('/api/admin/settings');
  // ---- 網站
  const nameIn = h('input', { value: st.site_name, maxlength: 40 });
  const urlIn = h('input', { value: st.site_url, placeholder: 'http://100.x.x.x:8080', autocapitalize: 'none' });
  // ---- 翻譯
  const prov = h('select', { onchange: () => hints() }, Object.entries(st.tr_providers).map(([k, v]) => h('option', { value: k, selected: k === st.tr_provider }, v)));
  const key = secretInput(st.has_tr_key);
  const url = h('input', { value: st.tr_url, autocapitalize: 'none' });
  const model = h('input', { value: st.tr_model, autocapitalize: 'none' });
  const target = h('select', {}, Object.entries(st.targets).map(([k, v]) => h('option', { value: k, selected: k === st.tr_target }, v)));
  const hint = h('p', { class: 'hint' });
  const trRes = h('div', { class: 'tr-test' });
  const HINTS = {
    '': '選一個翻譯服務後，閱讀 PDF 時選取文字就會出現「翻譯」按鈕。',
    deepl: 'DeepL：到 deepl.com 申請 API。2026 年起的免費 Developer 方案總共只有 100 萬字（用完不會重置），長期使用要付費的 Growth 方案。金鑰結尾是 :fx 會自動使用免費端點。網址、模型留空即可。',
    google: 'Google Cloud Translation v2：在 Google Cloud 啟用 Cloud Translation API 並建立 API key。網址、模型留空即可。',
    libre: 'LibreTranslate：可以用 Docker 在 NAS 上自架（libretranslate/libretranslate），資料不出實驗室。網址填 http://<NAS IP>:5000。物理專有名詞的翻譯品質較弱。',
    anthropic: 'Claude：在 console.anthropic.com 建立 API 金鑰。模型留空會用 claude-haiku-4-5-20251001；會保留專有名詞原文。',
    openai: 'OpenAI 相容 API：用 OpenAI 時網址留空；自架 Ollama 時網址填 http://<主機>:11434/v1、模型填例如 qwen2.5:7b，金鑰留空。',
  };
  const hints = () => { hint.textContent = HINTS[prov.value] || ''; model.placeholder = st.tr_default_model[prov.value] || '（不需要）'; url.placeholder = { libre: 'http://192.168.x.x:5000', openai: 'https://api.openai.com/v1' }[prov.value] || '（自動）'; };
  hints();
  // ---- AI
  const aprov = h('select', { onchange: () => aHints() }, Object.entries(st.ai_providers).map(([k, v]) => h('option', { value: k, selected: k === st.ai_provider }, v)));
  const akey = secretInput(st.has_ai_key);
  const aurl = h('input', { value: st.ai_url, autocapitalize: 'none' });
  const amodel = h('input', { value: st.ai_model, autocapitalize: 'none' });
  const amax = h('input', { value: st.ai_max_chars, inputmode: 'numeric' });
  const lab = h('textarea', { rows: 3, placeholder: '例如：我們用 VNA 量測 YIG 小球與共平面波導／1D 半開放波導（node mirror）耦合的 S21、S11，調整磁場與位置；關心 level attraction、EP、非互易、巨原子效應。' }, st.lab_context);
  const aBox = h('div', { class: 'stack' });
  const aRes = h('div', { class: 'tr-test' });
  const aHints = () => { aBox.hidden = ['', 'same'].includes(aprov.value); amodel.placeholder = st.ai_default_model[aprov.value] || ''; };
  aBox.append(field('API 金鑰', akey), h('div', { class: 'grid2' }, field('服務網址', aurl, 'Ollama：http://<主機>:11434/v1'), field('模型', amodel)));
  aHints();
  const saveAll = () => saveSettings({ site_name: nameIn.value, site_url: urlIn.value, tr_provider: prov.value, tr_key: key.value, tr_url: url.value, tr_model: model.value, tr_target: target.value,
    ai_provider: aprov.value, ai_key: akey.value, ai_url: aurl.value, ai_model: amodel.value, ai_max_chars: amax.value, lab_context: lab.value })
    .then(() => { key.value = ''; akey.value = ''; });
  const saveBtn = () => h('button', { class: 'btn primary', onclick: async () => { try { await saveAll(); toast('已儲存'); } catch (e) { fail(e); } } }, '儲存');
  body.replaceChildren(
    h('div', { class: 'card-form stack' }, h('h3', {}, '網站'),
      field('網站名稱', nameIn, '顯示在頂列、登入頁與加到主畫面的名稱'),
      field('網站網址', urlIn, '成員平常連線用的網址（例如 Tailscale IP）。用在 Email 摘要、Webhook 與 Zotero 的「在論文庫開啟」連結。'),
      h('div', { class: 'row' }, saveBtn())),
    h('div', { class: 'card-form stack' }, h('h3', {}, icon('globe'), '翻譯服務'),
      h('p', { class: 'muted' }, '閱讀 PDF 時選取文字 → 按「翻譯」。結果會快取，同一段文字再翻不會重複計費。金鑰只存在 NAS 的資料庫，不會傳到瀏覽器。'),
      h('div', { class: 'grid2' }, field('服務', prov), field('翻譯成', target)), hint, field('API 金鑰', key),
      h('div', { class: 'grid2' }, field('服務網址', url), field('模型', model)),
      h('div', { class: 'row wrap' }, saveBtn(),
        h('button', { class: 'btn', onclick: async (e) => {
          const b = e.currentTarget; b.disabled = true; trRes.replaceChildren(h('span', { class: 'spinner sm' }), ' 測試中…');
          try { await saveAll(); const r = await api.post('/api/admin/settings/test'); trRes.replaceChildren(h('div', { class: 'muted small' }, r.source), h('div', {}, r.text)); } catch (err) { trRes.replaceChildren(h('span', { class: 'err' }, err.message)); }
          b.disabled = false;
        } }, '儲存並測試'),
        st.has_tr_key ? h('button', { class: 'btn ghost', onclick: async () => { await saveSettings({ clear_tr_key: true }); toast('已清除金鑰'); route(); } }, '清除金鑰') : null),
      trRes),
    h('div', { class: 'card-form stack' }, h('h3', {}, icon('spark'), 'AI（預填重點欄、問論文庫）'),
      h('p', { class: 'muted' }, '需要大型語言模型：Claude 或 OpenAI 相容 API（也可以是實驗室自架的 Ollama，資料不出實驗室）。選「沿用翻譯設定」時，翻譯服務必須是 Claude 或 OpenAI 相容。'),
      field('服務', aprov), aBox,
      field('每次最多送出的字數', amax, '一篇論文全文約 3–6 萬字元。用小模型（例如 Ollama 7B）請改成 12000 左右。'),
      field('實驗室背景（AI 判斷「與我們實驗的關係」時參考）', lab),
      h('div', { class: 'row wrap' }, saveBtn(),
        h('button', { class: 'btn', onclick: async (e) => {
          const b = e.currentTarget; b.disabled = true; aRes.replaceChildren(h('span', { class: 'spinner sm' }), ' 測試中…');
          try { await saveAll(); const r = await api.post('/api/admin/ai/test'); aRes.replaceChildren(h('div', {}, r.text)); } catch (err) { aRes.replaceChildren(h('span', { class: 'err' }, err.message)); }
          b.disabled = false;
        } }, '儲存並測試'),
        st.has_ai_key ? h('button', { class: 'btn ghost', onclick: async () => { await saveSettings({ clear_ai_key: true }); toast('已清除金鑰'); route(); } }, '清除金鑰') : null),
      aRes));
  lab.addEventListener('input', () => autoGrow(lab)); autoGrow(lab);
  // ---- 語意搜尋的向量模型（選用）
  const eprov = h('select', {}, h('option', { value: '', selected: !st.emb_provider }, '內建（TF-IDF，不需設定）'), h('option', { value: 'openai', selected: st.emb_provider === 'openai' }, '向量模型（OpenAI 相容 /embeddings）'));
  const ekey = secretInput(st.has_emb_key);
  const eurl = h('input', { value: st.emb_url, autocapitalize: 'none', placeholder: 'https://api.openai.com/v1' });
  const emodel = h('input', { value: st.emb_model, autocapitalize: 'none', placeholder: 'text-embedding-3-small' });
  const eRes = h('div', { class: 'tr-test' });
  const eBox = h('div', { class: 'stack', hidden: !st.emb_provider }, field('API 金鑰', ekey, '自架 Ollama 留空'),
    h('div', { class: 'grid2' }, field('服務網址', eurl, 'OpenAI：留空。Voyage：https://api.voyageai.com/v1。Ollama：http://<主機>:11434/v1'), field('模型', emodel, 'OpenAI：text-embedding-3-small。Voyage：voyage-3.5。Ollama：bge-m3（中英文都好）')));
  eprov.onchange = () => { eBox.hidden = !eprov.value; };
  const esave = () => saveSettings({ emb_provider: eprov.value, emb_key: ekey.value, emb_url: eurl.value, emb_model: emodel.value }).then(() => { ekey.value = ''; });
  const estat = await api.get('/api/admin/embed').catch(() => null);
  body.append(h('div', { class: 'card-form stack' }, h('h3', {}, icon('sim'), '語意搜尋與相似論文'),
    h('p', { class: 'muted' }, '內建引擎不需任何設定：用內容關鍵詞的相似度找「意思接近」的論文（中文問題會先用 AI 轉成英文關鍵字）。接上向量模型後，換句話說、中英文混搜都能找到，相似論文也更準；每篇論文只在內容變動時重新計算，費用極低（整個論文庫通常不到 US$0.1）。'),
    field('引擎', eprov), eBox,
    estat?.enabled ? h('p', { class: 'small' }, `已建立向量：${estat.done}／${estat.total} 篇（${estat.model}）。新論文每天清晨自動補上。`) : null,
    h('div', { class: 'row wrap' },
      h('button', { class: 'btn primary', onclick: async () => { try { await esave(); toast('已儲存'); } catch (e) { fail(e); } } }, '儲存'),
      h('button', { class: 'btn', onclick: async () => { try { await esave(); const r = await api.post('/api/admin/embed/test'); eRes.replaceChildren(r.msg); } catch (e) { eRes.replaceChildren(h('span', { class: 'err' }, e.message)); } } }, '儲存並測試'),
      h('button', { class: 'btn', onclick: async () => { try { await esave(); const r = await api.post('/api/admin/embed/run'); watchJob(r.job, eRes); } catch (e) { fail(e); } } }, '建立全部向量'),
      st.has_emb_key ? h('button', { class: 'btn ghost', onclick: async () => { await saveSettings({ clear_emb_key: true }); toast('已清除金鑰'); route(); } }, '清除金鑰') : null),
    eRes));
}

export async function adminNotify(body) {
  const st = await api.get('/api/admin/settings');
  const host = h('input', { value: st.smtp_host, placeholder: 'smtp.gmail.com', autocapitalize: 'none' });
  const port = h('input', { value: st.smtp_port, inputmode: 'numeric' });
  const sec = h('select', {}, [['starttls', 'STARTTLS（587）'], ['ssl', 'SSL（465）'], ['none', '不加密（區網）']].map(([k, v]) => h('option', { value: k, selected: st.smtp_security === k }, v)));
  const user = h('input', { value: st.smtp_user, autocapitalize: 'none' });
  const pass = secretInput(st.has_smtp_pass, '密碼或應用程式密碼');
  const from = h('input', { value: st.smtp_from, placeholder: '寄件人 Email（留空＝帳號）', autocapitalize: 'none' });
  const hook = secretInput(st.has_webhook_url, 'https://discord.com/api/webhooks/…');
  const en = h('input', { type: 'checkbox', checked: st.digest_enabled === '1' });
  const dow = h('select', {}, '一二三四五六日'.split('').map((d, i) => h('option', { value: i, selected: String(i) === st.digest_dow }, `星期${d}`)));
  const hour = h('select', {}, Array.from({ length: 24 }, (_, i) => h('option', { value: i, selected: String(i) === st.digest_hour }, `${i}:00`)));
  const out = h('pre', { class: 'digest-out', hidden: true });
  const save = () => saveSettings({ smtp_host: host.value, smtp_port: port.value, smtp_security: sec.value, smtp_user: user.value, smtp_pass: pass.value, smtp_from: from.value,
    webhook_url: hook.value, digest_enabled: en.checked, digest_dow: dow.value, digest_hour: hour.value }).then(() => { pass.value = ''; hook.value = ''; });
  const test = (kind, label) => h('button', { class: 'btn', onclick: async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { await save(); const r = await api.post('/api/admin/digest/test', { kind }); if (kind === 'preview') { out.hidden = false; out.textContent = r.msg; } else if (r.job) { out.hidden = false; watchJob(r.job, out); } else toast(r.msg); } catch (err) { fail(err); }
    b.disabled = false;
  } }, label);
  body.replaceChildren(
    h('div', { class: 'card-form stack' }, h('h3', {}, icon('bell'), '站內通知'),
      h('p', { class: 'muted' }, '不用設定：有人在筆記或回覆中 @你、回覆你參與的討論、或排你在組會報告時，右上角的鈴鐺會亮。')),
    h('div', { class: 'card-form stack' }, h('h3', {}, '每週摘要'),
      h('p', { class: 'muted' }, '內容：接下來兩週的組會與報告人、本週新增論文、誰標註了多少、新論文追蹤待看數；寄給每個填了 Email 的成員時，還會列出他自己要報告的論文與未讀通知。成員在右上角「我的帳號」填 Email、可自行取消訂閱。'),
      h('label', { class: 'check' }, en, '啟用每週自動寄送'),
      h('div', { class: 'grid2' }, field('寄送日', dow), field('時間', hour))),
    h('div', { class: 'card-form stack' }, h('h3', {}, 'Email（SMTP）'),
      h('p', { class: 'muted small' }, 'Gmail：主機 smtp.gmail.com、587、STARTTLS，密碼用 Google 帳戶的「應用程式密碼」。Synology 也可以用 DSM 內建的通知 SMTP 設定。'),
      h('div', { class: 'grid2' }, field('主機', host), field('連接埠', port)),
      h('div', { class: 'grid2' }, field('加密', sec), field('寄件人', from)),
      h('div', { class: 'grid2' }, field('帳號', user), field('密碼', pass))),
    h('div', { class: 'card-form stack' }, h('h3', {}, 'Webhook（Discord／Slack／Teams）'),
      h('p', { class: 'muted small' }, '把摘要貼到實驗室的聊天頻道：在 Discord 頻道設定 →「整合」→「Webhook」建立後貼上網址（Slack 用 Incoming Webhook）。'),
      field('Webhook 網址', hook)),
    h('div', { class: 'row wrap' }, h('button', { class: 'btn primary', onclick: async () => { try { await save(); toast('已儲存'); } catch (e) { fail(e); } } }, '儲存'),
      test('preview', '預覽摘要'), test('email', '寄測試信給我'), test('webhook', '測試 Webhook'), test('send', '立即寄送本週摘要')),
    out);
}

export async function adminZotero(body) {
  const st = await api.get('/api/admin/settings');
  const type = h('select', {}, h('option', { value: 'user', selected: st.zotero_type === 'user' }, '個人 library'), h('option', { value: 'group', selected: st.zotero_type === 'group' }, '群組 library（實驗室共用）'));
  const id = h('input', { value: st.zotero_id, inputmode: 'numeric', placeholder: '例如 1234567' });
  const key = secretInput(st.has_zotero_key, 'Zotero API 金鑰');
  const coll = h('input', { value: st.zotero_collection });
  const imp = h('input', { type: 'checkbox', checked: st.zotero_import === '1' });
  const auto = h('input', { type: 'checkbox', checked: st.zotero_auto === '1' });
  const out = h('div', { class: 'tr-test' });
  const save = () => saveSettings({ zotero_type: type.value, zotero_id: id.value, zotero_key: key.value, zotero_collection: coll.value, zotero_import: imp.checked, zotero_auto: auto.checked }).then(() => { key.value = ''; });
  const last = (await api.get('/api/jobs?kind=zotero'))[0];
  body.replaceChildren(h('div', { class: 'card-form stack' }, h('h3', {}, 'Zotero 同步'),
    h('p', { class: 'muted' }, '推送：論文庫的書目、標籤、重點欄與公開標註（寫成子筆記）同步到 Zotero；分類變成子收藏夾；設定了網站網址時會加上「在論文庫開啟」連結。之後每次同步更新同一個項目，不會重複。'),
    h('p', { class: 'muted' }, '匯入：Zotero 裡新增、論文庫還沒有的項目會進「未歸檔待讀」，有 PDF 附件會一起下載（標籤「Zotero 匯入」）。'),
    h('p', { class: 'hint' }, '取得金鑰：登入 zotero.org → Settings → Security（API keys）→「Create new private key」，勾選要同步的 library 的讀寫權限。個人 library 的 ID 就在同一頁的「Your user ID」；群組 ID 在群組網址 zotero.org/groups/<ID>。'),
    h('div', { class: 'grid2' }, field('類型', type), field('Library ID', id)), field('API 金鑰', key),
    field('Zotero 裡的收藏夾名稱', coll),
    h('label', { class: 'check' }, imp, '同步時一併匯入 Zotero 的新項目'),
    h('label', { class: 'check' }, auto, '每天凌晨自動同步'),
    h('div', { class: 'row wrap' },
      h('button', { class: 'btn primary', onclick: async () => { try { await save(); toast('已儲存'); } catch (e) { fail(e); } } }, '儲存'),
      h('button', { class: 'btn', onclick: async () => { try { await save(); const r = await api.post('/api/admin/zotero/test'); out.replaceChildren(r.msg); } catch (e) { out.replaceChildren(h('span', { class: 'err' }, e.message)); } } }, '測試連線'),
      h('button', { class: 'btn', onclick: async () => { try { await save(); const r = await api.post('/api/admin/zotero/sync'); watchJob(r.job, out); } catch (e) { fail(e); } } }, icon('refresh'), '立即同步')),
    out, last ? h('p', { class: 'muted small' }, `上次同步：${fmtDate(last.created_at)} ${last.state === 'failed' ? '失敗：' : ''}${last.result}`) : null));
}

export async function adminMaint(body, extra) {
  const st = await api.get('/api/admin/settings');
  const ov = S.overview || {};
  const jobsEl = h('table', { class: 'table small' });
  const drawJobs = async () => {
    const js = await api.get('/api/jobs');
    jobsEl.replaceChildren(h('tbody', {}, js.map((j) => h('tr', {}, h('td', { class: 'muted' }, fmtDate(j.created_at)), h('td', {}, j.label),
      h('td', {}, { queued: '排隊中', running: '執行中', done: '完成', failed: '失敗' }[j.state]), h('td', { class: 'muted' }, j.result || j.progress)))));
  };
  const langs = h('input', { value: st.ocr_langs, placeholder: 'eng 或 eng+chi_tra' });
  const ocrAuto = h('input', { type: 'checkbox', checked: st.ocr_auto === '1' });
  const feedEn = h('input', { type: 'checkbox', checked: st.feed_enabled === '1' });
  const feedHour = h('select', {}, Array.from({ length: 24 }, (_, i) => h('option', { value: i, selected: String(i) === st.feed_hour }, `${i}:00`)));
  const refOut = h('div', { class: 'tr-test' }), ocrOut = h('div', { class: 'tr-test' });
  body.replaceChildren(
    ...extra,
    h('div', { class: 'card-form stack' }, h('h3', {}, '自動引用關聯'),
      h('p', { class: 'muted' }, `從每篇論文的參考文獻（期刊卷頁、DOI、arXiv、標題）找出也在論文庫裡的論文，自動建立「引用」關聯。目前有 ${ov.n_auto_links ?? '?'} 條。新上傳論文時會自動更新；被刪掉的自動關聯不會再加回來。`),
      h('div', { class: 'row wrap' },
        h('button', { class: 'btn', onclick: async () => { const r = await api.post('/api/admin/refs', {}); watchJob(r.job, refOut, () => { refreshNav(); drawJobs(); }); } }, '重新分析'),
        h('button', { class: 'btn ghost', onclick: async () => { if (await confirmBox('刪除全部自動引用關聯後重新分析？（手動建立的關聯不受影響；之前刪掉的自動關聯仍不會加回）', '重建')) { const r = await api.post('/api/admin/refs', { reset: true }); watchJob(r.job, refOut, () => { refreshNav(); drawJobs(); }); } } }, '清空後重建')),
      refOut),
    h('div', { class: 'card-form stack' }, h('h3', {}, 'OCR（掃描檔文字辨識）'),
      st.ocr.ok ? h('p', { class: 'muted' }, `已安裝 Tesseract，可用語言：${st.ocr.langs.join('、') || '（無）'}。掃描檔 OCR 後才能搜尋、選字劃線與翻譯；原始檔會保留在 trash 資料夾。`)
        : h('p', { class: 'hint warn' }, '這台伺服器沒有 OCR 工具。Docker 版已內建；本機執行請安裝 tesseract-ocr 並 pip install ocrmypdf。'),
      h('label', { class: 'check' }, ocrAuto, '上傳沒有文字層的 PDF 時自動 OCR'),
      field('辨識語言', langs, '英文論文用 eng；有中文論文用 eng+chi_tra（Docker 版已內建這兩種）'),
      h('div', { class: 'row wrap' },
        h('button', { class: 'btn primary', onclick: async () => { await saveSettings({ ocr_langs: langs.value, ocr_auto: ocrAuto.checked }); toast('已儲存'); } }, '儲存'),
        st.ocr.ok ? h('button', { class: 'btn', onclick: async () => { try { const r = await api.post('/api/admin/ocr/all'); ocrOut.textContent = r.queued ? `已排入 ${r.queued} 個檔案，在下方「背景工作」看進度` : '沒有需要 OCR 的檔案'; drawJobs(); } catch (e) { fail(e); } } }, 'OCR 所有掃描檔') : null),
      ocrOut),
    (() => {
      const chk = h('input', { type: 'checkbox', checked: st.pub_check === '1' });
      const auto = h('input', { type: 'checkbox', checked: st.pub_auto === '1' });
      return h('div', { class: 'card-form stack' }, h('h3', {}, icon('merge'), '預印本與重複論文'),
        h('p', { class: 'muted' }, '每週日清晨檢查只有 arXiv 編號的論文是否已正式發表（arXiv 記錄的 DOI，或 Crossref 用標題＋第一作者比對），並找出重複的論文。'),
        h('label', { class: 'check' }, chk, '每週自動檢查預印本是否已發表'),
        h('label', { class: 'check' }, auto, '找到正式版且確定是同一篇時自動更新書目（DOI、期刊、年份）；不確定的列在「預印本與重複論文」等人確認'),
        h('div', { class: 'row wrap' }, h('button', { class: 'btn primary', onclick: async () => { await saveSettings({ pub_check: chk.checked, pub_auto: auto.checked }); toast('已儲存'); } }, '儲存'),
          h('a', { class: 'btn ghost', href: '#/versions' }, '查看與立即檢查')));
    })(),
    S.site.ai?.enabled ? (() => {
      const out = h('div', { class: 'tr-test' });
      return h('div', { class: 'card-form stack' }, h('h3', {}, icon('sliders'), 'AI 批次抽取參數'),
        h('p', { class: 'muted' }, `讓 AI 逐篇讀全文，依「參數 → 名稱對照表」抽出 g、κ、頻率、材料等數值（換成統一單位）。只處理還沒有任何參數的論文；人工填的不會被覆蓋。費用與「逐篇填寫重點欄」相近（Claude Haiku 約每篇 US$0.02–0.03）。`),
        h('div', { class: 'row wrap' }, h('button', { class: 'btn', onclick: async () => { if (!await confirmBox('對所有還沒有參數的論文執行 AI 抽取？', '開始')) return; try { const r = await api.post('/api/params/batch', { all: true }); watchJob(r.job, out, drawJobs); } catch (e) { fail(e); } } }, icon('spark'), '開始抽取'),
          h('a', { class: 'btn ghost', href: '#/params?tab=defs' }, '名稱對照表')), out);
    })() : null,
    h('div', { class: 'card-form stack' }, h('h3', {}, '新論文追蹤排程'),
      h('label', { class: 'check' }, feedEn, '每天自動檢查'), field('檢查時間', feedHour),
      (() => {
        const key = secretInput(st.has_openalex_key, '貼上 OpenAlex API key');
        return h('div', { class: 'stack' }, h('b', {}, '期刊搜尋：OpenAlex 金鑰'),
          h('p', { class: 'muted small' }, '免費：到 openalex.org 註冊帳號，在帳號設定頁複製 API key。有金鑰時「期刊搜尋」會搜尋標題＋摘要並支援交集／聯集；沒有時改用 Crossref（免金鑰，主要只比對標題）。'),
          key, h('div', { class: 'row' }, h('button', { class: 'btn small', onclick: async () => { await saveSettings({ openalex_key: key.value }); key.value = ''; toast('已儲存'); } }, '儲存金鑰'),
            st.has_openalex_key ? h('button', { class: 'btn small ghost', onclick: async () => { await saveSettings({ clear_openalex_key: true }); toast('已清除'); route(); } }, '清除金鑰') : null));
      })(),
      h('div', { class: 'row' }, h('button', { class: 'btn primary', onclick: async () => { await saveSettings({ feed_enabled: feedEn.checked, feed_hour: feedHour.value }); toast('已儲存'); } }, '儲存'),
        h('a', { class: 'btn ghost', href: '#/feeds' }, '管理追蹤清單'))),
    h('div', { class: 'card-form stack' }, h('h3', {}, '背景工作'), h('div', { class: 'row' }, h('button', { class: 'btn small ghost', onclick: drawJobs }, icon('refresh'), '重新整理')), jobsEl));
  drawJobs();
}

