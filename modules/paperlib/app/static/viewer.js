// PDF 閱讀器：pdf.js 繪製頁面 + 文字層（可選取）+ 劃線層。只繪製看得到的頁，手機也順。
import * as pdfjs from './vendor/pdfjs/pdf.min.mjs';

const V = '/static/vendor/pdfjs/';
pdfjs.GlobalWorkerOptions.workerSrc = V + 'pdf.worker.min.mjs';

export const COLORS = {
  yellow: { name: '重點', css: 'rgba(250, 204, 21, .42)' },
  blue: { name: '方法／公式', css: 'rgba(59, 130, 246, .30)' },
  green: { name: '參數／數據', css: 'rgba(34, 197, 94, .32)' },
  red: { name: '疑問', css: 'rgba(239, 68, 68, .30)' },
};

// ---------------------------------------------------------------- 選字不亂跳
// pdf.js 的文字層在字與字的空隙是空白；拖曳經過空白時，瀏覽器會把選取範圍跳到頁首或頁尾。
// 做法同 pdf.js 官方閱讀器：每層放一個 endOfContent，選取時把它移到選取範圍的端點旁邊。
const TEXT_LAYERS = new Map();   // textLayer div -> endOfContent div
let selWired = false;
function resetEnd(end, tl) { tl.append(end); end.style.width = ''; end.style.height = ''; tl.classList.remove('selecting'); }
function wireSelection() {
  if (selWired) return;
  selWired = true;
  let prevRange = null;
  const resetAll = () => { TEXT_LAYERS.forEach(resetEnd); prevRange = null; };
  document.addEventListener('pointerup', resetAll);
  window.addEventListener('blur', resetAll);
  document.addEventListener('keyup', resetAll);
  document.addEventListener('selectionchange', () => {
    for (const tl of [...TEXT_LAYERS.keys()]) if (!tl.isConnected) TEXT_LAYERS.delete(tl);
    const sel = document.getSelection();
    if (!sel || sel.rangeCount === 0) { resetAll(); return; }
    const active = new Set();
    for (let i = 0; i < sel.rangeCount; i++) {
      const r = sel.getRangeAt(i);
      for (const tl of TEXT_LAYERS.keys()) if (!active.has(tl) && r.intersectsNode(tl)) active.add(tl);
    }
    for (const [tl, end] of TEXT_LAYERS) { if (active.has(tl)) tl.classList.add('selecting'); else resetEnd(end, tl); }
    const range = sel.getRangeAt(0);
    const modifyStart = prevRange && (range.compareBoundaryPoints(Range.END_TO_END, prevRange) === 0 || range.compareBoundaryPoints(Range.START_TO_END, prevRange) === 0);
    let anchor = modifyStart ? range.startContainer : range.endContainer;
    if (anchor.nodeType === Node.TEXT_NODE) anchor = anchor.parentNode;
    const tl = anchor?.parentElement?.closest('.textLayer');
    const end = tl && TEXT_LAYERS.get(tl);
    if (end && anchor.parentElement) {
      end.style.width = tl.style.width; end.style.height = tl.style.height;
      anchor.parentElement.insertBefore(end, modifyStart ? anchor : anchor.nextSibling);
    }
    prevRange = range.cloneRange();
  });
}

