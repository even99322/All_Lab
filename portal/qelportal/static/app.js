// QEL Lab 大程式網頁：原生 JavaScript（ES module），不需要編譯。手機、平板、電腦共用，可安裝成 App。
const $ = (s, el = document) => el.querySelector(s);
const S = { site: null, user: null, tags: null, timers: [] };
const CAT_ORDER = ["Project", "Level", "Board Design", "Data Analysis", "Measurement", "Paper Topic", "Other"];
const STATE = { idle: "閒置", preparing: "準備中", running: "量測中", paused: "已暫停", finished: "完成", aborted: "已中斷",
  failed: "失敗", updating: "更新中", offline: "離線" };

// ---------------------------------------------------------------- 工具
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(9)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}
function when(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000), s = (Date.now() - d) / 1000;
  if (s < 60) return "剛剛"; if (s < 3600) return `${Math.round(s / 60)} 分鐘前`; if (s < 86400) return `${Math.round(s / 3600)} 小時前`;
  return d.toLocaleDateString("zh-TW") + " " + d.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" });
}
function size(b) { const u = ["B", "KB", "MB", "GB"]; let i = 0; b = +b || 0; while (b >= 1024 && i < 3) { b /= 1024; i++; } return `${b.toFixed(i ? 1 : 0)} ${u[i]}`; }
function dur(s) { if (s == null || !isFinite(s)) return ""; s = Math.max(0, Math.round(s)); const hh = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return hh ? `${hh}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
const ICONS = {
  book: '<path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5"/><path d="M8 7h6"/>',
  gauge: '<path d="M12 14l4-4"/><path d="M3.3 17a9 9 0 1 1 17.4 0"/>',
  chart: '<path d="M4 19V5"/><path d="M4 19h16"/><path d="M7 15l4-5 3 3 5-7"/>',
  link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
  home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>', pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/>',
  relay: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 12h10"/>',
  wrench: '<path d="M14 6a4 4 0 0 0 5 5l-9 9-3-3 9-9a4 4 0 0 0-2-2z"/>', tag: '<path d="M3 3h8l10 10-8 8L3 11z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  download: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/>',
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "icon"); s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "1.8"); s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round"); s.innerHTML = ICONS[name] || ICONS.link; return s;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", credentials: "same-origin", headers: { "X-QEL": "1", Accept: "application/json" } };
  if (opts.raw) { init.body = opts.raw; init.headers["Content-Type"] = opts.type || "application/octet-stream"; }
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api/v1" + path, init);
  let j = null;
  try { j = await r.json(); } catch { j = null; }
  if (r.status === 401 && !opts.quiet) { S.user = null; location.hash = "#/login"; throw new Error("請先登入"); }
  if (!r.ok) throw new Error((j && (j.error || j.detail)) || `錯誤 ${r.status}`);
  return j;
}
const can = (m) => !!(S.user && S.user.modules.includes(m));
const tagUrl = (t) => `#/tags/${encodeURIComponent(t)}`;

// ---------------------------------------------------------------- 標籤
async function loadTags(force) {
  if (!S.tags || force) S.tags = await api("/tags");
  return S.tags;
}
function tagInfo(name) { return (S.tags?.tags || []).find((t) => t.name.toLowerCase() === String(name).toLowerCase()); }
function tagChip(name, extra) {
  const t = tagInfo(name);
  const dot = h("span", { class: "dot", style: t?.color ? `background:${t.color}` : null });
  return h("a", { class: "chip", href: tagUrl(name), title: t?.description || "" }, dot, name, extra || null);
}

// ---------------------------------------------------------------- 版面
function nav() {
  const items = [["#/", "首頁"], ["#/data", "數據"], ["#/tags", "標籤"]];
  if (can("labcontrol")) items.push(["#/measure", "量測"]);
  if (can("paperlib")) items.push([S.site.paperlib_url, "論文庫", true]);
  items.push(["#/downloads", "下載"]);
  if (S.user?.manager) items.push(["#/admin", "管理"]);
  const cur = location.hash || "#/";
  $("#nav").replaceChildren(...(S.user ? items : []).map(([href, label, ext]) =>
    h("a", { href, class: !ext && (cur === href || (href !== "#/" && cur.startsWith(href))) ? "on" : null, target: ext ? "_blank" : null, rel: ext ? "noopener" : null }, label)));
  $("#who").replaceChildren(...(S.user ? [
    h("span", { class: "hide-sm" }, S.user.display_name + (S.user.owner ? "（站長）" : "")),
    h("button", { class: "btn small", onclick: logout }, "登出")] : []));
}
async function logout() {
  try { await api("/auth/logout", { method: "POST", body: {} }); } catch { /* 已經登出 */ }
  S.user = null; location.hash = "#/login";
}

// ---------------------------------------------------------------- 路由
async function route() {
  S.timers.forEach(clearInterval); S.timers = [];
  const hash = location.hash || "#/";
  const [path, qs] = hash.slice(1).split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(qs || "");
  const view = $("#view");
  if (!S.user && !["login", "register"].includes(parts[0])) { location.hash = "#/login"; return; }
  nav();
  view.replaceChildren(h("p", { class: "muted pad" }, "載入中…"));
  try {
    switch (parts[0] || "") {
      case "": return await viewHome(view);
      case "login": return viewLogin(view);
      case "register": return viewRegister(view);
      case "data": return parts[1] ? await viewDataset(view, +parts[1]) : await viewData(view, params);
      case "tags": return parts[1] ? await viewTag(view, parts[1]) : await viewTags(view);
      case "measure": return await viewMeasure(view);
      case "downloads": return await viewDownloads(view);
      case "admin": return await viewAdmin(view, parts[1] || "users");
      default: location.hash = "#/";
    }
  } catch (e) {
    view.replaceChildren(h("div", { class: "card" }, h("p", { class: "err" }, e.message)));
  }
}

// ---------------------------------------------------------------- 登入、註冊
function viewLogin(view) {
  if (S.user) { location.hash = "#/"; return; }
  const err = h("p", { class: "err" });
  const code = h("label", { class: "field", style: "display:none" }, "兩步驟驗證碼", h("input", { name: "code", inputmode: "numeric", autocomplete: "one-time-code" }));
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const f = new FormData(form);
    try {
      const r = await api("/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password"), code: f.get("code") || "", client: "web" }, quiet: true });
      if (r.need_2fa) { code.style.display = ""; code.querySelector("input").focus(); err.textContent = "請輸入驗證 App 上的 6 位數字"; return; }
      S.user = r.user; location.hash = "#/";
    } catch (ex) { err.textContent = ex.message; }
  } },
  h("label", { class: "field" }, "帳號（與論文庫相同）", h("input", { name: "username", autocomplete: "username", required: true, autofocus: true })),
  h("label", { class: "field" }, "密碼", h("input", { name: "password", type: "password", autocomplete: "current-password", required: true })),
  code, err, h("button", { class: "btn primary", style: "width:100%;justify-content:center" }, "登入"));
  view.replaceChildren(h("div", { class: "login" }, h("div", { class: "card" },
    h("h1", null, S.site.name), h("p", { class: "muted" }, "用論文庫的帳號登入，就能使用各個模塊。"), form,
    h("p", { class: "muted", style: "margin-top:16px;font-size:13px" }, "還沒有帳號？", h("a", { href: "#/register" }, "申請註冊"), "（站長審核後開通）"))));
}

