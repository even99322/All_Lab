"""1.0.31：電子實驗記錄（ELN）——Hub 網頁「實驗記錄」（/notebook）。

每一筆記錄：標題、內文（Markdown，可以貼圖 / 附檔）、標籤、所屬專案（論文庫的主專案 / 子專案）、連結（數據檔、論文、網址）、
作者與時間（自動）、修改歷史（每次儲存留一版）、留言、共同編輯者。量測結束時 Lab Control 自動新增一筆
（方案、掃描範圍、儀器設定、存的檔、狀態），量測者之後補寫備註。

    GET    /api/notebook?q=&tag=&project=&author=&kind=&from=&to=&offset=&limit=   → 清單（新到舊）
    POST   /api/notebook                         {title, body, tags, project, links, editors} → 新增
    GET    /api/notebook/<id>                     → 一筆（含留言、誰可以改、誰正在編輯）
    PUT    /api/notebook/<id>                     {…, rev} → 修改（rev = 編輯時的版本；別人先存了 → 409 附上目前的內容）
    DELETE /api/notebook/<id>                     → 移到資源回收（作者或站長）
    GET    /api/notebook/<id>/history             → 歷史版本清單；/history/<rev> → 那一版
    POST   /api/notebook/<id>/comments            {text}；DELETE /api/notebook/<id>/comments/<cid>
    PUT    /api/notebook/<id>/editing             正在編輯（每 15 秒一次；別人看得到「某某正在編輯」）
    POST   /api/notebook/<id>/files?name=         原始檔案內容 → 附檔 / 圖片（{url, name, size}）
    GET    /api/notebook/<id>/files/<name>        → 附檔
    POST   /api/notebook/auto                     ← Lab Control 量測結束（裝置 token；同一個 run_id 只會有一筆）
    GET    /notebook                              → 網頁（清單、閱讀、編輯、列印 / 另存 PDF）

誰可以改：作者、共同編輯者、站長；量測自動新增的記錄任何登入的人都可以補寫。留言：任何登入的人。
資料存在 data/notebook/（entries/<id>.json、files/<id>/…、trash/）。只用標準函式庫。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .server import Handler, HubHTTPError, route

log = logging.getLogger(__name__)

MAX_BODY = 400_000              # 內文字數上限
MAX_FILE = 25 * 1024 * 1024
MAX_HISTORY = 60
EDITING_S = 40.0
_ID = re.compile(r"^[0-9]{8}-[0-9a-f]{6}$")
_NAME = re.compile(r"[^\w.\-()\[\] ]+", re.UNICODE)
INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf", ".txt", ".csv"}


def _s(v: Any, n: int = 200) -> str:
    return str(v if v is not None else "")[:n]


def _tags(v: Any) -> List[str]:
    if isinstance(v, str):
        v = re.split(r"[,，、]", v)
    out = []
    for t in v or []:
        t = _s(t, 40).strip().lstrip("#")
        if t and t not in out:
            out.append(t)
    return out[:30]


def _names(v: Any) -> List[str]:
    if isinstance(v, str):
        v = re.split(r"[,，、\s]+", v)
    return [n for n in dict.fromkeys(_s(x, 60).strip() for x in (v or [])) if n][:30]


def _project(v: Any) -> Dict[str, Any]:
    if not isinstance(v, dict) or not v.get("id"):
        return {}
    out: Dict[str, Any] = {"id": _s(v.get("id"), 60), "name": _s(v.get("name"), 120)}
    if isinstance(v.get("path"), list):
        out["path"] = [_s(x, 120) for x in v["path"]][:8]
    if isinstance(v.get("path_ids"), list):
        out["path_ids"] = [_s(x, 60) for x in v["path_ids"]][:8]
    if v.get("url"):
        out["url"] = _s(v.get("url"), 400)
    return out


def _links(v: Any) -> List[Dict[str, str]]:
    out = []
    for x in v or []:
        if not isinstance(x, dict):
            continue
        kind = _s(x.get("kind"), 10) or "url"
        if kind not in ("data", "paper", "url", "entry"):
            kind = "url"
        ref = _s(x.get("ref"), 600).strip()
        if not ref:
            continue
        if kind == "url" and not re.match(r"^(https?://|/)", ref):
            continue                                       # 不收 javascript: 之類
        out.append({"kind": kind, "ref": ref, "label": _s(x.get("label"), 200)})
    return out[:100]


def safe_name(name: str) -> str:
    base = Path(str(name or "")).name.strip()
    base = _NAME.sub("_", base).strip(" .") or "file"
    return base[:120]


class Notebook:
    def __init__(self, data_dir: Path, now=time.time) -> None:
        self.dir = Path(data_dir) / "notebook"
        self.entries_dir = self.dir / "entries"
        self.files_dir = self.dir / "files"
        self.trash_dir = self.dir / "trash"
        for d in (self.entries_dir, self.files_dir, self.trash_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.now = now
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.by_run: Dict[str, str] = {}
        self.editing: Dict[str, Dict[str, Any]] = {}
        for p in self.entries_dir.glob("*.json"):
            try:
                e = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("實驗記錄讀不到：%s", p.name)
                continue
            if isinstance(e, dict) and _ID.match(str(e.get("id", ""))):
                self.entries[e["id"]] = e
                rid = (e.get("auto") or {}).get("run_id")
                if rid:
                    self.by_run[rid] = e["id"]

    # ---- 存 ----
    def _save(self, e: Dict[str, Any]) -> None:
        p = self.entries_dir / f"{e['id']}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(e, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def _new_id(self) -> str:
        d = _dt.datetime.fromtimestamp(self.now()).strftime("%Y%m%d")
        while True:
            i = f"{d}-{secrets.token_hex(3)}"
            if i not in self.entries:
                return i

    # ---- 權限 ----
    @staticmethod
    def can_edit(e: Dict[str, Any], user: Dict[str, Any]) -> bool:
        if not user.get("username"):
            return False
        if user.get("owner") or e.get("open_edit"):
            return True
        u = user["username"]
        return u == e.get("author") or u in (e.get("editors") or [])

    @staticmethod
    def can_delete(e: Dict[str, Any], user: Dict[str, Any]) -> bool:
        return bool(user.get("owner")) or (bool(user.get("username")) and user.get("username") == e.get("author"))

    # ---- 讀 ----
    def get(self, eid: str) -> Dict[str, Any]:
        e = self.entries.get(eid)
        if e is None:
            raise KeyError(eid)
        return e

    def view(self, eid: str, user: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            e = dict(self.get(eid))
            e.pop("history", None)
            e["n_history"] = len(self.entries[eid].get("history") or [])
            e["can_edit"] = self.can_edit(e, user)
            e["can_delete"] = self.can_delete(e, user)
            now = self.now()
            ed = self.editing.get(eid) or {}
            e["editing"] = [v["name"] for k, v in ed.items() if now - v["t"] < EDITING_S and k != user.get("username")]
            return e

    def summary(self, e: Dict[str, Any]) -> Dict[str, Any]:
        body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "🖼", str(e.get("body") or ""))
        body = re.sub(r"[#*_`>|\-]+", " ", body)
        return {k: e.get(k) for k in ("id", "title", "author", "author_name", "created", "updated", "tags", "project",
                                     "kind", "rev")} | {
            "snippet": re.sub(r"\s+", " ", body).strip()[:160], "n_comments": len(e.get("comments") or []),
            "status": (e.get("auto") or {}).get("status", ""), "n_files": len(e.get("files") or [])}

    def search(self, q: Dict[str, str]) -> Dict[str, Any]:
        text = (q.get("q") or "").strip().lower()
        tag = (q.get("tag") or "").strip()
        proj = (q.get("project") or "").strip()
        author = (q.get("author") or "").strip()
        kind = (q.get("kind") or "").strip()
        lo = (q.get("from") or "").strip()
        hi = (q.get("to") or "").strip()
        try:
            offset = max(0, int(q.get("offset") or 0))
            limit = max(1, min(int(q.get("limit") or 100), 500))
        except ValueError:
            offset, limit = 0, 100
        with self.lock:
            rows = []
            for e in self.entries.values():
                if tag and tag not in (e.get("tags") or []):
                    continue
                if proj:
                    p = e.get("project") or {}
                    if proj != p.get("id") and proj not in (p.get("path_ids") or []):
                        continue
                if author and author not in (e.get("author"), e.get("author_name")):
                    continue
                if kind and e.get("kind") != kind:
                    continue
                day = _dt.datetime.fromtimestamp(float(e.get("created") or 0)).strftime("%Y-%m-%d")
                if (lo and day < lo) or (hi and day > hi):
                    continue
                if text:
                    hay = " ".join([str(e.get("title") or ""), str(e.get("body") or ""), " ".join(e.get("tags") or []),
                                    str(e.get("author_name") or ""), str((e.get("project") or {}).get("name") or ""),
                                    " ".join(c.get("text", "") for c in e.get("comments") or [])]).lower()
                    if not all(w in hay for w in text.split()):
                        continue
                rows.append(e)
            rows.sort(key=lambda e: -float(e.get("created") or 0))
            tags: Dict[str, int] = {}
            for e in self.entries.values():
                for t in e.get("tags") or []:
                    tags[t] = tags.get(t, 0) + 1
            return {"total": len(rows), "items": [self.summary(e) for e in rows[offset:offset + limit]],
                    "tags": sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))[:100]}

    # ---- 寫 ----
    def _apply(self, e: Dict[str, Any], b: Dict[str, Any]) -> None:
        if "title" in b:
            e["title"] = _s(b.get("title"), 200).strip() or "（無標題）"
        if "body" in b:
            body = str(b.get("body") or "")
            if len(body) > MAX_BODY:
                raise ValueError(f"內文太長（上限 {MAX_BODY} 字）")
            e["body"] = body
        if "tags" in b:
            e["tags"] = _tags(b.get("tags"))
        if "project" in b:
            e["project"] = _project(b.get("project"))
        if "links" in b:
            e["links"] = _links(b.get("links"))
        if "editors" in b:
            e["editors"] = _names(b.get("editors"))

    def create(self, user: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        now = self.now()
        with self.lock:
            e = {"id": self._new_id(), "title": "（無標題）", "body": "", "tags": [], "project": {}, "links": [],
                 "editors": [], "comments": [], "files": [], "history": [], "kind": "manual", "rev": 1,
                 "author": _s(user.get("username"), 60), "author_name": _s(user.get("display_name") or user.get("username"), 60),
                 "created": now, "updated": now, "updated_by": _s(user.get("display_name") or user.get("username"), 60)}
            self._apply(e, b)
            self.entries[e["id"]] = e
            self._save(e)
            return e

    def update(self, eid: str, user: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            e = self.get(eid)
            if not self.can_edit(e, user):
                raise PermissionError("只有作者、共同編輯者或站長可以修改")
            if "editors" in b and not (user.get("owner") or user.get("username") == e.get("author")):
                b = {k: v for k, v in b.items() if k != "editors"}           # 只有作者 / 站長能改共同編輯者
            rev = b.get("rev")
            if rev is not None and int(rev) != int(e.get("rev") or 1):
                raise Conflict(self.view(eid, user))
            old = {k: e.get(k) for k in ("rev", "title", "body", "tags", "project", "links")}
            old.update(time=e.get("updated"), user=e.get("updated_by"))
            before = json.dumps({k: e.get(k) for k in ("title", "body", "tags", "project", "links", "editors")},
                                sort_keys=True, ensure_ascii=False)
            self._apply(e, b)
            after = json.dumps({k: e.get(k) for k in ("title", "body", "tags", "project", "links", "editors")},
                               sort_keys=True, ensure_ascii=False)
            if before == after:
                return e
            hist = e.setdefault("history", [])
            hist.append(old)
            del hist[:-MAX_HISTORY]
            e["rev"] = int(e.get("rev") or 1) + 1
            e["updated"] = self.now()
            e["updated_by"] = _s(user.get("display_name") or user.get("username"), 60)
            self._save(e)
            return e

    def delete(self, eid: str, user: Dict[str, Any]) -> None:
        with self.lock:
            e = self.get(eid)
            if not self.can_delete(e, user):
                raise PermissionError("只有作者或站長可以刪除")
            e["deleted"] = {"time": self.now(), "by": user.get("username")}
            (self.trash_dir / f"{eid}.json").write_text(json.dumps(e, ensure_ascii=False), encoding="utf-8")
            (self.entries_dir / f"{eid}.json").unlink(missing_ok=True)
            fd = self.files_dir / eid
            if fd.exists():
                shutil.move(str(fd), str(self.trash_dir / f"{eid}_files"))
            self.entries.pop(eid, None)
            rid = (e.get("auto") or {}).get("run_id")
            if rid:
                self.by_run.pop(rid, None)

    def history(self, eid: str) -> List[Dict[str, Any]]:
        e = self.get(eid)
        return [{"rev": h.get("rev"), "time": h.get("time"), "user": h.get("user"), "title": h.get("title"),
                 "size": len(h.get("body") or "")} for h in reversed(e.get("history") or [])]

    def version(self, eid: str, rev: int) -> Dict[str, Any]:
        e = self.get(eid)
        for h in e.get("history") or []:
            if int(h.get("rev") or 0) == int(rev):
                return h
        raise KeyError(rev)

    def comment(self, eid: str, user: Dict[str, Any], text: str) -> Dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("留言是空的")
        with self.lock:
            e = self.get(eid)
            c = {"id": secrets.token_hex(4), "user": _s(user.get("username"), 60),
                 "name": _s(user.get("display_name") or user.get("username"), 60), "text": text[:5000], "time": self.now()}
            e.setdefault("comments", []).append(c)
            self._save(e)
            return c

    def delete_comment(self, eid: str, cid: str, user: Dict[str, Any]) -> None:
        with self.lock:
            e = self.get(eid)
            cs = e.get("comments") or []
            c = next((x for x in cs if x.get("id") == cid), None)
            if c is None:
                raise KeyError(cid)
            if not (user.get("owner") or c.get("user") == user.get("username")):
                raise PermissionError("只能刪自己的留言")
            cs.remove(c)
            self._save(e)

    def touch_editing(self, eid: str, user: Dict[str, Any], on: bool = True) -> None:
        with self.lock:
            self.get(eid)
            d = self.editing.setdefault(eid, {})
            u = user.get("username") or ""
            if on:
                d[u] = {"name": user.get("display_name") or u, "t": self.now()}
            else:
                d.pop(u, None)

    # ---- 附檔 ----
    def add_file(self, eid: str, user: Dict[str, Any], name: str, data: bytes) -> Dict[str, Any]:
        if len(data) > MAX_FILE:
            raise ValueError(f"檔案太大（上限 {MAX_FILE // 1024 // 1024} MB）")
        with self.lock:
            e = self.get(eid)
            if not self.can_edit(e, user):
                raise PermissionError("只有可以修改這筆記錄的人能附檔")
            d = self.files_dir / eid
            d.mkdir(parents=True, exist_ok=True)
            base = safe_name(name)
            stem, ext = os.path.splitext(base)
            n, k = base, 2
            while (d / n).exists():
                n, k = f"{stem}_{k}{ext}", k + 1
            (d / n).write_bytes(data)
            info = {"name": n, "size": len(data), "time": self.now(), "by": user.get("username") or "",
                    "sha256": hashlib.sha256(data).hexdigest()[:16]}
            e.setdefault("files", []).append(info)
            self._save(e)
            return dict(info, url=f"/api/notebook/{eid}/files/{n}")

    def file_path(self, eid: str, name: str) -> Path:
        self.get(eid)
        p = (self.files_dir / eid / safe_name(name))
        if not p.is_file() or p.parent.resolve() != (self.files_dir / eid).resolve():
            raise KeyError(name)
        return p

    # ---- 量測自動新增 ----
    def auto(self, b: Dict[str, Any]) -> Dict[str, Any]:
        rid = _s(b.get("run_id"), 80).strip()
        if not rid:
            raise ValueError("缺少 run_id")
        info = clean_auto(b)
        with self.lock:
            eid = self.by_run.get(rid)
            if eid and eid in self.entries:
                e = self.entries[eid]
                old = e.get("auto") or {}
                files = list(dict.fromkeys([*(old.get("files") or []), *info["files"]]))
                e["auto"] = {**old, **info, "files": files}
                if int(e.get("rev") or 1) == 1:              # 還沒有人改過 → 跟著更新
                    e["title"] = auto_title(e["auto"])
                    e["body"] = auto_body(e["auto"])
                e["updated"] = self.now()
                self._save(e)
                return e
            now = self.now()
            e = {"id": self._new_id(), "title": auto_title(info), "body": auto_body(info), "tags": _tags(info.get("tags")),
                 "project": _project(info.get("project")), "links": [], "editors": [], "comments": [], "files": [],
                 "history": [], "kind": "auto", "open_edit": True, "rev": 1, "auto": info,
                 "author": "", "author_name": info.get("user") or info.get("host") or "量測電腦",
                 "created": float(info.get("started") or now), "updated": now, "updated_by": "Lab Control"}
            self.entries[e["id"]] = e
            self.by_run[rid] = e["id"]
            self._save(e)
            return e


class Conflict(Exception):
    def __init__(self, current: Dict[str, Any]) -> None:
        super().__init__("別人已經先存了新的版本")
        self.current = current


STATUS = {"finished": "完成", "stopped": "中途停止", "aborted": "中止", "error": "錯誤", "failed": "失敗"}


def clean_auto(b: Dict[str, Any]) -> Dict[str, Any]:
    def num(v: Any) -> Optional[float]:
        try:
            f = float(v)
            return f if f == f else None
        except (TypeError, ValueError):
            return None
    axes = []
    for a in (b.get("axes") or [])[:10]:
        if isinstance(a, dict):
            axes.append({"name": _s(a.get("name"), 80), "unit": _s(a.get("unit"), 20), "start": num(a.get("start")),
                         "stop": num(a.get("stop")), "n": int(num(a.get("n")) or 0), "target": _s(a.get("target"), 80)})
    measures = []
    for m in (b.get("measures") or [])[:10]:
        if isinstance(m, dict):
            st = m.get("settings") if isinstance(m.get("settings"), dict) else {}
            measures.append({"instrument": _s(m.get("instrument"), 60), "traces": [_s(t, 20) for t in m.get("traces") or []][:20],
                             "settings": {_s(k, 40): _s(v, 80) for k, v in list(st.items())[:40]}})
    setup = b.get("setup") if isinstance(b.get("setup"), dict) else {}
    files = [_s(f, 300) for f in (b.get("files") or []) if f][:200]
    return {"run_id": _s(b.get("run_id"), 80), "scheme": _s(b.get("scheme"), 200), "status": _s(b.get("status"), 20),
            "host": _s(b.get("host"), 80), "node": _s(b.get("node"), 80), "user": _s(b.get("user"), 60),
            "started": num(b.get("started")), "finished": num(b.get("finished")), "points": int(num(b.get("points")) or 0),
            "planned": int(num(b.get("planned")) or 0), "axes": axes, "measures": measures,
            "setup": {_s(k, 80): _s(v, 80) for k, v in list(setup.items())[:60]}, "files": files,
            "comment": _s(b.get("comment"), 2000), "tags": _tags(b.get("tags")), "project": _project(b.get("project")),
            "version": _s(b.get("version"), 20)}


def auto_title(a: Dict[str, Any]) -> str:
    t = _dt.datetime.fromtimestamp(float(a.get("started") or time.time())).strftime("%m/%d %H:%M")
    return f"量測：{a.get('scheme') or '量測'}（{t}）"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def auto_body(a: Dict[str, Any]) -> str:
    """量測自動新增的內文（Markdown）。"""
    def ts(t):
        return _dt.datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M:%S") if t else "—"
    st = STATUS.get(a.get("status", ""), a.get("status") or "")
    dur = ""
    if a.get("started") and a.get("finished"):
        s = int(float(a["finished"]) - float(a["started"]))
        dur = f"（{s // 3600} 小時 {s % 3600 // 60} 分）" if s >= 3600 else f"（{s // 60} 分 {s % 60} 秒）"
    lines = [f"**量測方案**：{a.get('scheme') or '—'}　**狀態**：{st or '—'}"
             + (f"（{a.get('points')} / {a.get('planned')} 點）" if a.get("planned") else ""),
             f"**量測電腦**：{a.get('node') or a.get('host') or '—'}　**量測者**：{a.get('user') or '—'}",
             f"**時間**：{ts(a.get('started'))} → {ts(a.get('finished'))}{dur}", ""]
    if a.get("comment"):
        lines += [f"> {a['comment']}", ""]
    if a.get("axes"):
        lines += ["### 掃描", "", "| 軸 | 起 | 迄 | 點數 |", "|---|---|---|---|"]
        for x in a["axes"]:
            lines.append(f"| {x['name']} | {_fmt(x['start'])} {x['unit']} | {_fmt(x['stop'])} {x['unit']} | {x['n']} |")
        lines.append("")
    if a.get("measures"):
        lines += ["### 量測", ""]
        for m in a["measures"]:
            sets = "、".join(f"{k} = {v}" for k, v in m["settings"].items())
            lines.append(f"- **{m['instrument']}**：{'、'.join(m['traces'])}" + (f"（{sets}）" if sets else ""))
        lines.append("")
    if a.get("setup"):
        lines += ["### 固定設定", ""] + [f"- {k} = {v}" for k, v in a["setup"].items()] + [""]
    lines += ["### 備註", "", "（在這裡補寫：樣品、目的、觀察到什麼、下一步…）", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Hub 路由
# ---------------------------------------------------------------------------
def store(h: Handler) -> Notebook:
    nb = getattr(h.hub, "notebook", None)
    if nb is None:
        nb = h.hub.notebook = Notebook(h.hub.cfg.data_dir)
    return nb


def _user(h: Handler) -> Dict[str, Any]:
    try:
        u = dict(h.user() or {})
    except Exception:  # noqa: BLE001
        u = {}
    return u


def _need_user(h: Handler) -> Dict[str, Any]:
    u = _user(h)
    if not u.get("username"):
        raise HubHTTPError(403, "要用論文庫帳號登入才能寫實驗記錄")
    return u


def _body(h: Handler) -> Dict[str, Any]:
    b = h._json_body() or {}
    if not isinstance(b, dict):
        raise HubHTTPError(400, "格式錯誤")
    return b


def _get(h: Handler, eid: str):
    try:
        return store(h).get(eid)
    except KeyError:
        raise HubHTTPError(404, "沒有這筆實驗記錄") from None


@route("GET", r"/api/notebook", auth="any")
def _list(h: Handler) -> None:
    h._json({"ok": True, **store(h).search(h._query()), "me": {k: _user(h).get(k) for k in ("username", "display_name", "owner")}})


@route("POST", r"/api/notebook", auth="any")
def _create(h: Handler) -> None:
    u = _need_user(h)
    try:
        e = store(h).create(u, _body(h))
    except ValueError as ex:
        raise HubHTTPError(400, str(ex)) from None
    h._json({"ok": True, "entry": store(h).view(e["id"], u)})


@route("POST", r"/api/notebook/auto", auth=True)
def _auto(h: Handler) -> None:
    try:
        e = store(h).auto(_body(h))
    except ValueError as ex:
        raise HubHTTPError(400, str(ex)) from None
    h._json({"ok": True, "id": e["id"], "url": f"/notebook#/e/{e['id']}"})


@route("GET", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})", auth="any")
def _view(h: Handler, eid: str) -> None:
    _get(h, eid)
    h._json({"ok": True, "entry": store(h).view(eid, _user(h))})


@route("PUT", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})", auth="any")
def _update(h: Handler, eid: str) -> None:
    u = _need_user(h)
    _get(h, eid)
    try:
        store(h).update(eid, u, _body(h))
    except PermissionError as ex:
        raise HubHTTPError(403, str(ex)) from None
    except Conflict as ex:
        h._json({"ok": False, "error": str(ex), "conflict": True, "entry": ex.current}, 409)
        return
    except ValueError as ex:
        raise HubHTTPError(400, str(ex)) from None
    h._json({"ok": True, "entry": store(h).view(eid, u)})


@route("DELETE", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})", auth="any")
def _delete(h: Handler, eid: str) -> None:
    u = _need_user(h)
    _get(h, eid)
    try:
        store(h).delete(eid, u)
    except PermissionError as ex:
        raise HubHTTPError(403, str(ex)) from None
    h._json({"ok": True})


@route("GET", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/history", auth="any")
def _history(h: Handler, eid: str) -> None:
    _get(h, eid)
    h._json({"ok": True, "history": store(h).history(eid)})


@route("GET", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/history/([0-9]+)", auth="any")
def _version(h: Handler, eid: str, rev: str) -> None:
    _get(h, eid)
    try:
        h._json({"ok": True, "version": store(h).version(eid, int(rev))})
    except KeyError:
        raise HubHTTPError(404, "沒有這個版本") from None


@route("POST", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/comments", auth="any")
def _comment(h: Handler, eid: str) -> None:
    u = _need_user(h)
    _get(h, eid)
    try:
        c = store(h).comment(eid, u, str(_body(h).get("text") or ""))
    except ValueError as ex:
        raise HubHTTPError(400, str(ex)) from None
    h._json({"ok": True, "comment": c})


@route("DELETE", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/comments/([0-9a-f]{8})", auth="any")
def _del_comment(h: Handler, eid: str, cid: str) -> None:
    u = _need_user(h)
    _get(h, eid)
    try:
        store(h).delete_comment(eid, cid, u)
    except KeyError:
        raise HubHTTPError(404, "沒有這則留言") from None
    except PermissionError as ex:
        raise HubHTTPError(403, str(ex)) from None
    h._json({"ok": True})


@route("PUT", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/editing", auth="any")
def _editing(h: Handler, eid: str) -> None:
    u = _need_user(h)
    _get(h, eid)
    store(h).touch_editing(eid, u, bool(_body(h).get("on", True)))
    h._json({"ok": True})


@route("POST", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/files", auth="any")
def _upload(h: Handler, eid: str) -> None:
    u = _need_user(h)
    _get(h, eid)
    name = h._query().get("name") or "file"
    try:
        info = store(h).add_file(eid, u, name, h._body_bytes(MAX_FILE + 1))
    except PermissionError as ex:
        raise HubHTTPError(403, str(ex)) from None
    except ValueError as ex:
        raise HubHTTPError(413, str(ex)) from None
    h._json({"ok": True, "file": info})


@route("GET", r"/api/notebook/([0-9]{8}-[0-9a-f]{6})/files/([^/]+)", auth="any")
def _file(h: Handler, eid: str, name: str) -> None:
    import urllib.parse

    _get(h, eid)
    try:
        p = store(h).file_path(eid, name)
    except KeyError:
        raise HubHTTPError(404, "沒有這個附檔") from None
    data = p.read_bytes()
    ext = p.suffix.lower()
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ext not in INLINE:
        ctype = "application/octet-stream"                     # svg / html 之類一律下載，不在網頁裡打開
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("Cache-Control", "private, max-age=86400")
    disp = "inline" if ext in INLINE else "attachment"
    h.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{urllib.parse.quote(p.name)}")
    h.end_headers()
    h.wfile.write(data)


@route("GET", r"/notebook/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "notebook.html", "notebook")
