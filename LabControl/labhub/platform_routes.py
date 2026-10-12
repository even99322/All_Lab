"""量子工程平台 API（掛在 Hub 上；所有模塊共用的通信中樞）。

登入 / 使用者::

    POST /api/platform/login            {username, password, code?, client?} → {token:"u_…", user, modules, …}
                                        （用論文庫 paperlib 的帳號；body 有 "cookie": true 時同時設定網頁 cookie）
    POST /api/platform/logout
    GET  /api/platform/me               → {kind, user, modules, paperlib_url, hub}
    GET  /api/platform/status           → 論文庫連線、數據目錄數量、各模塊最新版

模塊（啟動器用來單獨安裝 / 更新）::

    GET  /api/platform/modules                         → [{id, name, kind, url, latest, versions, allowed}]
    PUT  /api/platform/modules/<id>                    ← 站長：修改名稱、說明、網址、入口…
    PUT  /api/platform/modules/<id>/<版本>/<檔名>?notes= ← 站長：上傳新版
    GET  /api/platform/modules/<id>/<版本>/<檔名>        → 下載（需要該模塊權限）

站長::

    GET   /api/platform/users                → 論文庫的使用者 ＋ 各模塊權限
    PATCH /api/platform/users/<uid>          {enabled}            停用 / 啟用帳號（寫回論文庫）
    PUT   /api/platform/perms                {username, module, allowed}
    GET   /api/platform/audit, /api/platform/sessions

標籤與論文（所有模塊共用同一批標籤）::

    GET   /api/platform/tags                 → [{name, category, color, papers, data, url, in_paperlib}]
    POST  /api/platform/tags                 {name, category?, color?, description?}
    PATCH /api/platform/tags/<name>          {category?, color?, description?}
    GET   /api/platform/papers?tags=a,b      → 每個標籤對應的論文（含跳到論文庫的連結）

數據目錄::

    GET  /api/platform/catalog?tags=&mode=and|or&q=&node=&user=&limit=&offset=
    GET  /api/platform/catalog/<id>          → 項目（含下載網址、標籤連結）
    PUT  /api/platform/catalog/<id>/tags     {tags}
    POST /api/platform/catalog/register      {node, rel, size?, tags?, run_id?, scheme?, meta?}（只登記、不上傳）
    GET  /api/platform/catalog/<id>/papers   → 這筆數據的標籤對應的論文

模塊之間的訊息（同一位使用者的各模塊、各裝置互傳指令）::

    POST /api/platform/bus/<模塊>             {type, …} → {id}
    GET  /api/platform/bus/<模塊>?after=&wait= → {messages, seq}
"""
from __future__ import annotations

import json
import time
import urllib.parse

from . import __version__
from .server import STATIC, Handler, HubHTTPError, _cookie, parse_version, route

P = r"/api/platform"


def _plat(h: Handler):
    return h.hub.platform


def _tags_arg(v: str) -> list:
    return [t for t in (x.strip() for x in (v or "").split(",")) if t]


# ---- 入口網頁（PWA）------------------------------------------------------------------
@route("GET", r"/portal", auth=False)
def _portal(h: Handler) -> None:
    if not h.authorized():
        h._redirect("/login?next=/portal")
        return
    h._html((STATIC / "portal.html").read_text(encoding="utf-8"))


@route("GET", r"/manifest\.webmanifest", auth=False)
def _manifest(h: Handler) -> None:
    h._send(200, (STATIC / "manifest.webmanifest").read_bytes(), "application/manifest+json")


@route("GET", r"/sw\.js", auth=False)
def _sw(h: Handler) -> None:
    h._send(200, (STATIC / "sw.js").read_bytes(), "text/javascript; charset=utf-8",
            {"Service-Worker-Allowed": "/"})


@route("GET", r"/icon\.svg", auth=False)
def _icon(h: Handler) -> None:
    h._send(200, (STATIC / "icon.svg").read_bytes(), "image/svg+xml")


# ---- 登入 ---------------------------------------------------------------------------
@route("POST", P + r"/login", auth=False)
def _login(h: Handler) -> None:
    b = h._json_body()
    res = _plat(h).login(str(b.get("username") or ""), str(b.get("password") or ""), str(b.get("code") or ""),
                         h.ip, str(b.get("client") or h.headers.get("User-Agent", ""))[:80])
    headers = {"Set-Cookie": _cookie(res["token"], 30 * 86400)} if b.get("cookie") else None
    body = dict(res, ok=True, hub=__version__)
    h._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8", headers)


