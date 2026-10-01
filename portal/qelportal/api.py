"""API v1（協定說明見 docs/PROTOCOL.md）。"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import PROTOCOL, __version__
from .app import Portal
from .httpd import HTTPError, Request, Response, make_router
from .modules import BUILTIN, BY_ID, ID_RE, VERSION_RE, inspect_release, latest, parse_version, sha256_file
from .paperlib import PaperlibError
from .store import loads

routes, route = make_router()
V1 = r"/api/v1"
DATA_MODULES = ("labcontrol", "lablogviewer")

CATEGORIES = [("Project", "專案", True), ("Level", "階段", True), ("Board Design", "板子設計", False),
              ("Data Analysis", "數據分析", False), ("Measurement", "量測", False), ("Paper Topic", "論文主題", False),
              ("Other", "其他", False)]
CATEGORY_KEYS = [c[0] for c in CATEGORIES]
DEFAULT_TAGS = {
    "Project": ("LRCPAEP", "BIC", "CM", "RSMEP"), "Level": ("LA", "LR"), "Board Design": ("Mirror",),
    "Data Analysis": ("Flux", "BG", "De-background"), "Measurement": ("VNA Sweep",),
    "Other": ("Good Data", "Best Data", "Debug", "singleYIG", "doubleYIG"),
}
TAG_RE = re.compile(r"\s+")
COLOR_RE = re.compile(r"^(#[0-9a-fA-F]{3,8})?$")


def seed(app: Portal) -> None:
    if app.store.one("SELECT 1 FROM meta WHERE key='seeded'"):
        return
    now = time.time()
    with app.store.tx() as c:
        for i, (cat, names) in enumerate(DEFAULT_TAGS.items()):
            for j, n in enumerate(names):
                c.execute("INSERT OR IGNORE INTO tags(name, category, sort, created_by, created_at, updated_at) "
                          "VALUES(?,?,?,?,?,?)", (n, cat, i * 100 + j, "system", now, now))
        c.execute("INSERT INTO meta(key, value) VALUES('seeded', '1')")


def tag_name(v: Any) -> str:
    if not isinstance(v, str):
        return ""
    n = TAG_RE.sub(" ", v.strip())
    return n[:60]


def user(req: Request) -> Dict[str, Any]:
    return req.session["user"]  # type: ignore[index]


# ============================================================================ 基本
@route("GET", V1 + r"/ping", auth="public")
def ping(app: Portal, req: Request):
    return {"ok": True, "server": "qel-portal", "version": __version__, "protocol": PROTOCOL, "time": time.time(),
            "site": app.cfg.site_name}


@route("GET", V1 + r"/site", auth="public", optional_user=True)
def site(app: Portal, req: Request):
    s = req.session
    return {"name": app.cfg.site_name, "version": __version__, "protocol": PROTOCOL,
            "paperlib_url": paperlib_public(app, req),
            "user": app.user_view(s["user"]) if s else None,
            "categories": [{"key": k, "name": n, "single": one} for k, n, one in CATEGORIES]}


def paperlib_public(app: Portal, req: Request) -> str:
    if app.cfg.paperlib_public_url:
        return app.cfg.paperlib_public_url
    stored = app.store.setting("paperlib_public_url", "")
    if stored:
        return str(stored).rstrip("/")
    try:
        su = (app.paperlib.site() or {}).get("site_url") or ""
        if su:
            return su.rstrip("/")
    except PaperlibError:
        pass
    u = urllib.parse.urlsplit(req.host_base)
    return f"{u.scheme}://{u.hostname}:8080"


# ============================================================================ 登入
@route("POST", V1 + r"/auth/login", auth="public")
def login(app: Portal, req: Request):
    b = req.json()
    client = str(b.get("client") or "web")
    r = app.login(req, str(b.get("username") or ""), str(b.get("password") or ""), str(b.get("code") or ""), client)
    if not r.get("ok"):
        return r
    if client.startswith("web"):                       # 網頁：token 只放在 HttpOnly cookie
        resp = Response.json({"ok": True, "user": r["user"]})
        return app.session_cookies(resp, req, r["token"], r["pl_cookie"])
    return {"ok": True, "token": r["token"], "user": r["user"]}


@route("POST", V1 + r"/auth/logout")
def logout(app: Portal, req: Request):
    app.logout(req.session)
    return app.clear_cookies(Response.json({"ok": True}))


@route("POST", V1 + r"/auth/register", auth="public")
def register(app: Portal, req: Request):
    b = req.json()
    body = {k: str(b.get(k) or "") for k in ("username", "password", "display_name", "email", "note", "website")}
    try:
        app.paperlib.register(body, req.ip)
    except PaperlibError as e:
        raise HTTPError(e.code if 400 <= e.code < 500 else 502, str(e)) from None
    app.store.audit(body["username"], "register", body["email"], req.ip)
    return {"ok": True, "message": "已送出申請，站長審核通過後會寄信通知你"}


@route("POST", V1 + r"/sso/ticket")
def sso_ticket(app: Portal, req: Request):
    t = app.new_ticket(req.session)
    return {"ticket": t, "url": f"/sso?ticket={urllib.parse.quote(t)}"}


@route("GET", r"/sso", auth="public")
def sso(app: Portal, req: Request):
    r = app.redeem_ticket(req.query.get("ticket", ""))
    if r is None:
        return Response.redirect("/#/login")
    nxt = req.query.get("next", "/")
    pl = paperlib_public(app, req)
    if not (nxt.startswith("/") and not nxt.startswith("//")) and not nxt.startswith(pl + "/"):
        nxt = "/"
    return app.session_cookies(Response.redirect(nxt), req, r["token"], r["pl_cookie"])


@route("GET", V1 + r"/me")
def me(app: Portal, req: Request):
    u = user(req)
    return dict(app.user_view(u), access=app.access_map(u["username"]), paperlib_perms=u.get("pl_perms", {}))


# ============================================================================ 模塊與發佈
def _releases(app: Portal, mid: str, include_withdrawn: bool = False) -> List[Dict[str, Any]]:
    rows = app.store.q("SELECT * FROM releases WHERE module=?" + ("" if include_withdrawn else " AND withdrawn=0"),
                       (mid,))
    out = [{"version": r["version"], "size": r["size"], "sha256": r["sha256"], "notes": r["notes"],
            "uploaded_by": r["uploaded_by"], "uploaded_at": r["uploaded_at"], "withdrawn": bool(r["withdrawn"]),
            "manifest": loads(r["manifest"], {})} for r in rows]
    return sorted(out, key=lambda r: parse_version(r["version"]), reverse=True)


def _release_access(app: Portal, req: Request, mid: str) -> None:
    if mid not in BY_ID:
        raise HTTPError(404, f"沒有模塊 {mid}")
    if not app.can(user(req), mid):
        raise HTTPError(403, f"站長還沒有開放「{BY_ID[mid]['name']}」給你")


@route("GET", V1 + r"/modules")
def modules(app: Portal, req: Request):
    u = user(req)
    out = []
    for m in BUILTIN:
        if m["access"] == "owner" and not u["manager"]:
            continue
        rels = _releases(app, m["id"])
        out.append(dict(m, allowed=app.can(u, m["id"]), latest=rels[0]["version"] if rels else None,
                        latest_notes=rels[0]["notes"] if rels else "",
                        url=paperlib_public(app, req) if m["id"] == "paperlib" else ""))
    return {"modules": out}


@route("GET", V1 + r"/modules/([a-z0-9_-]+)/releases")
def releases(app: Portal, req: Request, mid: str):
    _release_access(app, req, mid)
    return {"module": mid, "releases": _releases(app, mid, include_withdrawn=user(req)["manager"])}


@route("GET", V1 + r"/modules/([a-z0-9_-]+)/releases/([^/]+)\.zip")
def release_zip(app: Portal, req: Request, mid: str, ver: str):
    _release_access(app, req, mid)
    r = app.store.one("SELECT file FROM releases WHERE module=? AND version=?", (mid, ver))
    p = app.cfg.data_dir / "releases" / mid / (r["file"] if r else "-")
    if r is None or not p.is_file():
        raise HTTPError(404, f"沒有 {mid} v{ver}")
    return Response.download(p, f"{mid}_v{ver}.zip", "application/zip")


@route("PUT", V1 + r"/modules/([a-z0-9_-]+)/releases/([^/]+)", auth="owner")
def publish_release(app: Portal, req: Request, mid: str, ver: str):
    if not ID_RE.match(mid) or mid not in BY_ID:
        raise HTTPError(404, f"沒有模塊 {mid}")
    if not VERSION_RE.match(ver):
        raise HTTPError(400, "版本號格式不對（例如 1.0.3）")
    if app.store.one("SELECT 1 FROM releases WHERE module=? AND version=?", (mid, ver)):
        raise HTTPError(409, f"{mid} v{ver} 已經發佈過；已發佈的版本不能覆寫，請用新的版本號")
    d = app.cfg.data_dir / "releases" / mid
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(d), suffix=".part")
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        size = req.save_body(tmp_p, app.cfg.max_release_mb << 20)
        try:
            man = inspect_release(tmp_p, mid, ver)
        except ValueError as e:
            raise HTTPError(400, str(e)) from None
        name = f"{mid}_v{ver}.zip"
        os.replace(tmp_p, d / name)
    finally:
        if tmp_p.exists():
            tmp_p.unlink()
    notes = req.query.get("notes", "")[:4000]
    with app.store.tx() as c:
        c.execute("INSERT INTO releases(module, version, file, size, sha256, notes, manifest, uploaded_by, uploaded_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (mid, ver, name, size, sha256_file(d / name), notes,
                                                json.dumps(man, ensure_ascii=False), user(req)["username"], time.time()))
    app.store.audit(user(req)["username"], "release", f"{mid} v{ver}", req.ip)
    app.publish("modules.changed", {"module": mid, "version": ver}, user(req)["username"])
    return {"ok": True, "module": mid, "version": ver, "size": size}


@route("DELETE", V1 + r"/modules/([a-z0-9_-]+)/releases/([^/]+)", auth="owner")
def withdraw_release(app: Portal, req: Request, mid: str, ver: str):
    with app.store.tx() as c:
        n = c.execute("UPDATE releases SET withdrawn=1 WHERE module=? AND version=?", (mid, ver)).rowcount
    if not n:
        raise HTTPError(404, "沒有這個版本")
    app.store.audit(user(req)["username"], "withdraw", f"{mid} v{ver}", req.ip)
    app.publish("modules.changed", {"module": mid, "version": ver, "withdrawn": True}, user(req)["username"])
    return {"ok": True}


# ============================================================================ 標籤
def _pl_cookie(req: Request) -> str:
    return req.session.get("pl_cookie", "") if req.session else ""


def _tag_rows(app: Portal) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in app.store.q("SELECT * FROM tags ORDER BY sort, name COLLATE NOCASE"):
        out[r["name"].casefold()] = {"name": r["name"], "category": r["category"], "color": r["color"],
                                     "description": r["description"], "aliases": loads(r["aliases"], []),
                                     "created_by": r["created_by"], "papers": [], "paper_count": 0,
                                     "datasets": 0, "source": "portal"}
    for r in app.store.q("SELECT tag, paper_id FROM tag_papers ORDER BY added_at"):
        t = out.get(r["tag"].casefold())
        if t is not None:
            t["papers"].append(r["paper_id"])
    for r in app.store.q("SELECT dt.tag, COUNT(*) c FROM dataset_tags dt JOIN datasets d ON d.id=dt.dataset_id "
                         "WHERE d.deleted=0 GROUP BY dt.tag COLLATE NOCASE"):
        key = r["tag"].casefold()
        if key not in out:
            out[key] = {"name": r["tag"], "category": "Other", "color": "", "description": "", "aliases": [],
                        "created_by": "", "papers": [], "paper_count": 0, "datasets": 0, "source": "dataset"}
        out[key]["datasets"] = r["c"]
    return out


@route("GET", V1 + r"/tags")
def tags(app: Portal, req: Request):
    out = _tag_rows(app)
    if app.can(user(req), "paperlib"):
        try:
            for t in app.paperlib.tags(_pl_cookie(req)):
                key = str(t.get("name", "")).casefold()
                if not key:
                    continue
                if key not in out:
                    out[key] = {"name": t["name"], "category": "Paper Topic", "color": t.get("color") or "",
                                "description": t.get("description") or "", "aliases": [], "created_by": "",
                                "papers": [], "paper_count": 0, "datasets": 0, "source": "paperlib"}
                out[key]["paperlib_count"] = int(t.get("count") or 0)
        except PaperlibError:
            pass
    for t in out.values():
        t["paper_count"] = len(t["papers"]) + int(t.get("paperlib_count") or 0)
    order = {k: i for i, k in enumerate(CATEGORY_KEYS)}
    lst = sorted(out.values(), key=lambda t: (order.get(t["category"], 99), t["name"].casefold()))
    return {"categories": [{"key": k, "name": n, "single": one} for k, n, one in CATEGORIES], "tags": lst,
            "updated": app.store.setting("tags_updated", 0)}


def _tags_changed(app: Portal, req: Request, what: Dict[str, Any]) -> None:
    app.store.set_setting("tags_updated", time.time())
    app.publish("tags.changed", what, user(req)["username"])


@route("POST", V1 + r"/tags")
def save_tag(app: Portal, req: Request):
    b = req.json()
    u = user(req)
    name = tag_name(b.get("name"))
    if not name:
        raise HTTPError(400, "請輸入標籤名稱")
    cat = b.get("category")
    if cat is not None and cat not in CATEGORY_KEYS:
        raise HTTPError(400, f"分類只能是：{'、'.join(CATEGORY_KEYS)}")
    color = b.get("color")
    if color is not None and not COLOR_RE.match(str(color)):
        raise HTTPError(400, "顏色格式例如 #1c6dd0")
    aliases = b.get("aliases")
    if aliases is not None:
        aliases = [tag_name(a) for a in aliases if tag_name(a)][:20] if isinstance(aliases, list) else None
    now = time.time()
    row = app.store.one("SELECT * FROM tags WHERE name=?", (name,))
    with app.store.tx() as c:
        if row is None:
            c.execute("INSERT INTO tags(name, category, color, description, aliases, created_by, created_at, updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (name, cat or "Other", color or "", str(b.get("description") or "")[:500],
                                                  json.dumps(aliases or [], ensure_ascii=False), u["username"], now, now))
            created = True
        else:
            if not (u["manager"] or row["created_by"] == u["username"]):
                if any(v is not None for v in (cat, color, b.get("description"), aliases)):
                    raise HTTPError(403, "只有站長或建立的人可以修改這個標籤")
            c.execute("UPDATE tags SET category=COALESCE(?, category), color=COALESCE(?, color), "
                      "description=COALESCE(?, description), aliases=COALESCE(?, aliases), updated_at=? WHERE name=?",
                      (cat, color, None if b.get("description") is None else str(b["description"])[:500],
                       None if aliases is None else json.dumps(aliases, ensure_ascii=False), now, name))
            created = False
    _tags_changed(app, req, {"tag": name, "created": created})
    return {"ok": True, "name": row["name"] if row else name, "created": created}


@route("DELETE", V1 + r"/tags/([^/]+)", auth="owner")
def delete_tag(app: Portal, req: Request, name: str):
    name = urllib.parse.unquote(name)
    with app.store.tx() as c:
        c.execute("DELETE FROM tag_papers WHERE tag=?", (name,))
        n = c.execute("DELETE FROM tags WHERE name=?", (name,)).rowcount
    if not n:
        raise HTTPError(404, "沒有這個標籤")
    app.store.audit(user(req)["username"], "tag.delete", name, req.ip)
    _tags_changed(app, req, {"tag": name, "deleted": True})
    return {"ok": True}


@route("POST", V1 + r"/tags/([^/]+)/rename", auth="owner")
def rename_tag(app: Portal, req: Request, name: str):
    name = urllib.parse.unquote(name)
    new = tag_name(req.json().get("name"))
    if not new:
        raise HTTPError(400, "請輸入新名稱")
    with app.store.tx() as c:
        if c.execute("SELECT 1 FROM tags WHERE name=? AND name<>?", (new, name)).fetchone():
            raise HTTPError(409, f"已經有標籤「{new}」")
        if not c.execute("UPDATE tags SET name=?, updated_at=? WHERE name=?", (new, time.time(), name)).rowcount:
            raise HTTPError(404, "沒有這個標籤")
        for r in c.execute("SELECT dataset_id FROM dataset_tags WHERE tag=?", (name,)).fetchall():
            c.execute("INSERT OR IGNORE INTO dataset_tags(dataset_id, tag) VALUES(?,?)", (r["dataset_id"], new))
        c.execute("DELETE FROM dataset_tags WHERE tag=? AND tag<>?", (name, new))
        old_aliases = loads(c.execute("SELECT aliases FROM tags WHERE name=?", (new,)).fetchone()["aliases"], [])
        if name.casefold() != new.casefold() and name not in old_aliases:
            c.execute("UPDATE tags SET aliases=? WHERE name=?", (json.dumps([*old_aliases, name], ensure_ascii=False), new))
    app.store.audit(user(req)["username"], "tag.rename", f"{name} → {new}", req.ip)
    _tags_changed(app, req, {"tag": new, "renamed_from": name})
    return {"ok": True, "name": new}


def _paper_url(app: Portal, req: Request, pid: int) -> str:
    return f"{paperlib_public(app, req)}/#/p/{int(pid)}"


def _papers_for_tag(app: Portal, req: Request, tag: str, limit: int = 50) -> List[Dict[str, Any]]:
    cookie = _pl_cookie(req)
    seen: Dict[int, Dict[str, Any]] = {}
    explicit = [r["paper_id"] for r in app.store.q("SELECT paper_id FROM tag_papers WHERE tag=? ORDER BY added_at",
                                                    (tag,))]
    for pid in explicit:
        p = app.paperlib.paper(cookie, pid)
        if p:
            seen[pid] = dict(p, linked=True)
    try:
        for p in app.paperlib.papers_with_tag(cookie, tag, limit):
            if p and p["id"] not in seen:
                seen[p["id"]] = dict(p, linked=False)
    except PaperlibError:
        pass
    out = list(seen.values())[:limit]
    for p in out:
        p["url"] = _paper_url(app, req, p["id"])
    return out


@route("GET", V1 + r"/tags/([^/]+)/papers", module="paperlib")
def tag_papers(app: Portal, req: Request, name: str):
    name = urllib.parse.unquote(name)
    return {"tag": name, "papers": _papers_for_tag(app, req, name, req.qint("limit", 50, 1, 200)),
            "tag_url": f"{paperlib_public(app, req)}/#/t/{urllib.parse.quote(name)}"}


def _ensure_tag(c, name: str, by: str) -> None:
    now = time.time()
    c.execute("INSERT OR IGNORE INTO tags(name, category, created_by, created_at, updated_at) VALUES(?,?,?,?,?)",
              (name, "Other", by, now, now))


@route("PUT", V1 + r"/tags/([^/]+)/papers", module="paperlib")
def set_tag_papers(app: Portal, req: Request, name: str):
    name = tag_name(urllib.parse.unquote(name))
    ids = req.json().get("paper_ids")
    if not isinstance(ids, list):
        raise HTTPError(400, "需要 paper_ids")
    ids = list(dict.fromkeys(int(i) for i in ids))[:500]
    u = user(req)
    with app.store.tx() as c:
        _ensure_tag(c, name, u["username"])
        c.execute("DELETE FROM tag_papers WHERE tag=?", (name,))
        for pid in ids:
            c.execute("INSERT INTO tag_papers(tag, paper_id, added_by, added_at) VALUES(?,?,?,?)",
                      (name, pid, u["username"], time.time()))
    _tags_changed(app, req, {"tag": name, "papers": ids})
    return {"ok": True, "tag": name, "papers": ids}


@route("POST", V1 + r"/tags/([^/]+)/papers", module="paperlib")
def add_tag_paper(app: Portal, req: Request, name: str):
    name = tag_name(urllib.parse.unquote(name))
    try:
        pid = int(req.json().get("paper_id"))
    except (TypeError, ValueError):
        raise HTTPError(400, "需要 paper_id") from None
    if app.paperlib.paper(_pl_cookie(req), pid) is None:
        raise HTTPError(404, "論文庫沒有這篇論文")
    with app.store.tx() as c:
        _ensure_tag(c, name, user(req)["username"])
        c.execute("INSERT OR IGNORE INTO tag_papers(tag, paper_id, added_by, added_at) VALUES(?,?,?,?)",
                  (name, pid, user(req)["username"], time.time()))
    _tags_changed(app, req, {"tag": name, "added_paper": pid})
    return {"ok": True}


@route("DELETE", V1 + r"/tags/([^/]+)/papers/(\d+)", module="paperlib")
def remove_tag_paper(app: Portal, req: Request, name: str, pid: str):
    name = urllib.parse.unquote(name)
    with app.store.tx() as c:
        c.execute("DELETE FROM tag_papers WHERE tag=? AND paper_id=?", (name, int(pid)))
    _tags_changed(app, req, {"tag": name, "removed_paper": int(pid)})
    return {"ok": True}


# ============================================================================ 數據
def _dataset_view(app: Portal, r, full: bool = False) -> Dict[str, Any]:
    tags_ = [t["tag"] for t in app.store.q("SELECT tag FROM dataset_tags WHERE dataset_id=? ORDER BY rowid", (r["id"],))]
    d = {"id": r["id"], "name": r["name"], "path": r["path"], "fingerprint": r["fingerprint"],
         "hub_file": {"node": r["hub_node"], "rel": r["hub_rel"]} if r["hub_node"] else None,
         "source": loads(r["source"], {}), "tags": tags_, "has_scheme": bool(r["scheme"]),
         "created_by": r["created_by"], "created_at": r["created_at"], "updated_at": r["updated_at"],
         "downloadable": bool(r["hub_node"]) or bool(_mapped_path(app, r["path"]))}
    if full:
        d["meta"] = loads(r["meta"], {})
        d["scheme"] = loads(r["scheme"], None)
    return d


def _set_dataset_tags(c, dsid: int, names: List[str], by: str) -> List[str]:
    clean = list(dict.fromkeys(n for n in (tag_name(x) for x in names) if n))[:50]
    c.execute("DELETE FROM dataset_tags WHERE dataset_id=?", (dsid,))
    for n in clean:
        row = c.execute("SELECT name FROM tags WHERE name=?", (n,)).fetchone()
        if row is None:                            # 別名 → 正式名稱
            for t in c.execute("SELECT name, aliases FROM tags WHERE aliases<>'[]'").fetchall():
                if any(a.casefold() == n.casefold() for a in loads(t["aliases"], [])):
                    row = t
                    break
        if row is None:
            _ensure_tag(c, n, by)
            name = n
        else:
            name = row["name"]
        c.execute("INSERT OR IGNORE INTO dataset_tags(dataset_id, tag) VALUES(?,?)", (dsid, name))
    return [r["tag"] for r in c.execute("SELECT tag FROM dataset_tags WHERE dataset_id=? ORDER BY rowid", (dsid,)).fetchall()]


@route("POST", V1 + r"/datasets", module=DATA_MODULES)
def register_dataset(app: Portal, req: Request):
    b = req.json()
    u = user(req)
    name = str(b.get("name") or "").strip()[:300]
    path = str(b.get("path") or "").strip()[:2000]
    fp = str(b.get("fingerprint") or "").strip()[:100]
    if not name:
        name = Path(path.replace("\\", "/")).name if path else ""
    if not name:
        raise HTTPError(400, "需要 name 或 path")
    hub = b.get("hub_file") if isinstance(b.get("hub_file"), dict) else {}
    scheme = b.get("scheme")
    if scheme is not None and not isinstance(scheme, dict):
        raise HTTPError(400, "scheme 必須是物件")
    now = time.time()
    with app.store.tx() as c:
        row = None
        if fp:
            row = c.execute("SELECT * FROM datasets WHERE fingerprint=? AND deleted=0", (fp,)).fetchone()
        if row is None and path:
            row = c.execute("SELECT * FROM datasets WHERE path=? AND deleted=0", (path,)).fetchone()
        args = (name, path, fp, str(hub.get("node") or "")[:100], str(hub.get("rel") or "")[:1000],
                json.dumps(scheme, ensure_ascii=False) if scheme is not None else None,
                json.dumps(b.get("source") or {}, ensure_ascii=False), json.dumps(b.get("meta") or {}, ensure_ascii=False))
        if row is None:
            dsid = c.execute("INSERT INTO datasets(name, path, fingerprint, hub_node, hub_rel, scheme, source, meta, "
                             "created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             args + (u["username"], now, now)).lastrowid
            created = True
        else:
            dsid = row["id"]
            c.execute("UPDATE datasets SET name=?, path=COALESCE(NULLIF(?, ''), path), "
                      "fingerprint=COALESCE(NULLIF(?, ''), fingerprint), hub_node=COALESCE(NULLIF(?, ''), hub_node), "
                      "hub_rel=COALESCE(NULLIF(?, ''), hub_rel), scheme=COALESCE(?, scheme), source=?, meta=?, "
                      "updated_at=? WHERE id=?", args + (now, dsid))
            created = False
        if "tags" in b and isinstance(b["tags"], list):
            _set_dataset_tags(c, dsid, b["tags"], u["username"])
    r = app.store.one("SELECT * FROM datasets WHERE id=?", (dsid,))
    view = _dataset_view(app, r)
    app.publish("dataset.created" if created else "dataset.updated", view, u["username"])
    return dict(view, ok=True, created=created)


@route("GET", V1 + r"/datasets", module=DATA_MODULES + ("paperlib",))
def list_datasets(app: Portal, req: Request):
    where, args = ["d.deleted=0"], []
    tag = req.query.get("tag", "").strip()
    if tag:
        where.append("EXISTS(SELECT 1 FROM dataset_tags t WHERE t.dataset_id=d.id AND t.tag=?)")
        args.append(tag)
    q = req.query.get("q", "").strip()
    if q:
        where.append("(d.name LIKE ? OR d.path LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if req.query.get("mine") == "1":
        where.append("d.created_by=?")
        args.append(user(req)["username"])
    total = app.store.one(f"SELECT COUNT(*) n FROM datasets d WHERE {' AND '.join(where)}", tuple(args))["n"]
    rows = app.store.q(f"SELECT * FROM datasets d WHERE {' AND '.join(where)} ORDER BY d.created_at DESC LIMIT ? OFFSET ?",
                       tuple(args) + (req.qint("limit", 100, 1, 500), req.qint("offset", 0)))
    return {"total": total, "items": [_dataset_view(app, r) for r in rows]}


@route("GET", V1 + r"/datasets/lookup", module=DATA_MODULES)
def lookup_dataset(app: Portal, req: Request):
    fp, path = req.query.get("fingerprint", ""), req.query.get("path", "")
    r = None
    if fp:
        r = app.store.one("SELECT * FROM datasets WHERE fingerprint=? AND deleted=0", (fp,))
    if r is None and path:
        r = app.store.one("SELECT * FROM datasets WHERE path=? AND deleted=0", (path,))
    return {"dataset": _dataset_view(app, r, full=True) if r else None}


def _dataset_or_404(app: Portal, dsid: str):
    r = app.store.one("SELECT * FROM datasets WHERE id=? AND deleted=0", (int(dsid),))
    if r is None:
        raise HTTPError(404, "沒有這筆數據")
    return r


@route("GET", V1 + r"/datasets/(\d+)", module=DATA_MODULES + ("paperlib",))
def get_dataset(app: Portal, req: Request, dsid: str):
    return _dataset_view(app, _dataset_or_404(app, dsid), full=True)


@route("PATCH", V1 + r"/datasets/(\d+)", module=DATA_MODULES)
def edit_dataset(app: Portal, req: Request, dsid: str):
    r = _dataset_or_404(app, dsid)
    b = req.json()
    u = user(req)
    with app.store.tx() as c:
        if isinstance(b.get("tags"), list):
            _set_dataset_tags(c, r["id"], b["tags"], u["username"])
        if isinstance(b.get("name"), str) and b["name"].strip():
            c.execute("UPDATE datasets SET name=? WHERE id=?", (b["name"].strip()[:300], r["id"]))
        c.execute("UPDATE datasets SET updated_at=? WHERE id=?", (time.time(), r["id"]))
    view = _dataset_view(app, _dataset_or_404(app, dsid))
    app.publish("dataset.updated", view, u["username"])
    return view


@route("DELETE", V1 + r"/datasets/(\d+)", module=DATA_MODULES)
def delete_dataset(app: Portal, req: Request, dsid: str):
    r = _dataset_or_404(app, dsid)
    u = user(req)
    if not (u["manager"] or r["created_by"] == u["username"]):
        raise HTTPError(403, "只有站長或登錄的人可以移除（數據檔本身不會被刪除）")
    with app.store.tx() as c:
        c.execute("UPDATE datasets SET deleted=1, updated_at=? WHERE id=?", (time.time(), r["id"]))
    app.store.audit(u["username"], "dataset.remove", f"{r['id']} {r['name']}", req.ip)
    return {"ok": True}


@route("GET", V1 + r"/datasets/(\d+)/scheme", module=DATA_MODULES)
def dataset_scheme(app: Portal, req: Request, dsid: str):
    r = _dataset_or_404(app, dsid)
    return {"id": r["id"], "scheme": loads(r["scheme"], None)}


@route("GET", V1 + r"/datasets/(\d+)/papers", module=DATA_MODULES + ("paperlib",))
def dataset_papers(app: Portal, req: Request, dsid: str):
    view = _dataset_view(app, _dataset_or_404(app, dsid))
    return {"id": view["id"], "groups": _groups(app, req, view["tags"])}


def _groups(app: Portal, req: Request, tags_: List[str]) -> List[Dict[str, Any]]:
    can = app.can(user(req), "paperlib")
    pl = paperlib_public(app, req)
    out = []
    for t in tags_:
        g = {"tag": t, "tag_url": f"{pl}/#/t/{urllib.parse.quote(t)}", "papers": []}
        if can:
            g["papers"] = _papers_for_tag(app, req, t, 20)
        else:
            g["no_access"] = True
        out.append(g)
    return out


def _mapped_path(app: Portal, path: str) -> Optional[Path]:
    if not path:
        return None
    norm = path.replace("\\", "/")
    for prefix, mount in app.cfg.data_roots.items():
        pre = prefix.replace("\\", "/").rstrip("/")
        if norm.casefold().startswith(pre.casefold() + "/"):
            rel = norm[len(pre) + 1:]
            base = Path(mount).resolve()
            p = (base / rel).resolve()
            if base in p.parents and p.is_file():
                return p
    return None


@route("GET", V1 + r"/datasets/(\d+)/file", module=DATA_MODULES)
def dataset_file(app: Portal, req: Request, dsid: str):
    r = _dataset_or_404(app, dsid)
    p = _mapped_path(app, r["path"])
    if p is not None:
        return Response.download(p)
    if r["hub_node"]:
        path = f"/api/files/{urllib.parse.quote(r['hub_node'], safe='')}/{urllib.parse.quote(r['hub_rel'])}"
        return _hub_forward(app, req, "GET", path, b"", timeout=600,
                            download_name=Path(r["hub_rel"]).name or r["name"])
    raise HTTPError(404, "這筆數據只存在量測電腦上（沒有上傳到 NAS），請在那台電腦開啟")


# ============================================================================ 論文
@route("GET", V1 + r"/papers", module="paperlib")
def papers(app: Portal, req: Request):
    try:
        r = app.paperlib.papers(_pl_cookie(req), tag=req.query.get("tag", ""), q=req.query.get("q", ""),
                                limit=req.qint("limit", 30, 1, 200))
    except PaperlibError as e:
        raise HTTPError(502 if e.code >= 500 else e.code, str(e)) from None
    from .paperlib import slim
    items = [dict(p, url=_paper_url(app, req, p["id"])) for p in (slim(x) for x in r.get("items", [])) if p]
    return {"total": r.get("total", len(items)), "items": items}


@route("POST", V1 + r"/papers/by-tags", module=DATA_MODULES + ("paperlib",))
def papers_by_tags(app: Portal, req: Request):
    t = req.json().get("tags")
    if not isinstance(t, list):
        raise HTTPError(400, "需要 tags")
    names = list(dict.fromkeys(n for n in (tag_name(x) for x in t) if n))[:30]
    return {"groups": _groups(app, req, names)}


@route("GET", V1 + r"/papers/(\d+)/link")
def paper_link(app: Portal, req: Request, pid: str):
    return {"url": _paper_url(app, req, int(pid))}


# ============================================================================ 事件
@route("GET", V1 + r"/events")
def events(app: Portal, req: Request):
    topics = [t for t in req.query.get("topics", "").split(",") if t]
    u = user(req)
    r = app.events_after(u["username"], req.qint("after", -1, -1), req.qfloat("wait", 25, 0, 55), topics)
    if not any(app.can(u, m) for m in DATA_MODULES + ("paperlib",)):      # 數據事件只給看得到數據的人
        r["events"] = [e for e in r["events"] if not e["topic"].startswith("dataset.")]
    return r


@route("POST", V1 + r"/events")
def post_event(app: Portal, req: Request):
    b = req.json()
    topic = str(b.get("topic") or "")
    if not re.match(r"^app\.[a-z0-9_.-]{1,60}$", topic):
        raise HTTPError(400, "模塊自訂事件的主題必須以 app. 開頭（例如 app.labcontrol.started）")
    seq = app.publish(topic, b.get("data"), user(req)["username"])
    return {"ok": True, "seq": seq}


@route("POST", V1 + r"/handoff")
def handoff(app: Portal, req: Request):
    b = req.json()
    mod = str(b.get("module") or "")
    if mod not in BY_ID:
        raise HTTPError(400, f"沒有模塊 {mod}")
    if not app.can(user(req), mod):
        raise HTTPError(403, f"站長還沒有開放「{BY_ID[mod]['name']}」給你")
    seq = app.publish("handoff", {"module": mod, "action": str(b.get("action") or ""),
                                  "payload": b.get("payload") or {}}, user(req)["username"],
                      target=user(req)["username"])
    return {"ok": True, "seq": seq}


# ============================================================================ 量測中繼（Hub）
def _hub_forward(app: Portal, req: Request, method: str, path: str, body: bytes, timeout: float = 60,
                 download_name: str = "") -> Response:
    headers = {"Accept": req.headers.get("Accept", "application/json")}
    if app.cfg.hub_token:
        headers["Authorization"] = f"Bearer {app.cfg.hub_token}"
    if body:
        headers["Content-Type"] = req.headers.get("Content-Type", "application/json")
    hreq = urllib.request.Request(app.cfg.hub_url + path, data=body or None, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        r = opener.open(hreq, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return Response(raw, e.code, e.headers.get("Content-Type", "application/json"))
    except (urllib.error.URLError, OSError) as e:
        raise HTTPError(502, f"連不到量測中繼站（Lab Control Hub）：{getattr(e, 'reason', e)}") from None

    def stream():
        try:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                yield chunk
        finally:
            r.close()
    length = r.headers.get("Content-Length")
    hdrs = []
    if download_name:
        hdrs.append(("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(download_name)}"))
    elif r.headers.get("Content-Disposition"):
        hdrs.append(("Content-Disposition", r.headers["Content-Disposition"]))
    return Response(status=r.status, ctype=r.headers.get("Content-Type", "application/octet-stream"),
                    stream=stream(), length=int(length) if length else None, headers=hdrs)


@route("GET", V1 + r"/relay/(.+)", module="labcontrol")
def relay_get(app: Portal, req: Request, rest: str):
    return _relay(app, req, rest)


@route("POST", V1 + r"/relay/(.+)", module="labcontrol")
def relay_post(app: Portal, req: Request, rest: str):
    return _relay(app, req, rest)


@route("PUT", V1 + r"/relay/(.+)", module="labcontrol")
def relay_put(app: Portal, req: Request, rest: str):
    return _relay(app, req, rest)


@route("DELETE", V1 + r"/relay/(.+)", module="labcontrol")
def relay_delete(app: Portal, req: Request, rest: str):
    return _relay(app, req, rest)


def _relay(app: Portal, req: Request, rest: str) -> Response:
    if ".." in rest.split("/") or rest.startswith("hub/"):      # Hub 自我更新只能由更新代理做
        raise HTTPError(403, "這個 Hub 功能只能從監控程式操作")
    body = req.body(limit=256 * 1024 * 1024) if req.method in ("POST", "PUT") else b""
    if req.method in ("POST", "PUT") and rest.startswith("nodes/") and rest.endswith("/commands"):
        try:                                                    # 指令標上真正的使用者
            msg = json.loads(body.decode("utf-8") or "{}")
            msg.setdefault("from", {})
            if isinstance(msg["from"], dict):
                msg["from"]["user"] = user(req)["display_name"]
                msg["from"]["portal_user"] = user(req)["username"]
            body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        except ValueError:
            pass
    wait = req.qfloat("wait", 0, 0, 120)
    path = "/api/" + rest + (("?" + req.raw_query) if req.raw_query else "")
    return _hub_forward(app, req, req.method, path, body, timeout=max(60.0, wait + 30))


# ============================================================================ 狀態
def _probe(url: str, timeout: float = 4.0) -> Dict[str, Any]:
    t0 = time.time()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
        return {"online": True, "ms": round((time.time() - t0) * 1000, 1), "info": data}
    except Exception as e:  # noqa: BLE001
        return {"online": False, "ms": None, "error": str(getattr(e, "reason", e))}


@route("GET", V1 + r"/health")
def health(app: Portal, req: Request):
    pl = _probe(app.cfg.paperlib_url + "/api/site")
    hub = _probe(app.cfg.hub_url + "/api/ping")
    out: Dict[str, Any] = {
        "time": time.time(),
        "portal": {"online": True, "version": __version__, "uptime_s": round(time.time() - app.started),
                   "protocol": PROTOCOL},
        "paperlib": {"online": pl["online"], "ms": pl["ms"], "version": (pl.get("info") or {}).get("version"),
                     "error": pl.get("error")},
        "labhub": {"online": hub["online"], "ms": hub["ms"], "version": (hub.get("info") or {}).get("version"),
                   "error": hub.get("error")},
    }
    if user(req)["manager"]:
        if app.cfg.agent_url:
            ag = _probe(app.cfg.agent_url + "/api/ping")
            out["agent"] = {"online": ag["online"], "version": (ag.get("info") or {}).get("version"),
                            "url": app.cfg.agent_url, "error": ag.get("error")}
        out["traffic"] = app.traffic(60)
        out["recent_errors"] = list(app.errors)[-20:]
        db = app.cfg.data_dir / "portal.db"
        du = shutil.disk_usage(str(app.cfg.data_dir))
        out["portal"].update(db_mb=round(db.stat().st_size / 1e6, 2) if db.exists() else 0,
                             disk_free_gb=round(du.free / 1e9, 1), disk_total_gb=round(du.total / 1e9, 1),
                             sessions=app.store.one("SELECT COUNT(*) n FROM sessions WHERE expires>?",
                                                    (time.time(),))["n"],
                             datasets=app.store.one("SELECT COUNT(*) n FROM datasets WHERE deleted=0")["n"],
                             tags=app.store.one("SELECT COUNT(*) n FROM tags")["n"])
        if pl["online"]:
            try:
                out["paperlib"]["status"] = app.paperlib.admin_status(_pl_cookie(req))
            except PaperlibError as e:
                out["paperlib"]["status_error"] = str(e)
        if hub["online"]:
            try:
                out["labhub"]["nodes"] = _hub_state(app)
            except Exception as e:  # noqa: BLE001
                out["labhub"]["state_error"] = str(e)
    return out


def _hub_state(app: Portal) -> Any:
    headers = {"Authorization": f"Bearer {app.cfg.hub_token}"} if app.cfg.hub_token else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(app.cfg.hub_url + "/api/state", headers=headers), timeout=5) as r:
        st = json.loads(r.read().decode("utf-8"))
    nodes = st.get("nodes") or [] if isinstance(st, dict) else []
    devices = st.get("devices") or [] if isinstance(st, dict) else []
    return {"nodes": len(nodes), "nodes_online": sum(1 for n in nodes if n.get("online")),
            "devices": len(devices), "devices_online": sum(1 for d in devices if d.get("online")),
            "latest": st.get("latest") if isinstance(st, dict) else None}


# ============================================================================ 站長管理
@route("GET", V1 + r"/admin/users", auth="owner")
def admin_users(app: Portal, req: Request):
    try:
        pl_users = app.paperlib.users(_pl_cookie(req))
    except PaperlibError as e:
        raise HTTPError(502, f"讀不到論文庫的使用者：{e}") from None
    last = {r["username"].casefold(): r["m"] for r in
            app.store.q("SELECT username, MAX(last_seen) m FROM sessions GROUP BY username COLLATE NOCASE")}
    flags = {r["username"].casefold(): dict(r) for r in app.store.q("SELECT * FROM user_flags")}
    out = []
    for u in pl_users:
        name = u.get("username")
        if not name:
            continue
        f = flags.get(name.casefold(), {})
        is_mgr = bool(u.get("owner"))
        out.append({"username": name, "display_name": u.get("display_name") or name, "role": u.get("role"),
                    "owner": is_mgr, "disabled_in_paperlib": bool(u.get("disabled")), "email": u.get("email") or "",
                    "blocked": bool(f.get("blocked")), "note": f.get("note", ""),
                    "access": {m: True for m in app.access_map(name)} if is_mgr else app.access_map(name),
                    "last_seen": last.get(name.casefold())})
    return {"users": out, "modules": [{"id": m["id"], "name": m["name"], "default": app.default_access()[m["id"]]}
                                      for m in BUILTIN if m["access"] is True],
            "paperlib_admin_url": f"{paperlib_public(app, req)}/#/admin/users"}


@route("PUT", V1 + r"/admin/users/([^/]+)", auth="owner")
def admin_set_user(app: Portal, req: Request, name: str):
    name = urllib.parse.unquote(name)
    b = req.json()
    me_ = user(req)
    now = time.time()
    changes = []
    with app.store.tx() as c:
        acc = b.get("access")
        if isinstance(acc, dict):
            for m, v in acc.items():
                if m not in BY_ID or BY_ID[m]["access"] is not True:
                    raise HTTPError(400, f"不能設定模塊 {m}")
                c.execute("INSERT INTO access(username, module, enabled, updated_by, updated_at) VALUES(?,?,?,?,?) "
                          "ON CONFLICT(username, module) DO UPDATE SET enabled=excluded.enabled, "
                          "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                          (name, m, 1 if v else 0, me_["username"], now))
                changes.append(f"{m}={'開' if v else '關'}")
        if "blocked" in b or "note" in b:
            if b.get("blocked") and name.casefold() == me_["username"].casefold():
                raise HTTPError(400, "不能停用自己")
            row = c.execute("SELECT * FROM user_flags WHERE username=?", (name,)).fetchone()
            blocked = bool(b["blocked"]) if "blocked" in b else bool(row and row["blocked"])
            note = str(b.get("note", row["note"] if row else ""))[:300]
            c.execute("INSERT INTO user_flags(username, blocked, note, updated_by, updated_at) VALUES(?,?,?,?,?) "
                      "ON CONFLICT(username) DO UPDATE SET blocked=excluded.blocked, note=excluded.note, "
                      "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                      (name, 1 if blocked else 0, note, me_["username"], now))
            if "blocked" in b:
                changes.append("停用大程式" if blocked else "恢復大程式")
                if blocked:
                    c.execute("DELETE FROM sessions WHERE username=?", (name,))
    app.store.audit(me_["username"], "access", f"{name}：{'、'.join(changes)}", req.ip)
    app.publish("access.changed", {"username": name}, me_["username"], target=name)
    return {"ok": True, "access": app.access_map(name), "blocked": app.blocked(name)}


@route("GET", V1 + r"/admin/settings", auth="owner")
def admin_settings(app: Portal, req: Request):
    return {"default_access": app.default_access(), "paperlib_public_url": app.store.setting("paperlib_public_url", ""),
            "effective_paperlib_url": paperlib_public(app, req),
            "env": {"paperlib_url": app.cfg.paperlib_url, "hub_url": app.cfg.hub_url,
                    "hub_token_set": bool(app.cfg.hub_token), "agent_url": app.cfg.agent_url, "sso": app.cfg.sso,
                    "cookie_domain": app.cfg.cookie_domain, "data_roots": app.cfg.data_roots,
                    "session_days": app.cfg.session_days}}


@route("PUT", V1 + r"/admin/settings", auth="owner")
def admin_save_settings(app: Portal, req: Request):
    b = req.json()
    if isinstance(b.get("default_access"), dict):
        app.store.set_setting("default_access", {m: bool(v) for m, v in b["default_access"].items()
                                                 if m in BY_ID and BY_ID[m]["access"] is True})
    if "paperlib_public_url" in b:
        u = str(b["paperlib_public_url"] or "").strip().rstrip("/")
        if u and not re.match(r"^https?://", u):
            raise HTTPError(400, "網址要以 http:// 或 https:// 開頭")
        app.store.set_setting("paperlib_public_url", u)
    app.store.audit(user(req)["username"], "settings", json.dumps(b, ensure_ascii=False)[:500], req.ip)
    return admin_settings(app, req)


@route("GET", V1 + r"/admin/audit", auth="owner")
def admin_audit(app: Portal, req: Request):
    rows = app.store.q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (req.qint("limit", 200, 1, 2000),))
    return {"items": [dict(r) for r in rows]}


@route("GET", V1 + r"/admin/sessions", auth="owner")
def admin_sessions(app: Portal, req: Request):
    rows = app.store.q("SELECT token_hash, username, client, ip, created, last_seen, expires FROM sessions "
                       "WHERE expires>? ORDER BY last_seen DESC", (time.time(),))
    return {"items": [dict(dict(r), id=r["token_hash"][:16]) for r in rows]}


@route("DELETE", V1 + r"/admin/sessions/([0-9a-f]{16})", auth="owner")
def admin_kick(app: Portal, req: Request, sid: str):
    with app.store.tx() as c:
        n = c.execute("DELETE FROM sessions WHERE substr(token_hash, 1, 16)=?", (sid,)).rowcount
    app.store.audit(user(req)["username"], "kick", sid, req.ip)
    return {"ok": True, "removed": n}


@route("GET", V1 + r"/admin/traffic", auth="owner")
def admin_traffic(app: Portal, req: Request):
    return app.traffic(req.qint("minutes", 60, 5, 1440))