function viewRegister(view) {
  const err = h("p", { class: "err" });
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const f = Object.fromEntries(new FormData(form));
    if (f.password !== f.password2) { err.textContent = "兩次密碼不一樣"; return; }
    try {
      const r = await api("/auth/register", { method: "POST", body: f, quiet: true });
      view.replaceChildren(h("div", { class: "login" }, h("div", { class: "card" }, h("h1", null, "已送出申請"), h("p", null, r.message), h("a", { class: "btn", href: "#/login" }, "回到登入"))));
    } catch (ex) { err.textContent = ex.message; }
  } },
  h("label", { class: "field" }, "用戶名稱（顯示用）", h("input", { name: "display_name", required: true, maxlength: 40 })),
  h("label", { class: "field" }, "帳號（3–32 個英文、數字或 . _ -）", h("input", { name: "username", required: true, pattern: "[A-Za-z0-9._-]{3,32}", autocomplete: "username" })),
  h("label", { class: "field" }, "密碼（至少 8 個字元）", h("input", { name: "password", type: "password", minlength: 8, required: true, autocomplete: "new-password" })),
  h("label", { class: "field" }, "再輸入一次密碼", h("input", { name: "password2", type: "password", minlength: 8, required: true, autocomplete: "new-password" })),
  h("label", { class: "field" }, "Email（審核結果會寄到這裡）", h("input", { name: "email", type: "email", required: true })),
  h("label", { class: "field" }, "申請說明（選填）", h("textarea", { name: "note", rows: 2, maxlength: 500 })),
  h("input", { name: "website", style: "display:none", tabindex: "-1", autocomplete: "off" }),
  err, h("button", { class: "btn primary", style: "width:100%;justify-content:center" }, "送出申請"));
  view.replaceChildren(h("div", { class: "login" }, h("div", { class: "card" }, h("h1", null, "申請帳號"),
    h("p", { class: "muted" }, "帳號建立在論文庫，大程式與所有模塊共用。"), form, h("p", null, h("a", { href: "#/login" }, "← 回到登入")))));
}

// ---------------------------------------------------------------- 首頁
async function viewHome(view) {
  const [mods, recent] = await Promise.all([api("/modules"), can("lablogviewer") || can("labcontrol") || can("paperlib") ? api("/datasets?limit=6").catch(() => null) : null]);
  const cards = mods.modules.filter((m) => m.access === true || m.access === "owner").map((m) => {
    const btns = [];
    if (!m.allowed) btns.push(h("span", { class: "muted" }, "尚未開放，請洽站長"));
    else if (m.id === "paperlib") btns.push(h("a", { class: "btn primary", href: S.site.paperlib_url, target: "_blank", rel: "noopener" }, "開啟論文庫"));
    else if (m.id === "labcontrol") btns.push(h("a", { class: "btn primary", href: "#/measure" }, "量測狀態"), h("a", { class: "btn", href: "#/downloads" }, "桌面版"));
    else if (m.id === "lablogviewer") btns.push(h("a", { class: "btn primary", href: "#/data" }, "瀏覽數據"), h("a", { class: "btn", href: "#/downloads" }, "桌面版"));
    else if (m.id === "monitor") btns.push(h("a", { class: "btn", href: "#/downloads" }, "下載"), h("a", { class: "btn", href: "#/admin/status" }, "系統狀態"));
    return h("div", { class: `card ${m.allowed ? "" : "off"}` }, h("h3", null, icon(m.icon), m.name),
      h("p", { class: "desc" }, m.description), h("div", { class: "row" }, ...btns,
        m.latest ? h("span", { class: "muted", style: "font-size:12px;margin-left:auto" }, `最新 v${m.latest}`) : null));
  });
  const parts = [h("h1", null, `${S.user.display_name}，你好`), h("div", { class: "grid" }, ...cards)];
  if (recent && recent.items.length) {
    await loadTags().catch(() => null);
    parts.push(h("h2", null, "最近的數據"), datasetTable(recent.items), h("p", null, h("a", { href: "#/data" }, "全部數據 →")));
  }
  const st = h("div", { class: "row", style: "gap:18px" }, h("span", { class: "muted" }, "檢查中…"));
  parts.push(h("h2", null, "系統狀態"), h("div", { class: "card" }, st));
  view.replaceChildren(...parts);
  api("/health").then((hh) => st.replaceChildren(
    statusDot("大程式", true, `v${hh.portal.version}`), statusDot("論文庫", hh.paperlib.online, hh.paperlib.version ? `v${hh.paperlib.version}` : hh.paperlib.error),
    statusDot("量測中繼站", hh.labhub.online, hh.labhub.version ? `v${hh.labhub.version}` : hh.labhub.error))).catch((e) => st.replaceChildren(h("span", { class: "err" }, e.message)));
}
function statusDot(label, ok, sub) {
  return h("span", { class: "status" }, h("span", { class: `dot ${ok ? "on" : "off"}` }), h("b", null, label), h("span", { class: "muted" }, sub || (ok ? "正常" : "離線")));
}