@route("POST", P + r"/logout", auth="user")
def _logout(h: Handler) -> None:
    _plat(h).logout(h.principal()["token"])
    h._json({"ok": True})


@route("GET", P + r"/me", auth="any")
def _me(h: Handler) -> None:
    p = h.principal()
    plat = _plat(h)
    if p["kind"] == "user":
        u = p["user"]
        mods = plat.modules_for(u)
    else:
        u = {"username": "", "display_name": "量測電腦（裝置 token）", "owner": False}
        mods = [dict(m, allowed=not m.get("owner_only")) for m in plat.modules().values()]
    h._json({"kind": p["kind"], "user": u, "modules": mods, "paperlib_url": plat.paperlib_public,
             "paperlib": plat.paperlib is not None, "hub": __version__,
             "projects_share": h.hub.cfg.projects_share})


@route("GET", P + r"/status", auth="any")
def _status(h: Handler) -> None:
    st = _plat(h).status()
    st.update(hub=h.hub.hub_info() if h.check("admin") is None else {"version": __version__}, time=time.time())
    snap = h.hub.snapshot()
    st["nodes"] = [{"name": n.get("name"), "online": n.get("online"), "state": n.get("state")}
                   for n in snap.get("nodes", [])]
    st["devices"] = sum(1 for d in snap.get("devices", []) if d.get("online"))
    h._json(st)


# ---- 模塊 ----------------------------------------------------------------------------
@route("GET", P + r"/modules", auth="any")
def _modules(h: Handler) -> None:
    p = h.principal()
    plat = _plat(h)
    if p["kind"] == "user":
        h._json({"modules": plat.modules_for(p["user"])})
    else:
        h._json({"modules": [dict(m, allowed=True) for m in plat.modules().values()]})


@route("PUT", P + r"/modules/([^/]+)", auth="owner")
def _set_module(h: Handler, mid: str) -> None:
    b = h._json_body()
    if not isinstance(b, dict):
        raise HubHTTPError(400, "需要 JSON 物件")
    m = _plat(h).set_module(mid, b)
    _plat(h).audit(h.user().get("username", ""), "module", f"{mid} {sorted(b)}")
    h._json({"ok": True, "module": m})


@route("PUT", P + r"/modules/([^/]+)/([^/]+)/([^/]+)", auth="owner")
def _upload_release(h: Handler, mid: str, version: str, filename: str) -> None:
    data = h._body_bytes(h.hub.cfg.max_upload)
    res = _plat(h).add_release(mid, version, filename, data, h.user().get("username", ""),
                               h._query().get("notes", ""))
    h._json(dict(res, ok=True))


@route("GET", P + r"/modules/([^/]+)/([^/]+)/([^/]+)", auth="any")
def _download_release(h: Handler, mid: str, version: str, filename: str) -> None:
    err = h.check(f"module:{mid}")
    if err:
        raise HubHTTPError(403, err)
    h._send_file(_plat(h).release_file(mid, version, filename))


# ---- 站長：使用者與權限 ------------------------------------------------------------------
@route("GET", P + r"/users", auth="owner")
def _users(h: Handler) -> None:
    plat = _plat(h)
    h._json({"users": plat.users(h.session()),
             "modules": [{"id": k, "name": v.get("name")} for k, v in plat.modules().items()
                         if not v.get("owner_only")]})


@route("PATCH", P + r"/users/(\d+)", auth="owner")
def _set_user(h: Handler, uid: str) -> None:
    b = h._json_body()
    if "enabled" not in b:
        raise HubHTTPError(400, "需要 enabled")
    if int(uid) == h.user().get("id") and not b["enabled"]:
        raise HubHTTPError(400, "不能停用自己")
    h._json(_plat(h).set_user_enabled(h.session(), int(uid), bool(b["enabled"])))


@route("PUT", P + r"/perms", auth="owner")
def _set_perm(h: Handler) -> None:
    b = h._json_body()
    if not b.get("username") or not b.get("module"):
        raise HubHTTPError(400, "需要 username 與 module")
    _plat(h).set_perm(h.user().get("username", ""), str(b["username"]), str(b["module"]), bool(b.get("allowed")))
    h._json({"ok": True})


@route("GET", P + r"/audit", auth="owner")
def _audit(h: Handler) -> None:
    h._json({"audit": _plat(h).recent_audit(int(h._qf("limit", 100)))})