// ---------------------------------------------------------------- 手寫
const SVGNS = 'http://www.w3.org/2000/svg';
function strokePath(pts, ar) {
  // 用中點做二次曲線，筆跡比較平滑；座標：x 為頁寬比例，y 乘上長寬比
  const P = pts.map(([x, y]) => [x, y * ar]);
  if (P.length === 1) return `M${P[0][0]} ${P[0][1]}l0.0001 0`;
  let d = `M${P[0][0]} ${P[0][1]}`;
  for (let i = 1; i < P.length - 1; i++) {
    const mx = (P[i][0] + P[i + 1][0]) / 2, my = (P[i][1] + P[i + 1][1]) / 2;
    d += `Q${P[i][0]} ${P[i][1]} ${mx.toFixed(5)} ${my.toFixed(5)}`;
  }
  const l = P[P.length - 1];
  return `${d}L${l[0]} ${l[1]}`;
}
function inkSvg(ar, cls = 'pv-ink') {
  const svg = document.createElementNS(SVGNS, 'svg');
  svg.setAttribute('class', cls);
  svg.setAttribute('viewBox', `0 0 1 ${ar}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  return svg;
}
function addStroke(svg, st, ar) {
  const path = document.createElementNS(SVGNS, 'path');
  path.setAttribute('d', strokePath(st.p, ar));
  path.setAttribute('stroke', st.c); path.setAttribute('stroke-width', st.w);
  path.setAttribute('stroke-opacity', st.o ?? 1);
  path.setAttribute('fill', 'none'); path.setAttribute('stroke-linecap', 'round'); path.setAttribute('stroke-linejoin', 'round');
  svg.append(path);
  return path;
}
function segDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const t = dx || dy ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}
export function inkPreview(ink, w = 120) {
  // 標註清單裡的小預覽
  const strokes = ink?.strokes || [];
  const xs = strokes.flatMap((s) => s.p.map((q) => q[0])), ys = strokes.flatMap((s) => s.p.map((q) => q[1]));
  const svg = document.createElementNS(SVGNS, 'svg');
  if (!xs.length) return svg;
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 0.01, vw = Math.max(0.02, x1 - x0) + 2 * pad, vh = Math.max(0.02, (y1 - y0) * 1.3) + 2 * pad;
  svg.setAttribute('viewBox', `${x0 - pad} ${y0 * 1.3 - pad} ${vw} ${vh}`);
  svg.setAttribute('class', 'ink-preview');
  svg.setAttribute('width', w); svg.setAttribute('height', Math.min(90, (w * vh) / vw));
  strokes.forEach((st) => addStroke(svg, st, 1.3));
  return svg;
}

// 看論文時鎖住整個網頁的縮放（手機兩指只縮放 PDF）；離開論文就恢復
let zoomLocks = 0;
function lockPageZoom(d) {
  zoomLocks = Math.max(0, zoomLocks + d);
  const m = document.querySelector('meta[name=viewport]');
  if (!m) return;
  const base = 'width=device-width, initial-scale=1, viewport-fit=cover';
  const want = zoomLocks ? `${base}, maximum-scale=1, user-scalable=no` : base;
  if (m.getAttribute('content') !== want) m.setAttribute('content', want);
}

export class Viewer {
  constructor(root, { onPage, onSelect, onAnnClick, onTranslate, canAnnotate = true } = {}) {
    this.root = root;
    this.onTranslate = onTranslate || null;
    this.canAnnotate = canAnnotate;
    this.onPage = onPage || (() => {});
    this.onSelect = onSelect || (() => {});
    this.onAnnClick = onAnnClick || (() => {});
    this.scroller = document.createElement('div');
    this.scroller.className = 'pv-scroll';
    this.pagesEl = document.createElement('div');
    this.pagesEl.className = 'pv-pages';
    this.scroller.append(this.pagesEl);
    this.pop = document.createElement('div');
    this.pop.className = 'pv-pop';
    this.pop.hidden = true;
    root.append(this.scroller, this.pop);
    this.pages = [];
    this.anns = [];
    this.scale = 1;
    this.fit = true;
    this.current = 1;
    this.io = new IntersectionObserver((es) => es.forEach((e) => {
      const p = this.pages[+e.target.dataset.n - 1];
      p.visible = e.isIntersecting;
      if (e.isIntersecting) this.#render(p);
    }), { root: this.scroller, rootMargin: '600px 0px' });
    this.scroller.addEventListener('scroll', () => this.#onScroll(), { passive: true });
    this._sel = () => this.#onSelection();
    document.addEventListener('selectionchange', this._sel);
    this.scroller.addEventListener('pointerup', () => setTimeout(() => this.#onSelection(true), 10));
    this.scroller.addEventListener('click', (e) => this.#onClick(e));
    this.ro = new ResizeObserver(() => { if (this.fit && this.doc) this.#fitWidth(); });
    this.ro.observe(this.scroller);
    this.ink = null;
    this.scroller.addEventListener('pointerdown', (e) => this.#inkDown(e));
    // iPad：「只用觸控筆」時，Apple Pencil 不捲動、手指照常捲動
    this.scroller.addEventListener('touchstart', (e) => {
      if (this.ink && (!this.ink.penOnly || [...e.touches].some((t) => t.touchType === 'stylus'))) e.preventDefault();
      if (e.touches.length === 2) this.#pinchStart(e);
    }, { passive: false });
    // 縮放論文而不是整個網頁：手機兩指、電腦 Ctrl＋滾輪（觸控板捏合也是）
    this.scroller.addEventListener('touchmove', (e) => this.#pinchMove(e), { passive: false });
    this.scroller.addEventListener('touchend', (e) => this.#pinchEnd(e));
    this.scroller.addEventListener('touchcancel', (e) => this.#pinchEnd(e));
    this._wheel = (e) => this.#onWheel(e);
    window.addEventListener('wheel', this._wheel, { passive: false });
    // Safari（iPhone／iPad／Mac 觸控板）的捏合手勢
    const gs = (e) => { e.preventDefault(); if (e.type === 'gesturestart' && !this._pinch && !('ontouchstart' in window)) this.#zoomStart(e.clientX, e.clientY); };
    this.scroller.addEventListener('gesturestart', gs, { passive: false });
    this.scroller.addEventListener('gesturechange', (e) => { e.preventDefault(); if (this._z && !this._pinch) this.#zoomMove(e.scale); }, { passive: false });
    this.scroller.addEventListener('gestureend', (e) => { e.preventDefault(); if (!this._pinch) this.#zoomEnd(); }, { passive: false });
    lockPageZoom(1);
  }

  // ---------------------------------------------------------------- 縮放手勢
  #pageAt(x, y) {
    for (const p of this.pages) { const r = p.el.getBoundingClientRect(); if (y >= r.top - 6 && y <= r.bottom + 6) return p; }
    return this.pages[(this.current || 1) - 1] || this.pages[0];
  }
  #zoomStart(x, y) {
    if (!this.doc || !this.pages.length) return false;
    const pg = this.#pageAt(x, y);
    const r = pg.el.getBoundingClientRect(), pr = this.pagesEl.getBoundingClientRect();
    this._z = { s0: this.scale, f: 1, tx: 0, ty: 0, pg, fx: (x - r.left) / r.width, fy: (y - r.top) / r.height, cx: x, cy: y };
    this.pagesEl.style.transformOrigin = `${x - pr.left}px ${y - pr.top}px`;
    this.pagesEl.style.willChange = 'transform';
    return true;
  }
  #zoomMove(f, tx = 0, ty = 0) {
    const z = this._z; if (!z) return;
    z.f = Math.max(0.3 / z.s0, Math.min(5 / z.s0, f)); z.tx = tx; z.ty = ty;
    this.pagesEl.style.transform = `translate(${tx}px, ${ty}px) scale(${z.f})`;
  }
  #zoomEnd() {
    const z = this._z; if (!z) return;
    this._z = null;
    clearTimeout(this._zt);
    this.pagesEl.style.transform = ''; this.pagesEl.style.transformOrigin = ''; this.pagesEl.style.willChange = '';
    if (Math.abs(z.f - 1) > 0.01) this.#setScale(z.s0 * z.f, false);
    const r = z.pg.el.getBoundingClientRect();
    this.scroller.scrollLeft += r.left + z.fx * r.width - (z.cx + z.tx);
    this.scroller.scrollTop += r.top + z.fy * r.height - (z.cy + z.ty);
  }
  #onWheel(e) {
    if (!e.ctrlKey || !this.doc || !this.root.isConnected) return;
    // 滑鼠在 PDF 上，或在這篇論文的其他地方（右側欄）時，都縮放這篇 PDF
    const inPdf = this.scroller.contains(e.target);
    const host = this.root.closest('.pane, .paper');
    if (!inPdf && !(host && host.contains(e.target))) return;
    e.preventDefault();
    let x = e.clientX, y = e.clientY;
    if (!inPdf) { const r = this.scroller.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top + r.height / 3; }
    if (!this._z && !this.#zoomStart(x, y)) return;
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
    this.#zoomMove(this._z.f * Math.exp(-Math.max(-60, Math.min(60, dy)) * 0.0025));
    clearTimeout(this._zt);
    this._zt = setTimeout(() => this.#zoomEnd(), 180);
  }
  #pinchStart(e) {
    if (this._crop || (this.ink && !this.ink.penOnly) || !this.doc) return;
    const [a, b] = e.touches;
    if (e.cancelable) e.preventDefault();
    const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
    this._pinch = { d0: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) || 1, mx, my };
    window.getSelection()?.removeAllRanges();
    this.pop.hidden = true;
    if (this._z) this.#zoomEnd();
    this.#zoomStart(mx, my);
  }
  #pinchMove(e) {
    const P = this._pinch;
    if (!P || e.touches.length < 2) return;
    if (e.cancelable) e.preventDefault();
    const [a, b] = e.touches;
    const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
    this.#zoomMove(d / P.d0, e.cancelable ? mx - P.mx : 0, e.cancelable ? my - P.my : 0);
  }
  #pinchEnd(e) {
    if (!this._pinch || e.touches.length >= 2) return;
    this._pinch = null;
    this.#zoomEnd();
  }

  // ---------------------------------------------------------------- 手寫
  // opts: { tool: 'pen'|'hl'|'eraser', color, width, penOnly, onStroke(page, stroke) → Promise, onErase([anns]), canErase(a) }
  startInk(opts) {
    this.stopCrop();
    this.pop.hidden = true;
    window.getSelection()?.removeAllRanges();
    this.ink = { ...(this.ink || {}), ...opts };
    this.scroller.classList.add('inking');
    this.scroller.classList.toggle('pen-only', !!this.ink.penOnly);
    this.scroller.classList.toggle('erasing', this.ink.tool === 'eraser');
  }
  stopInk() { this.ink = null; this.scroller.classList.remove('inking', 'pen-only', 'erasing'); }
  get inking() { return !!this.ink; }

  #inkDown(e) {
    const ink = this.ink;
    if (!ink || (e.button > 0 && e.pointerType === 'mouse')) return;
    if (ink.penOnly && e.pointerType === 'touch') return;          // 手指：捲動
    const p = this.#pageOf(e.target);
    if (!p) return;
    e.preventDefault();
    const box = p.el.getBoundingClientRect();
    const ar = p.h / p.w;
    const norm = (ev) => [Math.max(0, Math.min(1, (ev.clientX - box.left) / box.width)), Math.max(0, Math.min(1, (ev.clientY - box.top) / box.height))];
    const id = e.pointerId;
    try { this.scroller.setPointerCapture(id); } catch { /* */ }
    if (ink.tool === 'eraser') {
      const changed = new Set();
      const erase = (ev) => {
        const [x, y] = norm(ev);
        for (const a of this.anns) {
          if (a.page !== p.n || !a.ink?.strokes?.length || (ink.canErase && !ink.canErase(a))) continue;
          const before = a.ink.strokes.length;
          a.ink.strokes = a.ink.strokes.filter((st) => {
            const r = Math.max(0.008, st.w * 1.5);
            const P = st.p;
            if (P.length === 1) return Math.hypot(x - P[0][0], (y - P[0][1]) * ar) > r;
            for (let i = 1; i < P.length; i++) if (segDist(x, y * ar, P[i - 1][0], P[i - 1][1] * ar, P[i][0], P[i][1] * ar) < r) return false;
            return true;
          });
          if (a.ink.strokes.length !== before) changed.add(a);
        }
        if (changed.size) this.#drawAnns();
      };
      erase(e);
      const move = (ev) => { if (ev.pointerId === id) erase(ev); };
      const up = (ev) => {
        if (ev.pointerId !== id) return;
        this.scroller.removeEventListener('pointermove', move); this.scroller.removeEventListener('pointerup', up); this.scroller.removeEventListener('pointercancel', up);
        if (changed.size && ink.onErase) ink.onErase([...changed]);
      };
      this.scroller.addEventListener('pointermove', move); this.scroller.addEventListener('pointerup', up); this.scroller.addEventListener('pointercancel', up);
      return;
    }
    const hl = ink.tool === 'hl';
    const stroke = { c: ink.color || '#111111', w: hl ? Math.max(0.012, (ink.width || 0.003) * 4) : (ink.width || 0.003), o: hl ? 0.35 : 1, p: [norm(e)] };
    const live = inkSvg(ar, 'pv-ink live');
    p.el.append(live);
    const path = addStroke(live, stroke, ar);
    const move = (ev) => {
      if (ev.pointerId !== id) return;
      const evs = ev.getCoalescedEvents ? ev.getCoalescedEvents() : [ev];
      for (const c of (evs.length ? evs : [ev])) {
        const q = norm(c), l = stroke.p[stroke.p.length - 1];
        if (Math.hypot(q[0] - l[0], (q[1] - l[1]) * ar) > 0.0012) stroke.p.push([+q[0].toFixed(4), +q[1].toFixed(4)]);
      }
      path.setAttribute('d', strokePath(stroke.p, ar));
    };
    const up = async (ev) => {
      if (ev.pointerId !== id) return;
      this.scroller.removeEventListener('pointermove', move); this.scroller.removeEventListener('pointerup', up); this.scroller.removeEventListener('pointercancel', up);
      try { await (ink.onStroke && ink.onStroke(p.n, stroke)); } finally { live.remove(); }
    };
    this.scroller.addEventListener('pointermove', move); this.scroller.addEventListener('pointerup', up); this.scroller.addEventListener('pointercancel', up);
  }

  async open(url) {
    this.#clear();
    const task = pdfjs.getDocument({
      url, withCredentials: true, cMapUrl: V + 'cmaps/', cMapPacked: true,
      standardFontDataUrl: V + 'standard_fonts/', wasmUrl: V + 'wasm/', iccUrl: V + 'iccs/',
    });
    this.task = task;
    this.doc = await task.promise;
    const first = await this.doc.getPage(1);
    this.base = first.getViewport({ scale: 1 });
    for (let n = 1; n <= this.doc.numPages; n++) {
      const el = document.createElement('div');
      el.className = 'pv-page';
      el.dataset.n = n;
      const hl = document.createElement('div');
      hl.className = 'pv-hl';
      el.append(hl);
      this.pagesEl.append(el);
      this.pages.push({ n, el, hl, w: this.base.width, h: this.base.height, rendered: 0, visible: false });
    }
    this.#fitWidth();
    this.pages.forEach((p) => this.io.observe(p.el));
    this.#drawAnns();
    return this.doc.numPages;
  }

  // 筆記頁：沒有 PDF 的空白頁（A4 直式），背景可以是空白、橫線、方格、點陣
  openBlank({ pages = 1, bg = 'lined', w = 595, h = 842 } = {}) {
    this.#clear();
    this.blank = true;
    this.doc = { numPages: 0, blank: true };
    this.base = { width: w, height: h };
    this.setBg(bg);
    for (let i = 0; i < pages; i++) this.#blankPage();
    this.#fitWidth();
    this.#drawAnns();
    return this.doc.numPages;
  }
  #blankPage() {
    const n = this.pages.length + 1;
    const el = document.createElement('div');
    el.className = 'pv-page nb-page';
    el.dataset.n = n;
    const hl = document.createElement('div');
    hl.className = 'pv-hl';
    const num = document.createElement('span');
    num.className = 'nb-num';
    num.textContent = n;
    el.append(hl, num);
    const tail = this.pagesEl.querySelector(':scope > .nb-tail');
    this.pagesEl.insertBefore(el, tail);
    const p = { n, el, hl, w: this.base.width, h: this.base.height, rendered: 0, visible: false };
    this.pages.push(p);
    this.doc.numPages = n;
    if (this.scale) {
      el.style.width = `${Math.floor(p.w * this.scale)}px`;
      el.style.height = `${Math.floor(p.h * this.scale)}px`;
      el.style.setProperty('--scale-factor', this.scale);
    }
    this.io.observe(el);
    return p;
  }
  addPage() { if (!this.blank) return 0; this.#blankPage(); this.#drawAnns(); return this.doc.numPages; }
  setBg(bg) { this.pagesEl.dataset.bg = bg; }
  // 在頁面最後面放東西（例如「新增一頁」按鈕）
  setTail(el) { this.pagesEl.querySelector(':scope > .nb-tail')?.remove(); if (el) { el.classList.add('nb-tail'); this.pagesEl.append(el); } }

  get numPages() { return this.doc ? this.doc.numPages : 0; }

  #clear() {
    this.blank = false;
    this.io.disconnect();
    this.pages.forEach((p) => p.task && p.task.cancel());
    this.pagesEl.innerHTML = '';
    this.pages = [];
    this.doc = null;
    if (this.task) { try { this.task.destroy(); } catch (e) { /* 已關閉 */ } this.task = null; }
  }

  destroy() {
    window.removeEventListener('wheel', this._wheel);
    clearTimeout(this._zt);
    lockPageZoom(-1);
    this.stopCrop();
    this.stopInk();
    document.removeEventListener('selectionchange', this._sel);
    this.ro.disconnect();
    this.#clear();
    this.root.innerHTML = '';
  }

  #fitWidth() {
    const avail = this.scroller.clientWidth - (this.scroller.clientWidth < 600 ? 12 : 40);
    if (avail <= 0) return;
    this.#setScale(Math.max(0.3, Math.min(avail / this.base.width, 3)), true);
  }

  zoom(f) { this.#setScale(Math.max(0.3, Math.min(this.scale * f, 5)), false); }
  fitWidth() { this.fit = true; this.#fitWidth(); }
  // 版面變動後重新套用「符合寬度」；使用者自己縮放過的就保留他的比例
  refit() { if (this.fit && this.doc) this.#fitWidth(); }

  #setScale(s, fit) {
    if (Math.abs(s - this.scale) < 0.005 && this.pages[0]?.el.style.width) return;
    const keep = this.current;
    const off = this.#pageOffset(keep);
    this.scale = s;
    this.fit = fit;
    for (const p of this.pages) {
      p.el.style.width = `${Math.floor(p.w * s)}px`;
      p.el.style.height = `${Math.floor(p.h * s)}px`;
      p.el.style.setProperty('--total-scale-factor', s);
      p.el.style.setProperty('--scale-factor', s);
      if (p.rendered) { p.rendered = 0; this.#unrender(p); }
    }
    this.goTo(keep, off);
    this.pages.filter((p) => p.visible).forEach((p) => this.#render(p));
  }

  #pageOffset(n) {
    const p = this.pages[n - 1];
    if (!p) return 0;
    return (this.scroller.scrollTop - p.el.offsetTop) / p.el.offsetHeight;
  }

  goTo(n, frac = 0, smooth = false) {
    const p = this.pages[Math.max(1, Math.min(n, this.pages.length)) - 1];
    if (!p) return;
    const top = p.el.offsetTop + Math.max(0, frac) * p.el.offsetHeight - 8;
    this.scroller.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' });
  }

  #unrender(p) {
    if (p.task) { p.task.cancel(); p.task = null; }
    p.el.querySelectorAll('.textLayer').forEach((x) => TEXT_LAYERS.delete(x));
    p.el.querySelectorAll('canvas, .textLayer').forEach((x) => x.remove());
  }

  async #render(p) {
    if (p.rendered === this.scale || !this.doc) return;
    if (this.blank) { p.rendered = this.scale; return; }
    const scale = this.scale;
    p.rendered = scale;
    const page = await this.doc.getPage(p.n);
    const vp = page.getViewport({ scale });
    if (p.w !== vp.width / scale || p.h !== vp.height / scale) {  // 頁面大小不一
      p.w = vp.width / scale; p.h = vp.height / scale;
      p.el.style.width = `${Math.floor(vp.width)}px`;
      p.el.style.height = `${Math.floor(vp.height)}px`;
    }
    // 放很大時限制畫布大小（iPhone 單一畫布上限約 16M 像素，超過會變空白）
    const os = Math.max(0.5, Math.min(window.devicePixelRatio || 1, window.innerWidth < 700 ? 2 : 2.5, Math.sqrt(12e6 / (vp.width * vp.height))));
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(vp.width * os);
    canvas.height = Math.floor(vp.height * os);
    canvas.style.width = `${Math.floor(vp.width)}px`;
    canvas.style.height = `${Math.floor(vp.height)}px`;
    this.#unrender(p);
    p.el.prepend(canvas);
    p.task = page.render({ canvasContext: canvas.getContext('2d'), viewport: vp, transform: os !== 1 ? [os, 0, 0, os, 0, 0] : null });
    try {
      await p.task.promise;
    } catch (e) {
      if (e && e.name === 'RenderingCancelledException') return;
      console.warn(e);
    }
    if (p.rendered !== scale) return;
    const tl = document.createElement('div');
    tl.className = 'textLayer';
    p.el.append(tl);
    try {
      const layer = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: tl, viewport: vp });
      tl.style.width = `${Math.floor(vp.width)}px`;
      tl.style.height = `${Math.floor(vp.height)}px`;
      await layer.render();
      const end = document.createElement('div');
      end.className = 'endOfContent';
      tl.append(end);
      tl.addEventListener('mousedown', () => tl.classList.add('selecting'));
      TEXT_LAYERS.set(tl, end);
      wireSelection();
    } catch (e) { console.warn(e); }
    this.#pruneFar();
  }

  #pruneFar() {
    for (const p of this.pages) {
      if (p.rendered && Math.abs(p.n - this.current) > 6 && !p.visible) { p.rendered = 0; this.#unrender(p); }
    }
  }

  #onScroll() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = null;
      const mid = this.scroller.scrollTop + this.scroller.clientHeight * 0.35;
      let cur = 1;
      for (const p of this.pages) { if (p.el.offsetTop <= mid) cur = p.n; else break; }
      if (cur !== this.current) { this.current = cur; this.onPage(cur); }
      this.pop.hidden = true;
    });
  }

  // ---------------------------------------------------------------- 劃線
  setAnnotations(list) { this.anns = list || []; this.#drawAnns(); }

  #drawAnns() {
    for (const p of this.pages) {
      p.hl.innerHTML = '';
      const here = this.anns.filter((a) => a.page === p.n);
      const inks = here.filter((a) => a.ink?.strokes?.length);
      if (inks.length) {
        const ar = p.h / p.w;
        const svg = inkSvg(ar);
        inks.forEach((a) => a.ink.strokes.forEach((st) => { const path = addStroke(svg, st, ar); if (a._focus) path.classList.add('focus'); }));
        p.hl.append(svg);
      }
      for (const a of here) {
        for (const r of a.rects || []) {
          const d = document.createElement('div');
          d.className = 'pv-mark';
          d.style.cssText = `left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%;background:${(COLORS[a.color] || COLORS.yellow).css}`;
          if (a._focus) d.classList.add('focus');
          p.hl.append(d);
        }
      }
      const notes = here.filter((a) => !(a.rects || []).length && a.kind !== 'ink').length;
      if (notes) {
        const b = document.createElement('button');
        b.className = 'pv-notebadge';
        b.textContent = `筆記 ${notes}`;
        b.onclick = (e) => { e.stopPropagation(); this.onAnnClick(here.find((a) => !(a.rects || []).length && a.kind !== 'ink').id); };
        p.hl.append(b);
      }
    }
  }

  focusAnn(id) {
    this.anns.forEach((a) => { a._focus = a.id === id; });
    this.#drawAnns();
    const a = this.anns.find((x) => x.id === id);
    const y0 = a?.rects?.[0] ? a.rects[0][1] : a?.ink?.strokes?.[0] ? Math.min(...a.ink.strokes.flatMap((st) => st.p.map((q) => q[1]))) : 0;
    if (a && a.page) this.goTo(a.page, Math.max(0, y0 - 0.1), true);
  }

  #pageOf(node) {
    const el = (node.nodeType === 1 ? node : node.parentElement)?.closest('.pv-page');
    return el && this.pagesEl.contains(el) ? this.pages[+el.dataset.n - 1] : null;
  }

  #onSelection(force) {
    clearTimeout(this._selT);
    if (this._crop || this.ink) return;
    this._selT = setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) { if (!force) this.pop.hidden = true; return; }
      const range = sel.getRangeAt(0);
      const p = this.#pageOf(range.startContainer);
      if (!p) return;
      const box = p.el.getBoundingClientRect();
      let rects = [...range.getClientRects()]
        .filter((r) => r.width > 1 && r.height > 1 && r.bottom > box.top && r.top < box.bottom)
        .map((r) => [(r.left - box.left) / box.width, (r.top - box.top) / box.height, r.width / box.width, r.height / box.height]);
      rects = mergeRects(rects);
      const raw = sel.toString();
      const quote = raw.replace(/\s+/g, ' ').trim();
      if (!rects.length || !quote) return;
      if (!this.canAnnotate && !this.onTranslate) return;
      this.pending = { page: p.n, rects, quote, raw };
      const last = rects[rects.length - 1];
      const sBox = this.scroller.getBoundingClientRect();
      const x = box.left - sBox.left + (last[0] + last[2]) * box.width;
      const y = box.top - sBox.top + (last[1] + last[3]) * box.height + 8;
      this.#showPop(Math.min(Math.max(8, x - 140), sBox.width - 310), Math.min(y, sBox.height - 56));
    }, force ? 0 : 280);
  }

  #showPop(x, y) {
    this.pop.innerHTML = '';
    if (this.onTranslate) {
      const t = document.createElement('button');
      t.className = 'pv-poptr';
      t.textContent = '翻譯';
      t.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        const data = this.pending; this.pending = null; this.pop.hidden = true;
        window.getSelection()?.removeAllRanges();
        if (data) this.onTranslate(data);
      });
      this.pop.append(t);
    }
    if (!this.canAnnotate) { this.#place(x, y); return; }
    for (const [k, c] of Object.entries(COLORS)) {
      const b = document.createElement('button');
      b.className = 'pv-dot';
      b.title = `劃線：${c.name}`;
      b.style.background = c.css.replace(/[\d.]+\)$/, '1)');
      b.addEventListener('pointerdown', (e) => { e.preventDefault(); this.#commit(k, false); });
      this.pop.append(b);
    }
    const n = document.createElement('button');
    n.className = 'pv-popnote';
    n.textContent = '劃線＋筆記';
    n.addEventListener('pointerdown', (e) => { e.preventDefault(); this.#commit('yellow', true); });
    this.pop.append(n);
    this.#place(x, y);
  }

  #place(x, y) {
    this.pop.style.left = `${x}px`;
    this.pop.style.top = `${y}px`;
    this.pop.hidden = false;
  }

  #commit(color, withNote) {
    if (!this.pending) return;
    const data = { ...this.pending, color, withNote };
    this.pending = null;
    this.pop.hidden = true;
    window.getSelection()?.removeAllRanges();
    this.onSelect(data);
  }

  // ---------------------------------------------------------------- 框選（圖卡）
  // 拖出一個矩形，放開後呼叫 cb({ page, rect: [x, y, w, h] })（0–1 的相對座標）；Esc 取消
  startCrop(cb, onCancel) {
    this.stopCrop();
    this.stopInk();
    this.pop.hidden = true;
    this.scroller.classList.add('cropping');
    const clamp = (v) => Math.max(0, Math.min(1, v));
    const down = (e) => {
      const p = this.#pageOf(e.target);
      if (!p || e.button > 0) return;
      e.preventDefault();
      const box = p.el.getBoundingClientRect();
      const x0 = clamp((e.clientX - box.left) / box.width), y0 = clamp((e.clientY - box.top) / box.height);
      let rect = [x0, y0, 0, 0];
      const r = document.createElement('div');
      r.className = 'pv-crop';
      p.el.append(r);
      const move = (ev) => {
        const x1 = clamp((ev.clientX - box.left) / box.width), y1 = clamp((ev.clientY - box.top) / box.height);
        rect = [Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0)];
        r.style.cssText = `left:${rect[0] * 100}%;top:${rect[1] * 100}%;width:${rect[2] * 100}%;height:${rect[3] * 100}%`;
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointercancel', up);
        setTimeout(() => r.remove(), 150);
        if (rect[2] > 0.02 && rect[3] > 0.01) { this.stopCrop(); cb({ page: p.n, rect }); }
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up, { once: true });
      window.addEventListener('pointercancel', up, { once: true });
    };
    const key = (e) => { if (e.key === 'Escape') { this.stopCrop(); onCancel && onCancel(); } };
    this._crop = { down, key };
    this.scroller.addEventListener('pointerdown', down);
    document.addEventListener('keydown', key);
  }

  stopCrop() {
    if (!this._crop) return;
    this.scroller.classList.remove('cropping');
    this.scroller.removeEventListener('pointerdown', this._crop.down);
    document.removeEventListener('keydown', this._crop.key);
    this._crop = null;
  }

  get cropping() { return !!this._crop; }

  // 用畫面上已經繪好的頁面做預覽（真正存檔時由伺服器用高解析度重畫）
  cropPreview(page, rect) {
    const c = this.pages[page - 1]?.el.querySelector('canvas');
    if (!c) return null;
    const [x, y, w, h] = rect;
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(w * c.width)); out.height = Math.max(1, Math.round(h * c.height));
    out.getContext('2d').drawImage(c, x * c.width, y * c.height, w * c.width, h * c.height, 0, 0, out.width, out.height);
    return out.toDataURL('image/png');
  }

  // 跳到圖卡的位置並閃一下框
  flashRect(page, rect) {
    const p = this.pages[page - 1];
    if (!p || !rect || rect.length < 4) return;
    this.goTo(page, Math.max(0, rect[1] - 0.08), true);
    const d = document.createElement('div');
    d.className = 'pv-flash';
    d.style.cssText = `left:${rect[0] * 100}%;top:${rect[1] * 100}%;width:${rect[2] * 100}%;height:${rect[3] * 100}%`;
    p.el.append(d);
    setTimeout(() => d.remove(), 2400);
  }

  #onClick(e) {
    if (this._crop || this.ink) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const p = this.#pageOf(e.target);
    if (!p) return;
    const box = p.el.getBoundingClientRect();
    const x = (e.clientX - box.left) / box.width, y = (e.clientY - box.top) / box.height;
    const hit = this.anns.find((a) => a.page === p.n && (a.rects || []).some((r) => x >= r[0] && x <= r[0] + r[2] && y >= r[1] && y <= r[1] + r[3]));
    if (hit) this.onAnnClick(hit.id);
  }
}

function mergeRects(rs) {
  rs.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const out = [];
  for (const r of rs) {
    const l = out[out.length - 1];
    if (l && Math.abs(l[1] - r[1]) < 0.004 && Math.abs(l[3] - r[3]) < 0.006 && r[0] <= l[0] + l[2] + 0.01) {
      const right = Math.max(l[0] + l[2], r[0] + r[2]);
      l[0] = Math.min(l[0], r[0]); l[2] = right - l[0];
    } else if (!(l && r[0] >= l[0] && r[0] + r[2] <= l[0] + l[2] && r[1] >= l[1] && r[1] + r[3] <= l[1] + l[3])) {
      out.push([...r]);
    }
  }
  return out.slice(0, 200);
}