// ---------------------------------------------------------------- 數據
function datasetTable(items) {
  return h("div", { class: "tablewrap" }, h("table", null,
    h("thead", null, h("tr", null, h("th", null, "數據"), h("th", null, "標籤"), h("th", { class: "hide-sm" }, "來源"), h("th", null, "時間"))),
    h("tbody", null, items.map((d) => h("tr", null,
      h("td", null, h("a", { href: `#/data/${d.id}` }, d.name), d.has_scheme ? h("span", { class: "muted", title: "含量測設置" }, " ⚙") : null),
      h("td", null, h("div", { class: "chips" }, d.tags.map((t) => tagChip(t)))),
      h("td", { class: "hide-sm muted" }, [d.source?.host, d.created_by].filter(Boolean).join(" · ")),
      h("td", { class: "muted", style: "white-space:nowrap" }, when(d.created_at)))))));
}

async function viewData(view, params) {
  await loadTags();
  const q = h("input", { type: "search", placeholder: "搜尋檔名或路徑", value: params.get("q") || "", style: "flex:1;min-width:160px" });
  const sel = h("select", null, h("option", { value: "" }, "所有標籤"), ...S.tags.tags.filter((t) => t.datasets).map((t) =>
    h("option", { value: t.name, selected: t.name === params.get("tag") || null }, `${t.name}（${t.datasets}）`)));
  const go = () => { const p = new URLSearchParams(); if (q.value.trim()) p.set("q", q.value.trim()); if (sel.value) p.set("tag", sel.value); location.hash = "#/data" + (p.toString() ? "?" + p : ""); };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  sel.addEventListener("change", go);
  const r = await api(`/datasets?limit=200&q=${encodeURIComponent(params.get("q") || "")}&tag=${encodeURIComponent(params.get("tag") || "")}`);
  view.replaceChildren(h("h1", null, "數據"),
    h("p", { class: "muted" }, "量測模塊存檔時自動登錄，含量測設置與標籤。在桌面版讀檔模塊可以直接開啟；拖到量測模塊可以套用設置。"),
    h("div", { class: "row", style: "margin-bottom:12px" }, q, sel, h("button", { class: "btn", onclick: go }, "搜尋")),
    r.items.length ? datasetTable(r.items) : h("div", { class: "card empty" }, "沒有符合的數據"),
    r.total > r.items.length ? h("p", { class: "muted" }, `共 ${r.total} 筆，只顯示最新 ${r.items.length} 筆`) : null);
}

function schemeLines(s) {
  if (!s) return ["（沒有量測設置）"];
  const out = [`方案：${s.name || "未命名"}`];
  const nodes = s.graph?.nodes || s.blocks || [];
  for (const n of nodes) {
    const t = n.title || n.target || n.name || n.kind;
    if (n.mode === "sweep" || "start" in n) out.push(`• ${t}：${n.start} → ${n.stop} ${n.unit || ""}（步進 ${n.step ?? n.points ?? ""}）`);
    else if ("value" in n) out.push(`• ${t}：${n.value} ${n.unit || ""}`);
    else if (n.kind === "measure") out.push(`• 量測 ${n.instrument || t}：${(n.traces || []).join("、")}`);
    else if (n.kind === "save") out.push(`• 存檔 ${n.file_name || ""}`);
    else if (n.kind === "wait") out.push(`• 等待 ${n.seconds} s`);
  }
  return out;
}