@route("GET", P + r"/sessions", auth="owner")
def _sessions(h: Handler) -> None:
    h._json({"sessions": _plat(h).active_sessions()})


# ---- 標籤與論文 ------------------------------------------------------------------------
@route("GET", P + r"/tags", auth="any")
def _tags(h: Handler) -> None:
    h._json({"tags": _plat(h).tags(h.session()), "paperlib_url": _plat(h).paperlib_public})


@route("POST", P + r"/tags", auth="any")
def _create_tag(h: Handler) -> None:
    b = h._json_body()
    res = _plat(h).create_tag(h.session(), str(b.get("name") or ""), str(b.get("category") or ""),
                              str(b.get("color") or ""), str(b.get("description") or ""))
    _plat(h).audit(h.user().get("username", "") or f"device@{h.ip}", "tag", res["name"])
    h._json(dict(res, ok=True))


@route("PATCH", P + r"/tags/(.+)", auth="any")
def _patch_tag(h: Handler, name: str) -> None:
    b = h._json_body()
    _plat(h).set_tag_meta(name, **{k: b.get(k) for k in ("category", "color", "description")})
    h._json({"ok": True})


@route("GET", P + r"/papers", auth="any")
def _papers(h: Handler) -> None:
    q = h._query()
    h._json(_plat(h).papers_for_tags(h.session(), _tags_arg(q.get("tags", "")), int(q.get("limit", 50))))


# ---- 數據目錄 ---------------------------------------------------------------------------
@route("GET", P + r"/catalog", auth="any")
def _catalog(h: Handler) -> None:
    q = h._query()
    h._json(_plat(h).catalog(_tags_arg(q.get("tags", "")), q.get("q", ""), q.get("node", ""), q.get("user", ""),
                             q.get("mode", "and"), int(q.get("limit", 100)), int(q.get("offset", 0)),
                             q.get("project", "")))


@route("POST", P + r"/catalog/register", auth=True)
def _register(h: Handler) -> None:
    b = h._json_body()
    if not b.get("node") or not b.get("rel"):
        raise HubHTTPError(400, "需要 node 與 rel")
    item = _plat(h).register(str(b["node"]), str(b["rel"]), int(b.get("size") or 0),
                             h.user().get("username") or str(b.get("user") or ""), str(b.get("run_id") or ""),
                             str(b.get("scheme") or ""), list(b.get("tags") or []), dict(b.get("meta") or {}),
                             str(b.get("project") or ""))
    h._json(dict(item, ok=True))


@route("GET", P + r"/catalog/([0-9a-f]{16})", auth="any")
def _catalog_item(h: Handler, cid: str) -> None:
    h._json(_plat(h).catalog_item(cid))


@route("PUT", P + r"/catalog/([0-9a-f]{16})/tags", auth="any")
def _catalog_tags(h: Handler, cid: str) -> None:
    b = h._json_body()
    tags = b.get("tags")
    if not isinstance(tags, list):
        raise HubHTTPError(400, "需要 tags（清單）")
    h._json(_plat(h).set_catalog_tags(cid, [str(t) for t in tags], h.user().get("username", "") or h.ip))


@route("GET", P + r"/catalog/([0-9a-f]{16})/papers", auth="any")
def _catalog_papers(h: Handler, cid: str) -> None:
    item = _plat(h).catalog_item(cid)
    h._json(_plat(h).papers_for_tags(h.session(), item["tags"]))


@route("PUT", P + r"/catalog/([0-9a-f]{16})/project", auth="any")
def _catalog_project(h: Handler, cid: str) -> None:
    b = h._json_body()
    h._json(_plat(h).set_catalog_project(cid, str(b.get("project") or ""), h.user().get("username", "") or h.ip))


# ---- 專案（論文庫的專案專區 ＋ 平台的數據、數據標籤、專案資料夾）--------------------------------------
@route("GET", P + r"/projects", auth="any")
def _projects(h: Handler) -> None:
    h._json({"projects": _plat(h).projects(h.session(), force=h._query().get("refresh") == "1"),
             "share": h.hub.cfg.projects_share})


@route("POST", P + r"/projects", auth="user")
def _create_project(h: Handler) -> None:
    b = h._json_body()
    h._json(dict(_plat(h).create_project(h.session(), str(b.get("name") or ""), str(b.get("short_name") or ""),
                                         str(b.get("description") or ""), str(b.get("color") or "")), ok=True))


