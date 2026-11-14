"""1.0.42：論文庫匯出給 GPT —— 依 CCU Magnonics 論文庫規範（labhub/gptexport_README.md，1.1）產生
``CCU_Magnonics_Library_Export/``（zip）。論文庫本身不用改：Hub 用匯出者自己的論文庫登入讀 API。

    GET  /gptexport                       頁面（分類編輯＋匯出）
    GET  /api/gptexport/vocab             8 個主要主題、受控次級標籤、論文類型
    GET  /api/gptexport/papers            論文清單（論文庫）＋ Paper ID ＋ 已存的分類 ＋ 自動對照的建議
    PUT  /api/gptexport/class/<論文庫 id>  {topic, tags, type} 存分類（存在 Hub，任何登入的成員都可以改，記下誰改的）
    GET  /api/gptexport/scopes            可選的範圍（論文庫分類、專案）
    POST /api/gptexport/export            {scope: all | cat:<id> | project:<id>, pdf: all | none | grades, grades: [A..D]}
    GET  /api/gptexport/status            匯出進度
    GET  /api/gptexport/exports           做好的匯出檔；GET /api/gptexport/exports/<檔名> 下載（自己做的或站長）

資料（Hub 的 data/gptexport/）：
    ids.json      Paper ID（MAG-xxxx）與證據 ID（EVD-xxxxxx）的對照：論文庫 id / DOI / arXiv → ID；只增不改、刪了也不重用
    classes.json  每篇的主要主題、次級標籤、論文類型（Hub 的分類編輯頁）
    exports/      匯出的 zip（留最近 5 個）

對照（論文庫 → 規範欄位）：
    優先 A-D       論文庫「關聯性」（規範 1.1 採用同一套定義）
    閱讀狀態       一律「未讀」（論文庫的閱讀狀態是每個人各自的）
    主要主題 / 次級標籤 / 論文類型   Hub 的分類；沒存過 → 論文庫標籤名稱剛好是受控字彙的自動帶入（主題：論文庫分類名稱剛好是 8 個值之一）
    研究問題 / 物理系統 / 與本實驗室關聯   論文庫「重點」欄（AI 填的標「AI interpretation」）
    notes.csv      每篇一列：論文庫重點欄
    evidence.csv   公開的劃線標註（原文 quote ＋ 頁碼 = Verified from PDF）；私人標註不匯出
    notes/MAG-xxxx.md   書目、摘要、重點、標註（給 GPT 讀）
只用標準函式庫。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__
from .server import Handler, HubHTTPError, route

README = Path(__file__).resolve().parent / "gptexport_README.md"
SCHEMA = "1.1"
ROOT = "CCU_Magnonics_Library_Export"
KEEP_EXPORTS = 5

TOPICS = [("FND", "Fundamentals"), ("CAV", "Cavity Magnonics"), ("WAV", "Waveguide Magnonics"),
          ("NH", "Non-Hermitian Physics"), ("CPA", "CPA / CPAEP"), ("CPL", "Coupling Mechanisms"),
          ("DRV", "Driven Magnonics"), ("EXP", "Experimental Methods")]
TOPIC_VALUES = [t for _c, t in TOPICS]
TAG_GROUPS = [
    ("物理模式", ["YIG", "Kittel mode", "Walker mode", "Spin wave"]),
    ("腔與波導", ["CMP", "CLS", "GSE", "Waveguide-mediated coupling", "Mirror-mediated coupling"]),
    ("非厄米與散射", ["Non-Hermitian Hamiltonian", "EP", "EL", "CPA", "CPAEP", "Generic EP", "Non-generic EP",
                   "Scattering zero", "Scattering pole", "Reflectionless state"]),
    ("耦合與驅動", ["Coherent coupling", "Dissipative coupling", "PIM", "MFC", "Nonlinear dynamics", "Phase control"]),
    ("理論工具", ["Input-output theory", "Scattering matrix", "Effective Hamiltonian", "Transfer matrix"]),
    ("實驗技術", ["VNA-S21", "VNA-S11", "Time-domain", "SHFQC", "Ringdown", "IQ mixing", "FFT", "Curve fitting"]),
]
TAGS = [t for _g, ts in TAG_GROUPS for t in ts]
TYPES = ["Review", "Theory", "Experiment", "Theory+Experiment", "Methods", "Preprint", "Other"]

PAPERS_COLS = ["Paper ID", "優先 A-D", "閱讀狀態", "主要主題", "次級標籤", "論文標題 Title", "第一作者", "出版年",
               "期刊 / 來源", "DOI", "arXiv / URL", "PDF 檔名", "論文類型", "研究問題", "物理系統",
               "核心模型 / Hamiltonian", "耦合機制", "CPA / EP 條件", "量測方法", "關鍵式 / 圖", "與本實驗室關聯",
               "重要限制 / 待確認", "原文定位", "更新日期"]
NOTES_COLS = ["Paper ID", "研究問題與創新", "系統架構/器件", "重要假設", "Hamiltonian/方程式", "主要推導步驟",
              "定義/單位慣例", "實驗裝置與頻段", "主要結果", "必讀 Figure/Table", "與 CPAEP/CLS/PIM 的關聯",
              "尚未解決問題", "證據來源（頁、式、圖）", "人工核對狀態", "更新日期"]
EVIDENCE_COLS = ["證據 ID", "研究命題 / 比較問題", "Paper ID", "主題", "結論／公式", "原文定義與假設", "參數值",
                 "單位與頻率慣例", "原文頁/式/圖", "來源 DOI / URL", "相反或支持的文獻 ID", "可直接比較？", "目前判斷",
                 "需再驗證的點", "最後更新"]
WARN_COLS = ["Paper ID", "warning_code", "severity", "description", "action_required"]
PROPOSED_COLS = ["Paper ID", "建議標籤", "原因"]
GRADES = ("A", "B", "C", "D")


# ---------------------------------------------------------------- 小工具
def _key(s: str) -> str:
    return re.sub(r"[\s\-_/]+", "", str(s or "")).lower()


_TAG_BY_KEY = {_key(t): t for t in TAGS}
_TOPIC_BY_KEY = {**{_key(t): t for t in TOPIC_VALUES}, **{_key(c): t for c, t in TOPICS}}


def norm_doi(s: Any) -> str:
    s = str(s or "").strip()
    s = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", s, flags=re.I).strip()
    return s.lower()


def norm_arxiv(s: Any) -> str:
    s = str(s or "").strip()
    s = re.sub(r"^(https?://arxiv\.org/(abs|pdf)/|arxiv:\s*)", "", s, flags=re.I).strip()
    s = re.sub(r"\.pdf$", "", s)
    return re.sub(r"v\d+$", "", s)


def short_title(title: str, n: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", re.sub(r"[^\x00-\x7f]", " ", str(title or "").lower()))
    stop = {"a", "an", "the", "of", "in", "on", "and", "for", "with", "to", "by", "via", "at", "from"}
    w = [x for x in words if x not in stop][:n] or words[:n]
    return ("-".join(w) or "paper")[:60].strip("-") or "paper"


def first_author(authors: Any) -> str:
    if isinstance(authors, str):
        try:
            authors = json.loads(authors)
        except ValueError:
            authors = [a for a in re.split(r"[;\n]", authors) if a.strip()]
    if isinstance(authors, list) and authors:
        a = authors[0]
        if isinstance(a, dict):
            return str(a.get("name") or " ".join(x for x in (a.get("given"), a.get("family")) if x) or "").strip()
        return str(a).strip()
    return ""


def _date(s: Any) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(s or ""))
    return m.group(1) if m else ""


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "；".join(_text(x) for x in v if _text(x))
    if isinstance(v, dict):
        return "；".join(f"{k}：{_text(x)}" for k, x in v.items() if _text(x))
    return str(v).strip()


def auto_class(paper: Dict[str, Any], cat_names: Dict[Any, str]) -> Dict[str, Any]:
    """論文庫標籤 / 分類名稱剛好是受控字彙 → 帶入；其他論文庫標籤 → 建議（proposed_tags）。"""
    tags, other = [], []
    for t in paper.get("tags") or []:
        k = _TAG_BY_KEY.get(_key(t))
        if k:
            if k not in tags:
                tags.append(k)
        else:
            other.append(str(t))
    topic = ""
    for c in paper.get("categories") or []:
        tp = _TOPIC_BY_KEY.get(_key(cat_names.get(c) or cat_names.get(str(c)) or ""))
        if tp:
            topic = tp
            break
    return {"topic": topic, "tags": sorted(tags, key=TAGS.index), "type": "", "other_tags": other}


def clean_class(b: Dict[str, Any]) -> Dict[str, Any]:
    topic = str(b.get("topic") or "")
    if topic and topic not in TOPIC_VALUES:
        raise HubHTTPError(400, f"主要主題只能是：{'、'.join(TOPIC_VALUES)}")
    tags = []
    for t in b.get("tags") or []:
        if t not in TAGS:
            raise HubHTTPError(400, f"「{t}」不是受控標籤")
        if t not in tags:
            tags.append(t)
    typ = str(b.get("type") or "")
    if typ and typ not in TYPES:
        raise HubHTTPError(400, f"論文類型只能是：{'、'.join(TYPES)}")
    return {"topic": topic, "tags": sorted(tags, key=TAGS.index), "type": typ}


def _csv(rows: Iterable[Dict[str, Any]], cols: List[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r.get(c) is None else str(r.get(c)) for c in cols])
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- 存檔
class Store:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir) / "gptexport"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "exports").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.ids = self._load("ids.json") or {}
        for k, v in (("next", 1), ("evd_next", 1), ("pl", {}), ("doi", {}), ("arxiv", {}), ("evd", {})):
            self.ids.setdefault(k, v)
        self.classes: Dict[str, Dict[str, Any]] = self._load("classes.json") or {}
        self.job: Dict[str, Any] = {}

    def _load(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            d = json.loads((self.dir / name).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def _save(self, name: str, obj: Dict[str, Any]) -> None:
        p = self.dir / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def peek(self, pl_id: Any, doi: str = "", arxiv: str = "") -> str:
        i = self.ids
        return i["pl"].get(str(pl_id)) or (doi and i["doi"].get(doi)) or (arxiv and i["arxiv"].get(arxiv)) or ""

    def assign(self, pl_id: Any, doi: str = "", arxiv: str = "") -> str:
        """論文庫 id / DOI / arXiv 已經有 ID → 沿用；否則發新的（MAG-0001 起，只增不重用）。"""
        with self.lock:
            i = self.ids
            mag = self.peek(pl_id, doi, arxiv)
            if not mag:
                mag = f"MAG-{int(i['next']):04d}"
                i["next"] = int(i["next"]) + 1
            i["pl"].setdefault(str(pl_id), mag)
            if doi:
                i["doi"].setdefault(doi, mag)
            if arxiv:
                i["arxiv"].setdefault(arxiv, mag)
            return mag

    def evidence_id(self, ann_id: Any) -> str:
        with self.lock:
            i = self.ids
            e = i["evd"].get(str(ann_id))
            if not e:
                e = f"EVD-{int(i['evd_next']):06d}"
                i["evd_next"] = int(i["evd_next"]) + 1
                i["evd"][str(ann_id)] = e
            return e

    def save_ids(self) -> None:
        with self.lock:
            self._save("ids.json", self.ids)

    def set_class(self, pl_id: Any, c: Dict[str, Any], by: str) -> Dict[str, Any]:
        with self.lock:
            c = dict(clean_class(c), updated=time.time(), by=by)
            self.classes[str(pl_id)] = c
            self._save("classes.json", self.classes)
            return c

    def exports(self) -> List[Dict[str, Any]]:
        out = []
        for p in sorted((self.dir / "exports").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            meta = self._load(f"exports/{p.stem}.json") or {}
            out.append(dict(meta, name=p.name, size=p.stat().st_size, time=p.stat().st_mtime))
        return out

    def prune(self) -> None:
        for x in self.exports()[KEEP_EXPORTS:]:
            for suf in (".zip", ".json"):
                try:
                    (self.dir / "exports" / (Path(x["name"]).stem + suf)).unlink()
                except OSError:
                    pass


def store(hub: Any) -> Store:
    s = getattr(hub, "_gptexport", None)
    if s is None:
        s = hub._gptexport = Store(hub.cfg.data_dir)
    return s


# ---------------------------------------------------------------- 讀論文庫
class Source:
    """用匯出者的論文庫登入讀資料。"""

    def __init__(self, platform: Any, sess: Dict[str, Any]) -> None:
        if platform is None or platform.paperlib is None:
            raise HubHTTPError(503, "這個 Hub 沒有接論文庫")
        self.pl = platform.paperlib
        self.platform = platform
        self.sess = sess["pl_session"]

    def get(self, path: str) -> Any:
        return self.pl.get(path, self.sess)

    def site(self) -> Dict[str, Any]:
        try:
            return self.pl.site()
        except Exception:  # noqa: BLE001
            return {}

    def categories(self) -> Dict[Any, str]:
        try:
            rows = self.get("/api/categories") or []
        except Exception:  # noqa: BLE001
            return {}
        rows = rows.get("items", rows) if isinstance(rows, dict) else rows
        out: Dict[Any, str] = {}
        for c in rows or []:
            if isinstance(c, dict) and c.get("id") is not None:
                out[c["id"]] = str(c.get("name") or "")
                out[str(c["id"])] = str(c.get("name") or "")
        return out

    def list_papers(self, cat: Optional[str] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        off = 0
        while True:
            q = {"limit": 500, "offset": off, "sort": "added"}
            if cat:
                q["cat"] = cat
            res = self.get("/api/papers?" + urllib.parse.urlencode(q)) or {}
            items = list(res.get("items") or [])
            out += items
            off += len(items)
            if not items or off >= int(res.get("total") or 0):
                return out

    def project_paper_ids(self, pid: str) -> List[Any]:
        d = self.get(f"/api/projects/{urllib.parse.quote(str(pid))}") or {}
        ids = []
        for x in (d.get("papers") if isinstance(d, dict) else None) or []:
            x = x.get("paper", x) if isinstance(x, dict) else {}
            if x.get("id") is not None:
                ids.append(x["id"])
        return ids

    def detail(self, pid: Any) -> Dict[str, Any]:
        return self.get(f"/api/papers/{pid}") or {}

    def annotations(self, pid: Any) -> List[Dict[str, Any]]:
        try:
            return list(self.get(f"/api/papers/{pid}/annotations") or [])
        except Exception:  # noqa: BLE001
            return []

    def download(self, fid: Any, dest: Path) -> Tuple[int, str]:
        """論文庫的檔 → dest；回傳 (大小, sha256)。"""
        req = urllib.request.Request(f"{self.pl.base}/api/files/{fid}/content")
        req.add_header("X-PL", "1")
        req.add_header("Cookie", f"{self.pl.COOKIE}={self.sess}")
        h = hashlib.sha256()
        n = 0
        with self.pl._opener.open(req, timeout=120) as r, open(dest, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                h.update(b)
                f.write(b)
                n += len(b)
        return n, h.hexdigest()


# ---------------------------------------------------------------- 匯出
class Export:
    def __init__(self, st: Store, src: Source, opts: Dict[str, Any], by: str, hub_url: str = "") -> None:
        self.st, self.src, self.opts, self.by = st, src, opts, by
        self.paper_url = getattr(src.platform, "paper_url", lambda i: "")
        self.warn: List[Dict[str, Any]] = []
        self.proposed: List[Dict[str, Any]] = []

    def W(self, pid: str, code: str, sev: str, desc: str, act: str = "") -> None:
        self.warn.append({"Paper ID": pid, "warning_code": code, "severity": sev, "description": desc,
                          "action_required": act})

    def progress(self, msg: str, i: int = 0, n: int = 0) -> None:
        self.st.job.update(msg=msg, i=i, n=n)

    def run(self) -> Dict[str, Any]:
        o = self.opts
        scope = str(o.get("scope") or "all")
        self.progress("讀論文清單…")
        cats = self.src.categories()
        if scope.startswith("cat:"):
            items = self.src.list_papers(cat=scope[4:])
        else:
            items = self.src.list_papers()
            if scope.startswith("project:"):
                keep = set(str(i) for i in self.src.project_paper_ids(scope[8:]))
                items = [p for p in items if str(p.get("id")) in keep]
        if not items:
            raise HubHTTPError(400, "這個範圍沒有論文")
        details: List[Dict[str, Any]] = []
        for k, it in enumerate(items):
            self.progress("讀每篇的書目與重點…", k, len(items))
            d = self.src.detail(it["id"])
            d["_ann"] = self.src.annotations(it["id"]) if int(d.get("n_ann") or it.get("n_ann") or 0) else []
            details.append(d)
        # ---- 同一個 DOI / arXiv = 同一篇研究工作 → 一列
        groups: Dict[str, List[Dict[str, Any]]] = {}
        order: List[str] = []
        for d in details:
            doi, ax = norm_doi(d.get("doi")), norm_arxiv(d.get("arxiv"))
            key = f"doi:{doi}" if doi else (f"arxiv:{ax}" if ax else f"pl:{d.get('id')}")
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(d)
        rows, notes, evid, mani = [], [], [], []
        today = datetime.now().strftime("%Y-%m-%d")
        tmpdir = Path(tempfile.mkdtemp(prefix="gptexport-", dir=str(self.st.dir)))
        name = f"{ROOT}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        k = 1
        while (self.st.dir / "exports" / f"{name}.zip").exists():
            k += 1
            name = f"{ROOT}_{datetime.now().strftime('%Y%m%d-%H%M%S')}-{k}"
        zpath = self.st.dir / "exports" / f"{name}.zip"
        part = zpath.with_suffix(".part")
        want_pdf = str(o.get("pdf") or "all")
        grades = {g for g in (o.get("grades") or []) if g in GRADES}
        n_pdf = 0
        seen_title: Dict[Tuple[str, Any], str] = {}
        try:
            with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
                for gi, key in enumerate(order):
                    g = sorted(groups[key], key=lambda d: int(d.get("id") or 0))
                    d = g[0]
                    self.progress("整理與下載 PDF…", gi, len(order))
                    doi, ax = norm_doi(d.get("doi")), norm_arxiv(d.get("arxiv"))
                    mag = self.st.assign(d.get("id"), doi, ax)
                    for other in g[1:]:
                        self.st.assign(other.get("id"), doi, ax)
                    row, note, ev, m = self.paper(d, g, mag, cats, today)
                    # ---- PDF
                    grade = str(d.get("relevance_grade") or "").upper()
                    main = next((f for f in d.get("files") or [] if f.get("role") == "main"), None) or \
                        next(iter(d.get("files") or []), None)
                    take = want_pdf == "all" or (want_pdf == "grades" and grade in grades)
                    if main and take:
                        rel = f"papers/{mag}__{short_title(d.get('title', ''))}.pdf"
                        tmp = tmpdir / "f.pdf"
                        try:
                            size, sha = self.src.download(main["id"], tmp)
                            z.write(tmp, f"{ROOT}/{rel}", compress_type=zipfile.ZIP_STORED)
                            row["PDF 檔名"] = rel
                            m.update(pdf_relative_path=rel, sha256=sha, content_access="full_text",
                                     export_permission="authorized",
                                     source_version=f"論文庫主檔：{main.get('original_name') or main.get('filename') or ''}")
                            n_pdf += 1
                        except Exception as e:  # noqa: BLE001
                            self.W(mag, "PDF_UNREADABLE", "warning", f"下載 PDF 失敗：{e}", "到論文庫確認檔案")
                        finally:
                            try:
                                tmp.unlink()
                            except OSError:
                                pass
                    elif main:
                        self.W(mag, "RESTRICTED_SOURCE", "info", "依匯出設定未附 PDF（論文庫有 PDF）",
                               "需要時再匯出或到論文庫下載")
                    else:
                        self.W(mag, "PDF_MISSING", "info", "論文庫沒有 PDF", "")
                    if not row["PDF 檔名"]:
                        m.update(content_access="abstract_only" if d.get("abstract") else "metadata_only",
                                 export_permission="metadata_only")
                    tk = (re.sub(r"\W+", "", str(d.get("title") or "").lower())[:80], d.get("year"))
                    if tk[0] and tk in seen_title and seen_title[tk] != mag:
                        self.W(mag, "POSSIBLE_DUPLICATE", "warning", f"標題與年份跟 {seen_title[tk]} 相同，但 DOI／arXiv 不同或缺少",
                               f"核對是否同一篇；候選 {seen_title[tk]}")
                    seen_title.setdefault(tk, mag)
                    rows.append(row)
                    notes += note
                    evid += ev
                    mani.append(m)
                    z.writestr(f"{ROOT}/notes/{mag}.md", self.note_md(d, row, mag))
                self.st.save_ids()
                rows.sort(key=lambda r: r["Paper ID"])
                mani.sort(key=lambda r: r["paper_id"])
                site = self.src.site()
                manifest = {
                    "schema_version": SCHEMA, "exported_at": today, "producer": f"QEL Hub {__version__}（論文庫匯出）",
                    "source_system": f"LAB-QEL 論文庫（paperlib {site.get('version', '')}）經 QEL Hub {__version__}",
                    "paper_count": len(rows), "pdf_count": n_pdf, "papers": mani,
                    "export_options": {"scope": scope, "pdf": want_pdf, "grades": sorted(grades), "by": self.by},
                    "notes": ["優先 A-D = 論文庫「關聯性」（README 1.1 第 3.1 節）",
                              "閱讀狀態一律「未讀」（README 1.1 第 3.2 節）",
                              "主要主題／次級標籤／論文類型 = Hub 分類編輯頁；沒編過的只帶入論文庫標籤中剛好是受控字彙的",
                              "論文庫重點欄由 AI 產生者標「AI interpretation」，人工填寫者標「論文庫人工填寫」；都未與 PDF 逐項核對"],
                }
                report = self.validate(rows, mani, notes, evid, z)
                manifest["validation"] = report
                z.writestr(f"{ROOT}/README.md", README.read_text(encoding="utf-8"))
                z.writestr(f"{ROOT}/metadata/papers.csv", _csv(rows, PAPERS_COLS))
                z.writestr(f"{ROOT}/metadata/notes.csv", _csv(notes, NOTES_COLS))
                z.writestr(f"{ROOT}/metadata/evidence.csv", _csv(evid, EVIDENCE_COLS))
                z.writestr(f"{ROOT}/metadata/classification_warnings.csv", _csv(self.warn, WARN_COLS))
                z.writestr(f"{ROOT}/metadata/proposed_tags.csv", _csv(self.proposed, PROPOSED_COLS))
                z.writestr(f"{ROOT}/metadata/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            os.replace(part, zpath)
        finally:
            try:
                part.unlink()
            except OSError:
                pass
            try:
                for f in tmpdir.iterdir():
                    f.unlink()
                tmpdir.rmdir()
            except OSError:
                pass
        summary = self.summary(rows, n_pdf, report)
        meta = {"by": self.by, "options": manifest["export_options"], "summary": summary}
        (self.st.dir / "exports" / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.st.prune()
        return dict(meta, name=zpath.name)

    # ---- 一篇
    def paper(self, d: Dict[str, Any], group: List[Dict[str, Any]], mag: str, cats: Dict[Any, str],
              today: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        pid = d.get("id")
        doi, ax = norm_doi(d.get("doi")), norm_arxiv(d.get("arxiv"))
        saved = self.st.classes.get(str(pid))
        auto = auto_class(d, cats)
        c = saved or auto
        for t in auto["other_tags"]:
            self.proposed.append({"Paper ID": mag, "建議標籤": t, "原因": "論文庫標籤，不在受控字彙（未自動帶入）"})
        ki = d.get("keyinfo") if isinstance(d.get("keyinfo"), dict) else {}
        ai_keys = set(d.get("ai_keys") or [])

        def kv(field: str) -> str:
            v = _text(ki.get(field))
            if not v:
                return ""
            return f"[{'AI interpretation' if field in ai_keys else '論文庫人工填寫'}] {v}"
        grade = str(d.get("relevance_grade") or "").upper()
        grade = grade if grade in GRADES else ""
        rel = []
        if grade:
            how = "AI 評級" if d.get("relevance_ai") else "人工"
            conf = str(d.get("relevance_confidence") or "")
            rel.append(f"論文庫關聯性 {grade}（{how}{'，信心 ' + conf if conf and d.get('relevance_ai') else ''}）"
                       + (f"：{_text(d.get('relevance_reason'))}" if _text(d.get("relevance_reason")) else ""))
        if kv("與我們實驗的關係"):
            rel.append(kv("與我們實驗的關係"))
        limits = []
        if not c.get("topic"):
            limits.append("主分類待人工確認")
            self.W(mag, "PRIMARY_TOPIC_UNCERTAIN", "warning", "還沒有主要主題", "在 Hub「論文匯出」頁分類")
        if not grade:
            self.W(mag, "PRIORITY_PENDING", "info", "論文庫還沒有關聯性 A–D", "在論文庫評級（人工或「AI 只填關聯性」）")
        if not doi:
            self.W(mag, "DOI_MISSING", "info" if ax else "warning", "沒有 DOI" + ("（有 arXiv）" if ax else ""),
                   "在論文庫補 DOI" if not ax else "")
        if any(f in ai_keys for f in ki):
            limits.append("論文庫重點欄含 AI 產生內容，未與 PDF 逐項核對")
            self.W(mag, "EVIDENCE_NOT_VERIFIED", "info", "重點欄含 AI 產生內容", "需要時依 PDF 核對")
        if len(group) > 1:
            others = "、".join(str(x.get("id")) for x in group[1:])
            limits.append(f"論文庫有重複條目（id {others}），已併為一筆")
            self.W(mag, "POSSIBLE_DUPLICATE", "info", f"論文庫 id {pid} 與 {others} 的 DOI／arXiv 相同，已併為 {mag}",
                   "可在論文庫合併重複條目")
        updated = max(filter(None, [_date(d.get("updated_at")), _date(d.get("relevance_updated_at")),
                                    datetime.fromtimestamp(saved["updated"]).strftime("%Y-%m-%d") if saved else ""]),
                      default=today)
        url = f"https://arxiv.org/abs/{ax}" if ax else ""
        row = {
            "Paper ID": mag, "優先 A-D": grade, "閱讀狀態": "未讀", "主要主題": c.get("topic") or "",
            "次級標籤": "; ".join(c.get("tags") or []), "論文標題 Title": _text(d.get("title")),
            "第一作者": first_author(d.get("authors")),
            "出版年": str(d.get("year")) if re.fullmatch(r"\d{4}", str(d.get("year") or "")) else "",
            "期刊 / 來源": _text(d.get("venue")), "DOI": doi, "arXiv / URL": url, "PDF 檔名": "",
            "論文類型": c.get("type") or "", "研究問題": kv("重點"), "物理系統": kv("架設／平台"),
            "核心模型 / Hamiltonian": "", "耦合機制": "", "CPA / EP 條件": "", "量測方法": "", "關鍵式 / 圖": "",
            "與本實驗室關聯": "\n".join(rel), "重要限制 / 待確認": "；".join(limits), "原文定位": "",
            "更新日期": updated,
        }
        notes = []
        if any(kv(f) for f in ("重點", "架設／平台", "關鍵參數", "可萃取特徵／觀測量", "與我們實驗的關係", "備註")):
            only_ai = all(f in ai_keys for f in ki if _text(ki.get(f)))
            notes.append({
                "Paper ID": mag, "研究問題與創新": kv("重點"), "系統架構/器件": kv("架設／平台"),
                "實驗裝置與頻段": ("關鍵參數：" + kv("關鍵參數")) if kv("關鍵參數") else "",
                "主要結果": ("可萃取特徵／觀測量：" + kv("可萃取特徵／觀測量")) if kv("可萃取特徵／觀測量") else "",
                "與 CPAEP/CLS/PIM 的關聯": kv("與我們實驗的關係"),
                "尚未解決問題": ("備註：" + kv("備註")) if kv("備註") else "",
                "證據來源（頁、式、圖）": "論文庫「重點」欄（未附頁碼）",
                "人工核對狀態": "未核對" if only_ai else "部分核對", "更新日期": updated})
        ev = []
        main_ids = {f.get("id") for f in d.get("files") or [] if f.get("role") == "main"}
        files = {f.get("id"): f for f in d.get("files") or []}
        for a in d.get("_ann") or []:
            if a.get("private") or a.get("nb") or a.get("kind") == "ink":
                continue
            quote, body = _text(a.get("quote")), _text(a.get("body"))
            if not quote:
                continue
            page = a.get("page")
            fid = a.get("file_id")
            where = ""
            if page:
                where = f"PDF p. {page}" if (fid in main_ids or not fid) else \
                    f"附檔「{(files.get(fid) or {}).get('label') or (files.get(fid) or {}).get('original_name') or fid}」p. {page}"
            ev.append({
                "證據 ID": self.st.evidence_id(a.get("id")), "研究命題 / 比較問題": body, "Paper ID": mag,
                "主題": c.get("topic") or "", "結論／公式": quote, "原文頁/式/圖": where,
                "來源 DOI / URL": f"https://doi.org/{doi}" if doi else url, "可直接比較？": "待核對",
                "目前判斷": "Verified from PDF（論文庫劃線的原文）；註解為成員筆記" if body else "Verified from PDF（論文庫劃線的原文）",
                "需再驗證的點": "", "最後更新": _date(a.get("updated_at")) or updated})
        m = {"paper_id": mag, "doi_normalized": doi, "arxiv_id": ax, "pdf_relative_path": "", "sha256": "",
             "source_path": f"paperlib:/#/p/{pid}", "source_url": f"https://doi.org/{doi}" if doi else url,
             "source_version": "", "content_access": "metadata_only", "export_permission": "metadata_only",
             "paperlib_ids": [x.get("id") for x in group], "classification": "hub" if saved else "auto"}
        return row, notes, ev, m

    def note_md(self, d: Dict[str, Any], row: Dict[str, Any], mag: str) -> str:
        L = [f"# {mag} — {row['論文標題 Title']}", ""]
        for k in ("第一作者", "出版年", "期刊 / 來源", "DOI", "arXiv / URL", "優先 A-D", "主要主題", "次級標籤", "論文類型",
                  "PDF 檔名"):
            if row.get(k):
                L.append(f"- **{k}**：{row[k]}")
        au = d.get("authors")
        if isinstance(au, list) and len(au) > 1:
            L.append(f"- **作者**：{'; '.join(_text(a) for a in au)}")
        if _text(d.get("abstract")):
            L += ["", "## 摘要（論文庫書目資料）", "", _text(d.get("abstract"))]
        ki = d.get("keyinfo") if isinstance(d.get("keyinfo"), dict) else {}
        ai_keys = set(d.get("ai_keys") or [])
        if any(_text(v) for v in ki.values()):
            L += ["", "## 論文庫重點欄", ""]
            for k, v in ki.items():
                if _text(v):
                    L.append(f"- **{k}**（{'AI interpretation' if k in ai_keys else '人工填寫'}）：{_text(v)}")
        if row.get("與本實驗室關聯"):
            L += ["", "## 與本實驗室關聯", "", row["與本實驗室關聯"]]
        anns = [a for a in d.get("_ann") or [] if not a.get("private") and not a.get("nb") and a.get("kind") != "ink"
                and (_text(a.get("quote")) or _text(a.get("body")))]
        if anns:
            L += ["", "## 成員標註（公開）", ""]
            for a in anns:
                p = f"p. {a['page']}" if a.get("page") else "整篇"
                q = _text(a.get("quote"))
                L.append(f"- {p}：" + (f"「{q}」" if q else "") + (f" — {_text(a.get('body'))}" if _text(a.get("body")) else ""))
        L += ["", "> 本檔由 QEL Hub 依論文庫資料產生；AI interpretation 為論文庫 AI 產生、未與 PDF 逐項核對。", ""]
        return "\n".join(L)

    # ---- 驗收
    def validate(self, rows, mani, notes, evid, z: zipfile.ZipFile) -> Dict[str, Any]:
        errs: List[str] = []
        ids = [r["Paper ID"] for r in rows]
        if len(ids) != len(set(ids)):
            errs.append("Paper ID 有重複")
        for r in rows:
            if r["主要主題"] and r["主要主題"] not in TOPIC_VALUES:
                errs.append(f"{r['Paper ID']} 主要主題不在 8 個值內")
            for t in [x.strip() for x in r["次級標籤"].split(";") if x.strip()]:
                if t not in TAGS:
                    errs.append(f"{r['Paper ID']} 標籤「{t}」不在受控字彙")
            if r["優先 A-D"] not in ("",) + GRADES:
                errs.append(f"{r['Paper ID']} 優先 A-D 不合法")
            if r["出版年"] and not re.fullmatch(r"\d{4}", r["出版年"]):
                errs.append(f"{r['Paper ID']} 出版年格式")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["更新日期"]):
                errs.append(f"{r['Paper ID']} 更新日期格式")
            if r["PDF 檔名"] and f"{ROOT}/{r['PDF 檔名']}" not in z.namelist():
                errs.append(f"{r['Paper ID']} PDF 路徑不存在")
        dois = [r["DOI"] for r in rows if r["DOI"]]
        if len(dois) != len(set(dois)):
            errs.append("同一個 DOI 出現在多列")
        if len(mani) != len(rows):
            errs.append("manifest 筆數與 papers.csv 不同")
        known = set(ids)
        for x in notes + evid:
            if x["Paper ID"] not in known:
                errs.append(f"{x['Paper ID']} 出現在 notes／evidence 但不在 papers.csv")
        evids = [e["證據 ID"] for e in evid]
        if len(evids) != len(set(evids)):
            errs.append("證據 ID 重複")
        return {"ok": not errs, "errors": errs[:50], "checked": [
            "papers.csv 24 欄表頭", "Paper ID 唯一", "主要主題 8 值", "次級標籤受控字彙", "優先 A-D 合法值",
            "出版年／更新日期格式", "PDF 相對路徑存在", "DOI 不重複", "manifest 筆數一致", "notes／evidence 的 Paper ID 存在"]}

    def summary(self, rows, n_pdf: int, report: Dict[str, Any]) -> Dict[str, Any]:
        grades = {g: sum(1 for r in rows if r["優先 A-D"] == g) for g in GRADES}
        grades["未評級"] = sum(1 for r in rows if not r["優先 A-D"])
        topics = {t: sum(1 for r in rows if r["主要主題"] == t) for t in TOPIC_VALUES}
        topics["未分類"] = sum(1 for r in rows if not r["主要主題"])
        codes: Dict[str, int] = {}
        for w in self.warn:
            codes[w["warning_code"]] = codes.get(w["warning_code"], 0) + 1
        return {"papers": len(rows), "pdfs": n_pdf, "grades": grades, "topics": topics, "warnings": codes,
                "validation": report}


# ---------------------------------------------------------------- 路由
def _src(h: Handler) -> Source:
    sess = h.session()
    if sess is None:
        raise HubHTTPError(401, "需要用論文庫帳號登入（匯出用你自己的論文庫權限）")
    return Source(getattr(h.hub, "platform", None), sess)


@route("GET", r"/api/gptexport/vocab", auth="any")
def _vocab(h: Handler) -> None:
    h._json({"topics": [{"code": c, "value": t} for c, t in TOPICS],
             "tag_groups": [{"group": g, "tags": ts} for g, ts in TAG_GROUPS], "types": TYPES, "schema": SCHEMA})


@route("GET", r"/api/gptexport/papers", auth="user")
def _papers(h: Handler) -> None:
    src = _src(h)
    st = store(h.hub)
    cats = src.categories()
    out = []
    for p in src.list_papers():
        doi, ax = norm_doi(p.get("doi")), norm_arxiv(p.get("arxiv"))
        auto = auto_class(p, cats)
        saved = st.classes.get(str(p.get("id")))
        out.append({"id": p.get("id"), "mag": st.peek(p.get("id"), doi, ax), "title": p.get("title"),
                    "year": p.get("year"), "first_author": first_author(p.get("authors")), "venue": p.get("venue"),
                    "doi": doi, "grade": str(p.get("relevance_grade") or "").upper(),
                    "grade_ai": bool(p.get("relevance_ai")), "tags": p.get("tags") or [],
                    "categories": [cats.get(c, str(c)) for c in p.get("categories") or []],
                    "has_pdf": bool(p.get("file_ids")), "saved": saved,
                    "auto": {k: v for k, v in auto.items() if k != "other_tags"},
                    "url": src.platform.paper_url(p.get("id")) if hasattr(src.platform, "paper_url") else ""})
    h._json({"papers": out})


@route("PUT", r"/api/gptexport/class/(\d+)", auth="user")
def _set_class(h: Handler, pid: str) -> None:
    by = (h.user() or {}).get("username", "")
    h._json({"ok": True, "saved": store(h.hub).set_class(pid, h._json_body(), by)})


@route("GET", r"/api/gptexport/scopes", auth="user")
def _scopes(h: Handler) -> None:
    src = _src(h)
    cats = [{"id": k, "name": v} for k, v in src.categories().items() if isinstance(k, int)]
    projects = []
    try:
        projects = [{"id": p["id"], "name": p.get("name")} for p in h.hub.platform.projects(h.session())]
    except Exception:  # noqa: BLE001
        pass
    h._json({"categories": cats, "projects": projects})


@route("POST", r"/api/gptexport/export", auth="user")
def _export(h: Handler) -> None:
    st = store(h.hub)
    src = _src(h)
    b = h._json_body()
    if st.job.get("running"):
        raise HubHTTPError(409, "已經有一個匯出在進行")
    opts = {"scope": str(b.get("scope") or "all"), "pdf": str(b.get("pdf") or "all"),
            "grades": [g for g in (b.get("grades") or []) if g in GRADES]}
    if opts["pdf"] not in ("all", "none", "grades"):
        raise HubHTTPError(400, "pdf 只能是 all / none / grades")
    by = (h.user() or {}).get("username", "")
    st.job = {"running": True, "by": by, "msg": "開始…", "i": 0, "n": 0, "t0": time.time(), "opts": opts}

    def work() -> None:
        try:
            res = Export(st, src, opts, by).run()
            st.job.update(running=False, done=True, result=res, msg="完成")
        except Exception as e:  # noqa: BLE001
            st.job.update(running=False, done=True, error=str(e), msg="失敗")
    threading.Thread(target=work, name="gptexport", daemon=True).start()
    h._json({"ok": True, "job": st.job})


@route("GET", r"/api/gptexport/status", auth="user")
def _status(h: Handler) -> None:
    h._json({"job": store(h.hub).job})


def _can(h: Handler, meta: Dict[str, Any]) -> bool:
    u = h.user() or {}
    return bool(u.get("owner")) or meta.get("by") == u.get("username")


@route("GET", r"/api/gptexport/exports", auth="user")
def _exports(h: Handler) -> None:
    h._json({"exports": [x for x in store(h.hub).exports() if _can(h, x)]})


@route("GET", r"/api/gptexport/exports/([A-Za-z0-9_.\-]+\.zip)", auth="user")
def _download(h: Handler, name: str) -> None:
    st = store(h.hub)
    x = next((e for e in st.exports() if e["name"] == name), None)
    if x is None or not _can(h, x):
        raise HubHTTPError(404, "沒有這個匯出檔")
    h._send_file(st.dir / "exports" / name, "application/zip")


@route("GET", r"/gptexport/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "gptexport.html", "gptexport")