async function viewDataset(view, id) {
  await loadTags();
  const d = await api(`/datasets/${id}`);
  const chips = h("div", { class: "chips" });
  const input = h("input", { list: "taglist", placeholder: "加標籤", style: "width:140px" });
  const list = h("datalist", { id: "taglist" }, S.tags.tags.map((t) => h("option", { value: t.name })));
  const papers = h("div", null, h("p", { class: "muted" }, "載入論文…"));
  let tags = [...d.tags];
  const save = async (next) => {
    try { const r = await api(`/datasets/${id}`, { method: "PATCH", body: { tags: next } }); tags = r.tags; drawTags(); loadPapers(); loadTags(true); }
    catch (e) { toast(e.message); }
  };
  const drawTags = () => chips.replaceChildren(...tags.map((t) => tagChip(t, h("span", { class: "x", title: "移除", onclick: (e) => { e.preventDefault(); save(tags.filter((x) => x !== t)); } }, "×"))));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) { save([...tags, input.value.trim()]); input.value = ""; } });
  const loadPapers = async () => {
    try {
      const r = await api(`/datasets/${id}/papers`);
      papers.replaceChildren(...(r.groups.length ? r.groups.map(paperGroup) : [h("p", { class: "muted" }, "加上標籤後，這裡會列出每個標籤對應的論文。")]));
    } catch (e) { papers.replaceChildren(h("p", { class: "err" }, e.message)); }
  };
  drawTags(); loadPapers();
  const src = d.source || {};
  view.replaceChildren(h("p", null, h("a", { href: "#/data" }, "← 數據")), h("h1", null, d.name),
    h("div", { class: "card" }, h("dl", { class: "kv" },
      h("dt", null, "路徑"), h("dd", null, d.path || "—"),
      d.hub_file ? [h("dt", null, "NAS"), h("dd", null, `Hub：${d.hub_file.node}/${d.hub_file.rel}`)] : null,
      h("dt", null, "來源"), h("dd", null, [src.module, src.host, src.user || d.created_by].filter(Boolean).join(" · ") || "—"),
      h("dt", null, "登錄"), h("dd", null, `${when(d.created_at)}（${d.created_by}）`)),
      h("div", { class: "row", style: "margin-top:12px" }, d.downloadable
        ? h("a", { class: "btn primary", href: `/api/v1/datasets/${id}/file` }, icon("download"), "下載數據檔")
        : h("span", { class: "muted" }, "這個檔案只在量測電腦上，請在那台電腦用讀檔模塊開啟。"))),
    h("h2", null, "標籤"), h("div", { class: "card" }, h("div", { class: "row" }, chips, input, list)),
    h("h2", null, "量測設置"), h("pre", { class: "scheme" }, schemeLines(d.scheme).join("\n")),
    h("p", { class: "muted", style: "font-size:13px" }, "在桌面版：把這個數據檔從讀檔模塊拖到量測模塊，就會套用這組設置。"),
    h("h2", null, "相關論文"), papers);
}

function paperGroup(g) {
  return h("div", { class: "card", style: "margin-bottom:12px" },
    h("div", { class: "row" }, tagChip(g.tag), h("span", { class: "spacer" }), g.no_access ? null : h("a", { href: g.tag_url, target: "_blank", rel: "noopener", style: "font-size:13px" }, "在論文庫看全部 →")),
    g.no_access ? h("p", { class: "muted" }, "站長還沒有開放論文模塊給你。")
      : g.papers.length ? g.papers.map((p) => paperRow(p)) : h("p", { class: "muted" }, "還沒有論文。到標籤頁可以連結論文。"));
}
function paperRow(p, extra) {
  return h("div", { class: "paper row" }, h("div", { style: "flex:1;min-width:0" },
    h("a", { class: "t", href: p.url, target: "_blank", rel: "noopener" }, p.title),
    h("div", { class: "m" }, [p.first_author, p.year, p.venue].filter(Boolean).join(" · "), p.linked ? " · 手動連結" : "")), extra || null);
}

// ---------------------------------------------------------------- 標籤
async function viewTags(view) {
  const t = await loadTags(true);
  const filter = h("input", { type: "search", placeholder: "篩選標籤", style: "flex:1;min-width:140px" });
  const body = h("div");
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    const groups = {};
    for (const x of t.tags) if (!f || x.name.toLowerCase().includes(f) || (x.aliases || []).some((a) => a.toLowerCase().includes(f))) (groups[x.category] ||= []).push(x);
    body.replaceChildren(...t.categories.filter((c) => groups[c.key]).map((c) => h("div", { class: "cat" },
      h("h3", null, `${c.name}（${c.key}）${c.single ? " · 每筆數據只能選一個" : ""}`),
      h("div", { class: "chips" }, groups[c.key].map((x) => tagChip(x.name, h("span", { class: "muted" }, ` ${x.paper_count || 0} 篇 · ${x.datasets || 0} 筆`)))))));
    if (!body.children.length) body.append(h("div", { class: "empty" }, "沒有符合的標籤"));
  };
  filter.addEventListener("input", draw); draw();
  const name = h("input", { placeholder: "新標籤名稱", required: true, style: "min-width:140px" });
  const cat = h("select", null, t.categories.map((c) => h("option", { value: c.key, selected: c.key === "Other" || null }, c.name)));
  const add = h("form", { class: "row", onsubmit: async (e) => {
    e.preventDefault();
    try { await api("/tags", { method: "POST", body: { name: name.value, category: cat.value } }); toast("已新增"); location.hash = tagUrl(name.value.trim()); }
    catch (ex) { toast(ex.message); }
  } }, name, cat, h("button", { class: "btn" }, "新增"));
  view.replaceChildren(h("h1", null, "共用標籤"),
    h("p", { class: "muted" }, "量測、讀檔、論文模塊用同一批標籤。點標籤看對應的論文與數據。"),
    h("div", { class: "row", style: "margin-bottom:16px" }, filter), body, h("h2", null, "新增標籤"), h("div", { class: "card" }, add));
}