@route("GET", P + r"/projects/([^/]+)", auth="any")
def _project(h: Handler, pid: str) -> None:
    h._json(_plat(h).project_detail(h.session(), pid))


@route("PUT", P + r"/projects/([^/]+)/tags", auth="any")
def _project_tags(h: Handler, pid: str) -> None:
    b = h._json_body()
    tags = b.get("tags")
    if not isinstance(tags, list):
        raise HubHTTPError(400, "需要 tags（清單）")
    h._json({"ok": True, "tags": _plat(h).set_project_tags(pid, [str(t) for t in tags], bool(b.get("replace")),
                                                             h.user().get("username", "") or h.ip)})


@route("GET", P + r"/projects/([^/]+)/files", auth="any")
def _project_files(h: Handler, pid: str) -> None:
    plat = _plat(h)
    name = plat.project_name(pid)
    h._json({"project": pid, "folder": plat.folder_name(name, pid), "files": plat.project_files(pid)})


# ---- 模塊之間的訊息 -----------------------------------------------------------------------
def _bus_user(h: Handler) -> str:
    u = h.user().get("username")
    return u or "_device"


@route("POST", P + r"/bus/([a-z0-9_-]+)", auth="any")
def _bus_send(h: Handler, module: str) -> None:
    b = h._json_body()
    if not isinstance(b, dict) or not b.get("type"):
        raise HubHTTPError(400, "訊息需要 type")
    b["from"] = {"user": _bus_user(h), "ip": h.ip, "module": str(b.get("from_module") or "")}
    m = _plat(h).bus.send(_bus_user(h), module, b)
    h._json({"ok": True, "id": m["id"]})


@route("GET", P + r"/bus/([a-z0-9_-]+)", auth="any")
def _bus_take(h: Handler, module: str) -> None:
    q = h._query()
    h._json(_plat(h).bus.take(_bus_user(h), module, int(float(q.get("after", 0))), h._qf("wait")))


# ---- 站長控制台（0.0.18）：上傳檔案 → 發佈；系統狀態；量測電腦 ------------------------------------
C = P + r"/console"


def _by(h: Handler) -> str:
    return h.user().get("username", "") or h.ip


@route("GET", C + r"/items", auth="owner")
def _console_items(h: Handler) -> None:
    h._json({"items": h.hub.console.items()})


@route("POST", C + r"/stage", auth="owner")
def _console_stage(h: Handler) -> None:
    data = h._body_bytes(h.hub.cfg.max_upload)
    q = h._query()
    h._json(dict(h.hub.console.stage(q.get("filename", ""), data, _by(h), q.get("version", "")), ok=True))


@route("POST", C + r"/publish/([0-9a-f]+)", auth="owner")
def _console_publish(h: Handler, sid: str) -> None:
    b = h._json_body() if int(h.headers.get("Content-Length") or 0) else {}
    notes = b.get("notes") if isinstance(b, dict) else None
    h._json(dict(h.hub.console.publish(sid, _by(h), notes), ok=True))


@route("GET", C + r"/jobs/([0-9a-f]+)", auth="owner")
def _console_job(h: Handler, sid: str) -> None:
    q = h._query()
    h._json(h.hub.console.job(sid, int(float(q.get("after", 0))), min(h._qf("wait"), 20.0)))


@route("DELETE", C + r"/items/([0-9a-f]+)", auth="owner")
def _console_discard(h: Handler, sid: str) -> None:
    h.hub.console.discard(sid)
    h._json({"ok": True})


@route("POST", C + r"/clear", auth="owner")
def _console_clear(h: Handler) -> None:
    h.hub.console.clear_done()
    h._json({"ok": True})


@route("GET", C + r"/status", auth="owner")
def _console_status(h: Handler) -> None:
    h._json(h.hub.console.status())


@route("POST", C + r"/measure-pc", auth="owner")
def _console_measure_pc(h: Handler) -> None:
    """站長把某台電腦設成量測電腦：給啟動器裝置 token（長期有效、不跟著個人登入過期）。"""
    b = h._json_body()
    import re as _re

    name = str((b or {}).get("name") or "").strip()
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}", name):
        raise HubHTTPError(400, "量測電腦名稱請用英文、數字、- 或 _（例如 LAB-A）：會用在資料夾名稱與網址")
    if h.hub.cfg.token is None:
        raise HubHTTPError(400, "這個 Hub 沒有設定裝置 token")
    _plat(h).audit(_by(h), "measure-pc", f"{name} @ {h.ip}")
    h._json({"ok": True, "name": name, "token": h.hub.cfg.token})