async function viewTag(view, name) {
  await loadTags(true);
  const t = tagInfo(name) || { name, category: "Other", aliases: [], description: "", color: "" };
  const mine = S.user.manager || t.created_by === S.user.username;
  const papersBox = h("div", null, h("p", { class: "muted" }, "載入論文…"));
  const loadPapers = async () => {
    if (!can("paperlib")) { papersBox.replaceChildren(h("p", { class: "muted" }, "站長還沒有開放論文模塊給你。")); return; }
    try {
      const r = await api(`/tags/${encodeURIComponent(name)}/papers`);
      papersBox.replaceChildren(...(r.papers.length ? r.papers.map((p) => paperRow(p, p.linked
        ? h("button", { class: "btn small", onclick: async () => { await api(`/tags/${encodeURIComponent(name)}/papers/${p.id}`, { method: "DELETE" }); loadPapers(); } }, "取消連結") : null))
        : [h("p", { class: "muted" }, "還沒有論文。論文庫裡標了同名標籤的論文會自動出現，也可以在下面搜尋並連結。")]),
      h("p", null, h("a", { href: r.tag_url, target: "_blank", rel: "noopener" }, "在論文庫開啟這個標籤 →")));
    } catch (e) { papersBox.replaceChildren(h("p", { class: "err" }, e.message)); }
  };
  loadPapers();
  const sq = h("input", { type: "search", placeholder: "搜尋論文庫（標題、作者、全文）", style: "flex:1;min-width:160px" });
  const results = h("div");
  const search = async () => {
    if (!sq.value.trim()) return;
    try {
      const r = await api(`/papers?q=${encodeURIComponent(sq.value.trim())}&limit=15`);
      results.replaceChildren(...(r.items.length ? r.items.map((p) => paperRow(p, h("button", { class: "btn small", onclick: async () => {
        try { await api(`/tags/${encodeURIComponent(name)}/papers`, { method: "POST", body: { paper_id: p.id } }); toast("已連結"); loadPapers(); } catch (e) { toast(e.message); }
      } }, "連結"))) : [h("p", { class: "muted" }, "找不到")]));
    } catch (e) { results.replaceChildren(h("p", { class: "err" }, e.message)); }
  };
  sq.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  const ds = await api(`/datasets?tag=${encodeURIComponent(name)}&limit=50`).catch(() => ({ items: [] }));
  const parts = [h("p", null, h("a", { href: "#/tags" }, "← 標籤")),
    h("h1", { class: "row" }, h("span", { class: "dot", style: `width:14px;height:14px;${t.color ? "background:" + t.color : ""}` }), t.name),
    h("p", { class: "muted" }, `${(S.tags.categories.find((c) => c.key === t.category) || {}).name || t.category}`,
      t.aliases?.length ? ` · 別名：${t.aliases.join("、")}` : "", t.description ? ` · ${t.description}` : ""),
    h("h2", null, "對應論文"), h("div", { class: "card" }, papersBox),
    can("paperlib") ? h("div", { class: "card", style: "margin-top:12px" }, h("div", { class: "row" }, sq, h("button", { class: "btn", onclick: search }, "搜尋")), results) : null,
    h("h2", null, "有這個標籤的數據"), ds.items.length ? datasetTable(ds.items) : h("div", { class: "card empty" }, "還沒有數據")];
  if (mine) {
    const f = h("form", { onsubmit: async (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(f));
      try {
        await api("/tags", { method: "POST", body: { name: t.name, category: v.category, color: v.color, description: v.description,
          aliases: v.aliases.split(/[,，、]/).map((s) => s.trim()).filter(Boolean) } });
        toast("已儲存"); route();
      } catch (ex) { toast(ex.message); }
    } },
    h("label", { class: "field" }, "分類", h("select", { name: "category" }, S.tags.categories.map((c) => h("option", { value: c.key, selected: c.key === t.category || null }, c.name)))),
    h("label", { class: "field" }, "顏色", h("input", { name: "color", type: "color", value: t.color || "#5d6d7e" })),
    h("label", { class: "field" }, "說明", h("input", { name: "description", value: t.description || "" })),
    h("label", { class: "field" }, "別名（逗號分隔，例如舊寫法）", h("input", { name: "aliases", value: (t.aliases || []).join(", ") })),
    h("div", { class: "row" }, h("button", { class: "btn primary" }, "儲存"),
      S.user.manager ? h("button", { type: "button", class: "btn", onclick: async () => {
        const nn = prompt("新名稱（數據上的標籤會一起改）", t.name); if (!nn || nn === t.name) return;
        try { await api(`/tags/${encodeURIComponent(t.name)}/rename`, { method: "POST", body: { name: nn } }); location.hash = tagUrl(nn); } catch (ex) { toast(ex.message); }
      } }, "改名") : null,
      S.user.manager ? h("button", { type: "button", class: "btn danger", onclick: async () => {
        if (!confirm(`刪除標籤「${t.name}」？（數據上的標籤保留）`)) return;
        try { await api(`/tags/${encodeURIComponent(t.name)}`, { method: "DELETE" }); location.hash = "#/tags"; } catch (ex) { toast(ex.message); }
      } }, "刪除") : null));
    parts.push(h("h2", null, "編輯標籤"), h("div", { class: "card" }, f));
  }
  view.replaceChildren(...parts);
}