# ---- 下載頁（不用登入）：每個人只需要記這個網址 -------------------------------------------------
@route("GET", r"/get", auth=False)
def _get_page(h: Handler) -> None:
    h._html((STATIC / "get.html").read_text(encoding="utf-8"))


@route("GET", r"/get/info", auth=False)
def _get_info(h: Handler) -> None:
    """下載頁與程式內版本檢查：量測（Lab Control）、讀檔（LabLogViewer）的安裝檔與功能更新包（見 appupdates.py）。"""
    from .appupdates import all_apps

    from .appupdates import setup_info

    h._json({"name": "量子工程平台", "apps": all_apps(_plat(h)), "downloads": [], "zip": [],
             "setup": setup_info(_plat(h)), "paperlib": _plat(h).paperlib_public, "hub": __version__})


@route("GET", r"/get/setup\.txt", auth=False)
def _get_setup_plan(h: Handler) -> None:
    """Windows 一鍵安裝程式（QELSetup）讀的清單：量測、讀檔最新的安裝程式（見 appupdates.setup_plan）。"""
    from .appupdates import setup_plan

    q = h._query()
    body = setup_plan(_plat(h), q.get("platform", "windows"), q.get("arch", "x86_64"),
                      pets=q.get("pets", "") in ("1", "true", "yes")).encode("utf-8")
    h.send_response(200)
    h.send_header("Content-Type", "text/plain; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


@route("GET", r"/get/mac(?:\.sh)?", auth=False)
def _get_mac_script(h: Handler) -> None:
    """Mac 安裝腳本：curl -fsSL http://<NAS>:8765/get/mac | bash（用 curl 下載，不會「已損毀」）。"""
    from .appupdates import mac_script

    host = h.headers.get("Host") or f"{h.server.server_address[0]}:{h.server.server_address[1]}"
    body = mac_script(f"http://{host}").encode("utf-8")
    h.send_response(200)
    h.send_header("Content-Type", "text/x-shellscript; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


@route("GET", r"/get/setup", auth=False)
def _get_setup(h: Handler) -> None:
    """好記的網址：http://<NAS>:8765/get/setup → 最新的 Windows 一鍵安裝程式。"""
    from .appupdates import setup_info

    f = setup_info(_plat(h))
    if f is None:
        raise HubHTTPError(404, "站長還沒有上傳 Windows 一鍵安裝程式（QELSetup_v*_windows.exe）")
    h.send_response(302)
    h.send_header("Location", f["url"])
    h.send_header("Content-Length", "0")
    h.end_headers()


@route("GET", r"/get/watch", auth=False)
def _get_watch(h: Handler) -> None:
    """程式內推播（長輪詢）：?app=labcontrol&version=0.0.26&wait=60 → Hub 一發佈更新的版本就回應。"""
    from .appupdates import watch
    from .platform import APPS

    q = h._query()
    app = q.get("app", "")
    if app not in APPS:
        raise HubHTTPError(404, "沒有這個程式")
    try:
        wait = float(q.get("wait", "30"))
    except ValueError:
        wait = 30.0
    h._json(watch(_plat(h), app, q.get("version", "0"), wait))


@route("GET", r"/get/feed/(lablogviewer)\.json", auth=False)
def _get_feed(h: Handler, mid: str) -> None:
    """LabLogViewer 的更新 feed（latest.json 格式＋安裝檔清單）。"""
    from .appupdates import lablogviewer_feed

    h._json(lablogviewer_feed(_plat(h), mid))


@route("GET", r"/get/(labcontrol|lablogviewer|setup|llvpets)/([0-9.]+)/([^/]+)", auth=False)
def _get_app_file(h: Handler, mid: str, version: str, filename: str) -> None:
    """量測 / 讀檔程式的安裝檔（.exe、.dmg…）與功能更新包（Python zip）：不用登入就能下載。"""
    from .appupdates import public_file

    name = urllib.parse.unquote(filename)
    if not public_file(name):
        raise HubHTTPError(404, "沒有這個檔案")
    h._send_file(_plat(h).release_file(mid, version, name))


@route("GET", r"/get/([0-9.]+)/([^/]+)", auth=False)
def _get_file(h: Handler, version: str, filename: str) -> None:
    """（相容）舊的啟動器下載網址。"""
    h._send_file(_plat(h).release_file("launcher", version, urllib.parse.unquote(filename)))