// ---------------------------------------------------------------- 量測（Hub 經大程式轉送）
async function viewMeasure(view) {
  const grid = h("div", { class: "grid" });
  const meta = h("p", { class: "muted" });
  const draw = async () => {
    let s;
    try { s = await api("/relay/state"); } catch (e) { grid.replaceChildren(h("div", { class: "card err" }, e.message)); return; }
    meta.textContent = `量測中繼站 v${s.version} · ${new Date(s.time * 1000).toLocaleTimeString("zh-TW")} 更新`;
    grid.replaceChildren(...(s.nodes.length ? s.nodes.map(nodeCard) : [h("div", { class: "card empty" }, "沒有量測節點。在量測電腦的 Lab Control 按「🛰 量測節點」。")]));
  };
  view.replaceChildren(h("h1", null, "量測"), meta,
    h("p", { class: "muted" }, "手機、平板也能看進度與暫停 / 停止；設計量測方案請用桌面版量測模塊。"), grid);
  await draw();
  S.timers.push(setInterval(draw, 3000));
}
function nodeCard(n) {
  const st = n.state || "idle", run = n.run;
  const active = n.online && ["running", "paused", "preparing"].includes(st);
  const kids = [h("div", { class: "row" }, h("b", null, n.name), h("span", { class: "chip" }, STATE[st] || st), n.simulate ? h("span", { class: "chip" }, "模擬") : null,
    n.version ? h("span", { class: "muted", style: "font-size:12px" }, `v${n.version}`) : null),
  h("div", { class: "muted", style: "font-size:13px" }, `${n.user || ""}@${n.host || ""} · ${n.online ? "上線中" : "離線"}`)];
  if (run) {
    const tot = run.total || 0, idx = Math.min(run.index || 0, tot || 0), pct = tot ? 100 * idx / tot : 0;
    kids.push(h("div", { style: "margin-top:10px" }, h("div", null, run.name || "量測"), h("div", { class: "muted", style: "font-size:13px" }, `由 ${run.by || "?"} 開始`),
      h("div", { class: "bar" }, h("i", { style: `width:${pct.toFixed(1)}%` })),
      h("div", { class: "row muted", style: "font-size:13px" }, `${idx} / ${tot || "?"} 點（${pct.toFixed(0)}%）`, h("span", { class: "spacer" }), active && run.eta != null ? `剩餘 ${dur(run.eta)}` : "")));
  }
  if (active) kids.push(h("div", { class: "row", style: "margin-top:10px" },
    h("button", { class: "btn", onclick: () => command(n.name, st === "paused" ? "resume" : "pause") }, st === "paused" ? "▶ 繼續" : "⏸ 暫停"),
    h("button", { class: "btn danger", onclick: () => { if (confirm(`確定要停止 ${n.name} 的量測？`)) command(n.name, "stop"); } }, "⏹ 停止")));
  return h("div", { class: `card ${n.online ? "" : "off"}` }, ...kids);
}
async function command(node, cmd) {
  try {
    const r = await api(`/relay/nodes/${encodeURIComponent(node)}/commands?wait=30`, { method: "POST", body: { cmd, args: {}, from: { user: S.user.display_name } } });
    toast(r.reply && r.reply.ok !== false ? "已送出" : (r.reply?.error || "節點沒有回應"));
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- 下載
async function viewDownloads(view) {
  const mods = (await api("/modules")).modules.filter((m) => m.kind === "desktop" && m.allowed);
  const os = /Mac/i.test(navigator.platform) ? "macOS" : /Win/i.test(navigator.platform) ? "Windows" : /Android/i.test(navigator.userAgent) ? "Android" : /iPhone|iPad/i.test(navigator.userAgent) ? "iOS" : "";
  view.replaceChildren(h("h1", null, "下載與安裝"),
    h("div", { class: "card" }, h("h3", null, icon("home"), "手機、平板（Android、iPhone、iPad）"),
      h("p", { class: "desc" }, "直接用這個網站。可以安裝成 App：Android（Chrome）選單 →「安裝應用程式 / 加到主畫面」；iPhone、iPad（Safari）分享 →「加入主畫面」。"),
      os === "Android" || os === "iOS" ? h("p", null, "你現在用的是 ", os, "。") : null),
    h("h2", null, "電腦（Windows、macOS）"),
    h("p", { class: "muted" }, "先安裝「QEL Lab 大程式（桌面）」，登入後它會自動安裝、更新你有權限的模塊（量測、讀檔、通信），每個模塊各自更新。"),
    h("div", { class: "grid" }, mods.map((m) => h("div", { class: "card" }, h("h3", null, icon(m.icon), m.name), h("p", { class: "desc" }, m.description),
      m.latest ? h("a", { class: "btn primary", href: `/api/v1/modules/${m.id}/releases/${encodeURIComponent(m.latest)}.zip` }, icon("download"), `下載 v${m.latest}`)
        : h("span", { class: "muted" }, "還沒有發佈版本")))));
}

// ---------------------------------------------------------------- 管理（站長）
async function viewAdmin(view, tab) {
  if (!S.user.manager) { view.replaceChildren(h("div", { class: "card err" }, "需要站長身分")); return; }
  const tabs = [["users", "使用者與權限"], ["releases", "模塊發佈"], ["status", "系統狀態"], ["settings", "設定"], ["audit", "紀錄"]];
  const body = h("div");
  view.replaceChildren(h("h1", null, "管理"), h("div", { class: "tabs" }, tabs.map(([k, l]) => h("a", { href: `#/admin/${k}`, class: k === tab ? "on" : null }, l))), body);
  if (tab === "users") return adminUsers(body);
  if (tab === "releases") return adminReleases(body);
  if (tab === "status") return adminStatus(body);
  if (tab === "settings") return adminSettings(body);
  if (tab === "audit") return adminAudit(body);
}
function sw(checked, onchange, disabled) {
  const i = h("input", { type: "checkbox", checked: checked || null, disabled: disabled || null });
  i.addEventListener("change", () => onchange(i.checked, i));
  return h("label", { class: "switch" }, i, h("span"));
}
async function adminUsers(body) {
  const r = await api("/admin/users");
  const set = async (u, patch, input) => {
    try { await api(`/admin/users/${encodeURIComponent(u)}`, { method: "PUT", body: patch }); toast("已更新"); }
    catch (e) { toast(e.message); if (input) input.checked = !input.checked; }
  };
  const settings = await api("/admin/settings");
  const defRow = h("div", { class: "row", style: "gap:18px" }, r.modules.map((m) => h("span", { class: "row" }, sw(settings.default_access[m.id], async (v, i) => {
    try { await api("/admin/settings", { method: "PUT", body: { default_access: { ...settings.default_access, [m.id]: v } } }); settings.default_access[m.id] = v; toast("已更新預設"); }
    catch (e) { toast(e.message); i.checked = !v; }
  }), m.name)));
  body.replaceChildren(
    h("p", { class: "muted" }, "帳號以論文庫為準（新增帳號、審核註冊、停用帳號、改密碼請到", h("a", { href: r.paperlib_admin_url, target: "_blank", rel: "noopener" }, "論文庫的管理頁"), "）。這裡開關每個人能用哪些模塊。站長永遠可以使用全部模塊。"),
    h("div", { class: "card", style: "margin-bottom:14px" }, h("b", null, "新帳號的預設權限"), h("div", { style: "margin-top:8px" }, defRow)),
    h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "使用者"), ...r.modules.map((m) => h("th", null, m.name)), h("th", null, "大程式"), h("th", { class: "hide-sm" }, "最後使用"))),
      h("tbody", null, r.users.map((u) => h("tr", null,
        h("td", null, h("b", null, u.display_name), h("div", { class: "muted", style: "font-size:12px" }, u.username, u.owner ? " · 站長" : "", u.disabled_in_paperlib ? " · 論文庫已停用" : "")),
        ...r.modules.map((m) => h("td", null, sw(u.access[m.id], (v, i) => set(u.username, { access: { [m.id]: v } }, i), u.owner))),
        h("td", null, sw(!u.blocked, (v, i) => set(u.username, { blocked: !v }, i), u.owner || u.username === S.user.username)),
        h("td", { class: "hide-sm muted" }, when(u.last_seen))))))));
}
async function adminReleases(body) {
  const mods = (await api("/modules")).modules.filter((m) => m.kind === "desktop" || m.kind === "library");
  const sel = h("select", { name: "module" }, mods.map((m) => h("option", { value: m.id }, `${m.name}（${m.id}）`)));
  const ver = h("input", { name: "version", placeholder: "版本，例如 1.0.4", required: true, pattern: "[0-9][0-9A-Za-z.+-]*" });
  const notes = h("input", { name: "notes", placeholder: "版本說明（選填）", style: "flex:1;min-width:160px" });
  const file = h("input", { type: "file", accept: ".zip", required: true });
  const err = h("p", { class: "err" });
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const f = file.files[0]; if (!f) return;
    const btn = form.querySelector("button"); btn.disabled = true; btn.textContent = "上傳中…";
    try {
      await api(`/modules/${sel.value}/releases/${encodeURIComponent(ver.value.trim())}?notes=${encodeURIComponent(notes.value)}`, { method: "PUT", raw: f, type: "application/zip" });
      toast("已發佈，桌面大程式會提示更新"); adminReleases(body);
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; btn.textContent = "發佈"; }
  } }, h("div", { class: "row" }, sel, ver, notes), h("div", { class: "row", style: "margin-top:10px" }, file, h("button", { class: "btn primary" }, "發佈")), err,
  h("p", { class: "muted", style: "font-size:13px" }, "zip 的模塊根目錄要有 module.json，id 與版本必須和這裡一致。已發佈的版本不能覆寫。NAS 上的服務（大程式網站、論文庫、Hub）用監控程式更新。"));
  const lists = await Promise.all(mods.map((m) => api(`/modules/${m.id}/releases`).then((r) => [m, r.releases])));
  body.replaceChildren(h("div", { class: "card" }, h("h3", null, "發佈新版本"), form),
    ...lists.map(([m, rels]) => h("div", null, h("h2", null, m.name),
      rels.length ? h("div", { class: "tablewrap" }, h("table", null, h("tbody", null, rels.map((r) => h("tr", { class: r.withdrawn ? "off" : null },
        h("td", null, h("b", null, `v${r.version}`), r.withdrawn ? h("span", { class: "muted" }, "（已撤回）") : null),
        h("td", { class: "muted" }, r.notes || ""), h("td", { class: "muted hide-sm" }, size(r.size)),
        h("td", { class: "muted" }, `${when(r.uploaded_at)} · ${r.uploaded_by}`),
        h("td", null, r.withdrawn ? null : h("button", { class: "btn small danger", onclick: async () => {
          if (!confirm(`撤回 ${m.id} v${r.version}？（已安裝的人不受影響，只是不會再更新到這版）`)) return;
          await api(`/modules/${m.id}/releases/${encodeURIComponent(r.version)}`, { method: "DELETE" }); adminReleases(body);
        } }, "撤回")))))))
        : h("p", { class: "muted" }, "還沒有發佈"))));
}
async function adminStatus(body) {
  const draw = async () => {
    const s = await api("/health");
    const t = s.traffic || { series: [] };
    const max = Math.max(1, ...t.series.map((x) => x.n));
    const spark = h("div", { style: "display:flex;align-items:flex-end;gap:1px;height:48px;margin:8px 0" }, t.series.map((x) =>
      h("i", { title: `${new Date(x.t * 1000).toLocaleTimeString("zh-TW")}：${x.n} 次${x.err ? `，${x.err} 錯誤` : ""}`,
        style: `flex:1;background:${x.err ? "var(--err)" : "var(--accent)"};opacity:.75;height:${Math.max(2, 48 * x.n / max)}px` })));
    body.replaceChildren(h("div", { class: "grid" },
      h("div", { class: "card" }, statusDot("大程式網站", true, `v${s.portal.version}`), h("dl", { class: "kv", style: "margin-top:8px" },
        h("dt", null, "已執行"), h("dd", null, dur(s.portal.uptime_s)), h("dt", null, "登入中"), h("dd", null, `${s.portal.sessions} 個 session`),
        h("dt", null, "數據 / 標籤"), h("dd", null, `${s.portal.datasets} / ${s.portal.tags}`), h("dt", null, "資料庫"), h("dd", null, `${s.portal.db_mb} MB`),
        h("dt", null, "NAS 空間"), h("dd", null, `剩 ${s.portal.disk_free_gb} / ${s.portal.disk_total_gb} GB`))),
      h("div", { class: "card" }, statusDot("論文庫", s.paperlib.online, s.paperlib.version ? `v${s.paperlib.version} · ${s.paperlib.ms} ms` : s.paperlib.error),
        s.paperlib.status ? h("dl", { class: "kv", style: "margin-top:8px" }, Object.entries(s.paperlib.status).filter(([, v]) => typeof v !== "object").slice(0, 8)
          .flatMap(([k, v]) => [h("dt", null, k), h("dd", null, String(v))])) : null),
      h("div", { class: "card" }, statusDot("量測中繼站", s.labhub.online, s.labhub.version ? `v${s.labhub.version} · ${s.labhub.ms} ms` : s.labhub.error),
        s.labhub.nodes ? h("p", { class: "muted" }, `節點 ${s.labhub.nodes.nodes_online}/${s.labhub.nodes.nodes} 上線 · 電腦 ${s.labhub.nodes.devices_online}/${s.labhub.nodes.devices}`) : null),
      s.agent ? h("div", { class: "card" }, statusDot("更新代理", s.agent.online, s.agent.version ? `v${s.agent.version}` : s.agent.error),
        h("p", { class: "muted" }, "更新、重建、重新啟動請用監控程式。")) : null),
    h("h2", null, "最近 60 分鐘"), h("div", { class: "card" }, h("div", { class: "row muted" }, `${t.requests} 次請求 · 平均 ${t.avg_ms ?? "—"} ms · ${t.errors} 個錯誤 · 線上：${(t.online || []).join("、") || "—"}`), spark),
    s.recent_errors?.length ? [h("h2", null, "最近的錯誤"), h("div", { class: "tablewrap" }, h("table", null, h("tbody", null,
      s.recent_errors.slice().reverse().map((e) => h("tr", null, h("td", null, when(e.time)), h("td", null, e.status), h("td", null, e.path))))))] : null);
  };
  await draw();
  S.timers.push(setInterval(() => draw().catch(() => {}), 15000));
}
async function adminSettings(body) {
  const s = await api("/admin/settings");
  const url = h("input", { value: s.paperlib_public_url || "", placeholder: s.effective_paperlib_url, style: "flex:1;min-width:220px" });
  const sessions = await api("/admin/sessions");
  body.replaceChildren(h("div", { class: "card" }, h("h3", null, "論文庫網址（論文連結用）"),
    h("p", { class: "desc" }, `空白時依序用：環境變數 PAPERLIB_PUBLIC_URL → 論文庫「網站網址」設定 → 同主機的 8080 埠。目前：${s.effective_paperlib_url}`),
    h("div", { class: "row" }, url, h("button", { class: "btn primary", onclick: async () => {
      try { await api("/admin/settings", { method: "PUT", body: { paperlib_public_url: url.value } }); toast("已儲存"); } catch (e) { toast(e.message); }
    } }, "儲存"))),
  h("h2", null, "連線設定（docker-compose.yml）"), h("div", { class: "card" }, h("dl", { class: "kv" }, Object.entries(s.env).flatMap(([k, v]) =>
    [h("dt", null, k), h("dd", null, typeof v === "object" ? JSON.stringify(v) : String(v))]))),
  h("h2", null, "登入中的裝置"), h("div", { class: "tablewrap" }, h("table", null,
    h("thead", null, h("tr", null, h("th", null, "使用者"), h("th", null, "程式"), h("th", { class: "hide-sm" }, "IP"), h("th", null, "最後使用"), h("th"))),
    h("tbody", null, sessions.items.map((x) => h("tr", null, h("td", null, x.username), h("td", null, x.client), h("td", { class: "hide-sm" }, x.ip),
      h("td", null, when(x.last_seen)), h("td", null, h("button", { class: "btn small", onclick: async () => { await api(`/admin/sessions/${x.id}`, { method: "DELETE" }); adminSettings(body); } }, "登出"))))))));
}
async function adminAudit(body) {
  const r = await api("/admin/audit?limit=300");
  body.replaceChildren(h("div", { class: "tablewrap" }, h("table", null,
    h("thead", null, h("tr", null, h("th", null, "時間"), h("th", null, "使用者"), h("th", null, "動作"), h("th", null, "內容"), h("th", { class: "hide-sm" }, "IP"))),
    h("tbody", null, r.items.map((x) => h("tr", null, h("td", { style: "white-space:nowrap" }, when(x.time)), h("td", null, x.username), h("td", null, x.action),
      h("td", null, x.detail), h("td", { class: "hide-sm muted" }, x.ip)))))));
}

// ---------------------------------------------------------------- 啟動
async function boot() {
  try { S.site = await api("/site", { quiet: true }); } catch (e) { $("#view").replaceChildren(h("div", { class: "card err" }, `連不到大程式：${e.message}`)); return; }
  S.user = S.site.user;
  document.title = S.site.name;
  $("#site-name").textContent = S.site.name;
  window.addEventListener("hashchange", route);
  route();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  // 站長改了權限、別人加了標籤 → 自動更新
  let after = -1;
  (async function listen() {
    for (;;) {
      if (!S.user) { await new Promise((r) => setTimeout(r, 3000)); continue; }
      try {
        const r = await api(`/events?after=${after}&wait=25&topics=tags,access,modules`, { quiet: true });
        after = r.last;
        for (const ev of r.events) {
          if (ev.topic.startsWith("tags")) S.tags = null;
          if (ev.topic === "access.changed") { const s = await api("/site", { quiet: true }); S.user = s.user; nav(); toast("站長更新了你的權限"); }
        }
      } catch { await new Promise((r) => setTimeout(r, 5000)); }
    }
  })();
}
boot();
