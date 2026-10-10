"""量子工程平台：論文庫帳號登入、站長權限、共用標籤與論文、數據目錄、模塊訊息、模塊發佈。"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from labhub.server import Config, make_server
from tests import fake_paperlib

TOKEN = "dev-token"


class Client:
    def __init__(self, url: str, token: str = "") -> None:
        self.url, self.token = url, token
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body=None, raw: bytes = None, headers=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self.op.open(req, timeout=10) as r:
                b = r.read()
                return r.status, (json.loads(b) if b and r.headers.get_content_type() == "application/json" else b)
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b)
            except ValueError:
                return e.code, b

    def ok(self, method, path, body=None, **kw):
        st, obj = self.call(method, path, body, **kw)
        assert st == 200, (st, obj)
        return obj


@pytest.fixture
def plat(tmp_path):
    psrv, pstate, purl = fake_paperlib.start()
    cfg = Config(tmp_path / "hub", token=TOKEN, paperlib_url=purl, paperlib_public="https://papers.example")
    srv, state = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield url, state, pstate
    srv.shutdown()
    srv.server_close()
    psrv.shutdown()
    psrv.server_close()


def login(url, user, pw, code=""):
    c = Client(url)
    st, r = c.call("POST", "/api/platform/login", {"username": user, "password": pw, "code": code})
    if st != 200:
        return st, r
    c.token = r["token"]
    return c, r


def test_login_with_paperlib_account(plat):
    url, state, pstate = plat
    c, r = login(url, "alice", "alicepw")
    assert r["token"].startswith("u_") and r["user"]["username"] == "alice" and not r["user"]["owner"]
    assert r["paperlib_url"] == "https://papers.example"
    assert all(h.get("x-pl") == "1" for m, p, h in pstate.calls if m != "GET")      # paperlib 的 CSRF 標頭
    me = c.ok("GET", "/api/platform/me")
    assert me["kind"] == "user" and me["user"]["display_name"] == "Alice"
    mods = {m["id"]: m for m in me["modules"]}
    assert mods["labcontrol"]["allowed"] and not mods["monitor"]["allowed"]          # 平台監控只給站長
    assert mods["papers"]["url"] == "https://papers.example/"
    # 錯誤密碼 / 停用帳號 / 兩步驟
    assert login(url, "alice", "nope")[0] == 401
    st, r = login(url, "bob", "bobpw")
    assert st == 401 and "錯誤" in r["error"]
    st, r = login(url, "carol", "carolpw")
    assert st == 401 and r["error"] == "need_2fa"
    c3, r3 = login(url, "carol", "carolpw", "123456")
    assert r3["user"]["username"] == "carol"
    # 使用者 token 也能用量測 API（站長沒關掉量測模塊時）
    assert c.ok("GET", "/api/state")["server"] == "labhub"
    ping = Client(url, c.token).ok("GET", "/api/ping")
    assert ping["kind"] == "user" and ping["user"] == "alice" and ping["paperlib"]
    c.ok("POST", "/api/platform/logout")
    assert c.call("GET", "/api/platform/me")[0] == 401


def test_login_rate_limit(plat):
    url, *_ = plat
    for _ in range(5):
        assert login(url, "alice", "bad")[0] == 401
    st, r = login(url, "alice", "alicepw")
    assert st == 429 and "分鐘" in r["error"]


def test_owner_permissions_and_user_toggle(plat):
    url, state, pstate = plat
    alice, _ = login(url, "alice", "alicepw")
    owner, r = login(url, "admin", "adminpw")
    assert r["user"]["owner"]
    assert alice.call("GET", "/api/platform/users")[0] == 403          # 只有站長
    assert Client(url, TOKEN).call("GET", "/api/platform/users")[0] == 403   # 裝置 token 不是站長
    us = owner.ok("GET", "/api/platform/users")
    names = {u["username"]: u for u in us["users"]}
    assert names["bob"]["disabled"] and names["admin"]["owner"] and names["alice"]["modules"]["labcontrol"]
    assert "monitor" not in {m["id"] for m in us["modules"]}
    # 站長關掉 alice 的量測模塊 → alice 不能用量測 API，但仍可看數據目錄
    owner.ok("PUT", "/api/platform/perms", {"username": "alice", "module": "labcontrol", "allowed": False})
    owner.ok("PUT", "/api/platform/perms", {"username": "alice", "module": "nodes", "allowed": False})
    st, r = alice.call("GET", "/api/state")
    assert st == 403 and "站長" in r["error"]
    assert alice.ok("GET", "/api/platform/catalog")["total"] == 0
    assert not {m["id"]: m for m in alice.ok("GET", "/api/platform/me")["modules"]}["labcontrol"]["allowed"]
    owner.ok("PUT", "/api/platform/perms", {"username": "alice", "module": "labcontrol", "allowed": True})
    assert alice.call("GET", "/api/state")[0] == 200
    # Hub 更新 / 重啟：裝置 token 或站長；一般使用者不行
    assert alice.call("POST", "/api/hub/restart", {})[0] == 403
    # 停用帳號（寫回論文庫）→ 立刻登出
    owner.ok("PATCH", "/api/platform/users/2", {"enabled": False})
    assert pstate.users[2]["disabled"] == 1
    assert alice.call("GET", "/api/platform/me")[0] == 401
    assert owner.call("PATCH", "/api/platform/users/1", {"enabled": False})[0] == 400   # 不能停用自己
    owner.ok("PATCH", "/api/platform/users/2", {"enabled": True})
    assert login(url, "alice", "alicepw")[0].token
    actions = [a["action"] for a in owner.ok("GET", "/api/platform/audit")["audit"]]
    assert "perm" in actions and "user" in actions and "login" in actions


def test_session_rechecks_paperlib(plat, monkeypatch):
    url, state, pstate = plat
    alice, _ = login(url, "alice", "alicepw")
    pstate.users[2]["disabled"] = 1                     # 在論文庫那邊被停用
    import labhub.platform as P
    monkeypatch.setattr(P, "RECHECK_S", 0)
    assert alice.call("GET", "/api/platform/me")[0] == 401


def test_tags_papers_and_catalog(plat):
    url, state, pstate = plat
    alice, _ = login(url, "alice", "alicepw")
    owner, _ = login(url, "admin", "adminpw")
    dev = Client(url, TOKEN)
    tags = {t["name"]: t for t in alice.ok("GET", "/api/platform/tags")["tags"]}
    assert tags["transmon"]["papers"] == 2 and tags["transmon"]["url"] == "https://papers.example/#/t/transmon"
    # 成員沒有論文庫的 manage 權限 → 標籤只建在平台；站長 → 也建在論文庫；已存在 → 不會重複
    assert not alice.ok("POST", "/api/platform/tags", {"name": "sample-A", "category": "Project"})["in_paperlib"]
    assert owner.ok("POST", "/api/platform/tags", {"name": "fridge-2", "category": "Setup"})["in_paperlib"]
    assert owner.ok("POST", "/api/platform/tags", {"name": "JPA"})["in_paperlib"]
    assert sum(1 for t in pstate.tags if t["name"] == "JPA") == 1
    # 量測節點用裝置 token 上傳，帶標籤與量測資訊 → 登記在數據目錄
    meta = json.dumps({"VNA": "ZNA", "points": 201})
    up = dev.ok("PUT", "/api/files/LAB-1/2026/10/run1.hdf5?tags=transmon,sample-A&run=r1&scheme=02_2d&user=alice"
                       "&meta=" + urllib.request.quote(meta), raw=b"x" * 100)
    cid = up["catalog_id"]
    dev.ok("PUT", "/api/files/LAB-1/2026/10/run2.hdf5?tags=JPA", raw=b"y" * 10)
    cat = alice.ok("GET", "/api/platform/catalog?tags=transmon")
    assert cat["total"] == 1 and cat["items"][0]["user"] == "alice" and cat["items"][0]["meta"]["VNA"] == "ZNA"
    assert alice.ok("GET", "/api/platform/catalog?tags=transmon,JPA&mode=or")["total"] == 2
    assert alice.ok("GET", "/api/platform/catalog?tags=transmon,JPA")["total"] == 0
    assert alice.ok("GET", "/api/platform/catalog?q=run2")["total"] == 1
    item = alice.ok("GET", f"/api/platform/catalog/{cid}")
    assert item["exists"] and item["tag_urls"]["transmon"].endswith("#/t/transmon")
    st, data = alice.call("GET", item["url"])
    assert st == 200 and data == b"x" * 100
    # 這筆數據對應的論文（可以點擊跳到論文庫）
    ps = alice.ok("GET", f"/api/platform/catalog/{cid}/papers")
    assert {p["id"] for p in ps["papers"]} == {11, 12}
    assert ps["papers"][0]["url"].startswith("https://papers.example/#/p/")
    assert next(t for t in ps["tags"] if t["name"] == "sample-A")["papers"] == []
    # 改標籤 → 數量跟著變
    alice.ok("PUT", f"/api/platform/catalog/{cid}/tags", {"tags": ["transmon", "flux-tunable"]})
    tags = {t["name"]: t for t in alice.ok("GET", "/api/platform/tags")["tags"]}
    assert tags["flux-tunable"]["data"] == 1 and tags["sample-A"]["data"] == 0 and tags["sample-A"]["category"] == "Project"
    # 只登記（檔案留在量測電腦）
    reg = alice.ok("POST", "/api/platform/catalog/register", {"node": "PC-7", "rel": "local/a.hdf5", "tags": ["JPA"]})
    assert not reg["exists"] and reg["user"] == "alice"
    # 裝置 token 讀標籤：用最近一次的論文庫結果
    assert "transmon" in {t["name"] for t in dev.ok("GET", "/api/platform/tags")["tags"]}


def test_rescan_registers_old_uploads(tmp_path):
    cfg = Config(tmp_path / "hub", token=TOKEN)
    (cfg.data_dir / "files" / "OLD" / "x").mkdir(parents=True)
    (cfg.data_dir / "files" / "OLD" / "x" / "a.hdf5").write_bytes(b"1")
    srv, state = make_server(cfg, "127.0.0.1", 0)
    assert state.platform.rescan() == 1 and state.platform.rescan() == 0
    assert state.platform.catalog()["items"][0]["rel"] == "x/a.hdf5"
    srv.server_close()


def test_bus_between_modules(plat):
    url, *_ = plat
    alice, _ = login(url, "alice", "alicepw")
    alice2, _ = login(url, "alice", "alicepw")             # 同一個人的另一台裝置（手機網頁）
    owner, _ = login(url, "admin", "adminpw")
    got = {}

    def wait():
        got["r"] = alice.ok("GET", "/api/platform/bus/lablogviewer?after=0&wait=5")
    th = threading.Thread(target=wait)
    th.start()
    time.sleep(0.3)
    alice2.ok("POST", "/api/platform/bus/lablogviewer", {"type": "open", "catalog_id": "abc", "from_module": "portal"})
    th.join(6)
    msgs = got["r"]["messages"]
    assert msgs[0]["type"] == "open" and msgs[0]["from"]["user"] == "alice" and msgs[0]["from"]["module"] == "portal"
    assert owner.ok("GET", "/api/platform/bus/lablogviewer?wait=0")["messages"] == []      # 別人收不到
    assert alice.call("POST", "/api/platform/bus/labcontrol", {"no": "type"})[0] == 400


def test_module_releases(plat):
    url, state, _ = plat
    owner, _ = login(url, "admin", "adminpw")
    alice, _ = login(url, "alice", "alicepw")
    assert alice.call("PUT", "/api/platform/modules/labcontrol/0.0.13/LabControl_v0.0.13.zip", raw=b"z")[0] == 403
    assert owner.call("PUT", "/api/platform/modules/labcontrol/0.0.13a/x.zip", raw=b"z")[0] == 400   # 純數字版本
    owner.ok("PUT", "/api/platform/modules/labcontrol/0.0.13/LabControl_v0.0.13.zip?notes=hi", raw=b"zip13")
    owner.ok("PUT", "/api/platform/modules/labcontrol/0.0.9/LabControl_v0.0.9.zip", raw=b"zip9")
    m = {x["id"]: x for x in alice.ok("GET", "/api/platform/modules")["modules"]}["labcontrol"]
    assert m["latest"] == "0.0.13" and m["versions"][0]["notes"] == "hi" and m["allowed"]
    st, data = alice.call("GET", m["versions"][0]["files"][0]["url"])
    assert st == 200 and data == b"zip13"
    owner.ok("PUT", "/api/platform/perms", {"username": "alice", "module": "labcontrol", "allowed": False})
    assert alice.call("GET", m["versions"][0]["files"][0]["url"])[0] == 403
    owner.ok("PUT", "/api/platform/modules/labcontrol", {"name": "量測", "entry": "main.py"})
    assert {x["id"]: x for x in owner.ok("GET", "/api/platform/modules")["modules"]}["labcontrol"]["name"] == "量測"
    st = owner.ok("GET", "/api/platform/status")
    assert st["paperlib"]["online"] and st["paperlib"]["version"] == "9.9.9" and st["modules"]["labcontrol"] == "0.0.13"


def test_web_login_and_portal(plat):
    url, *_ = plat
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor())
    r = op.open(url + "/")                                   # 第一次來 → 下載頁（0.0.18）
    assert r.geturl().endswith("/get") and "量子工程平台" in r.read().decode()
    r = op.open(url + "/portal")
    assert "/login" in r.geturl() and "論文庫" in r.read().decode()
    try:
        op.open(url + "/login", data=b"username=alice&password=bad&next=/portal")
        raise AssertionError("應該失敗")
    except urllib.error.HTTPError as e:
        assert e.code == 401 and "錯誤" in e.read().decode()
    r = op.open(url + "/login", data=b"username=alice&password=alicepw&next=/portal")
    assert r.geturl().endswith("/portal") and "量子工程平台" in r.read().decode()
    me = json.loads(op.open(url + "/api/platform/me").read())
    assert me["user"]["username"] == "alice"
    assert "量測節點" in op.open(url + "/nodes").read().decode()
    # 兩步驟：先顯示驗證碼欄位
    op2 = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor())
    try:
        op2.open(url + "/login", data=b"username=carol&password=carolpw")
    except urllib.error.HTTPError as e:
        page = e.read().decode()
        assert "驗證碼" in page and 'value="carol"' in page
    for p in ("/manifest.webmanifest", "/sw.js", "/icon.svg"):
        assert op.open(url + p).status == 200
    op.open(url + "/logout")
    assert "/login" in op.open(url + "/portal").geturl()


def test_paperlib_url_from_platform_json(tmp_path, monkeypatch):
    for k in ("LABHUB_PAPERLIB_URL", "LABHUB_PAPERLIB_PUBLIC"):
        monkeypatch.delenv(k, raising=False)
    d = tmp_path / "d"
    d.mkdir()
    (d / "platform.json").write_text(json.dumps({"paperlib_url": "http://pl:8080/", "paperlib_public": "http://x"}))
    monkeypatch.setenv("LABHUB_DATA", str(d))
    cfg = Config.from_env(token="t")
    assert cfg.paperlib_url == "http://pl:8080" and cfg.paperlib_public == "http://x"
    monkeypatch.setenv("LABHUB_PAPERLIB_URL", "http://env:1")
    assert Config.from_env(token="t").paperlib_url == "http://env:1"


def test_paperlib_link_follows_hub_address(tmp_path):
    psrv, _, purl = fake_paperlib.start()
    cfg = Config(tmp_path / "h", token=TOKEN, paperlib_url=purl,
                 paperlib_public="http://192.168.50.2:8080,http://100.114.33.20:8080")
    srv, state = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    c, r = login(url, "alice", "alicepw")
    assert r["paperlib_url"] == "http://192.168.50.2:8080"                       # 不認得的主機 → 第一個
    for host, want in (("100.114.33.20:8765", "http://100.114.33.20:8080"), ("192.168.50.2", "http://192.168.50.2:8080")):
        t = c.ok("GET", "/api/platform/tags", headers={"Host": host})["tags"]
        assert next(x for x in t if x["name"] == "transmon")["url"] == want + "/#/t/transmon"
        ps = c.ok("GET", "/api/platform/papers?tags=transmon", headers={"Host": host})
        assert ps["papers"][0]["url"].startswith(want + "/#/p/")
    srv.shutdown()
    srv.server_close()
    psrv.shutdown()


def test_projects_tags_data_and_folders(plat):
    """論文庫的專案 → 平台：專案的數據標籤、論文、數據；每個專案一個資料夾（讀檔模塊直接開）。"""
    import os
    url, state, pstate = plat
    alice, _ = login(url, "alice", "alicepw")
    dev = Client(url, TOKEN)
    ps = {p["name"]: p for p in alice.ok("GET", "/api/platform/projects")["projects"]}
    assert set(ps) == {"YIG mirror coupling", "JPA readout"} and ps["YIG mirror coupling"]["data"] == 0
    mirror = ps["YIG mirror coupling"]["id"]
    # 量測上傳時帶專案 → 登記、標籤自動加入專案、專案資料夾出現（硬連結）
    up = dev.ok("PUT", f"/api/files/LAB-1/2026/10/m1.hdf5?tags=transmon,sample-A&project={mirror}", raw=b"m1")
    item = alice.ok("GET", f"/api/platform/catalog/{up['catalog_id']}")
    assert item["project"] == mirror and item["project_name"] == "YIG mirror coupling"
    dev.ok("PUT", "/api/files/LAB-1/2026/10/other.hdf5?tags=JPA", raw=b"o")
    assert alice.ok("GET", f"/api/platform/catalog?project={mirror}")["total"] == 1
    st = state.platform.sync_views()
    view = state.cfg.data_dir / "projects" / "YIG mirror coupling" / "LAB-1" / "2026" / "10" / "m1.hdf5"
    assert view.read_bytes() == b"m1" and st["linked"] == 1
    assert os.path.samefile(view, state.files_dir / "LAB-1" / "2026" / "10" / "m1.hdf5")      # 不多佔空間
    d = alice.ok("GET", f"/api/platform/projects/{mirror}")
    assert d["tags"] == ["sample-A", "transmon"] and [p["id"] for p in d["papers"]] == [12]
    assert d["papers"][0]["url"].endswith("/#/p/12") and d["data"] == 1
    # 手動加專案標籤；建立新專案（寫到論文庫）
    assert "flux-tunable" in alice.ok("PUT", f"/api/platform/projects/{mirror}/tags", {"tags": ["flux-tunable"]})["tags"]
    newp = alice.ok("POST", "/api/platform/projects", {"name": "Magnon comb", "short_name": "Comb"})
    assert newp["name"] == "Magnon comb" and any(p["name"] == "Magnon comb" for p in pstate.projects)
    assert alice.call("POST", "/api/platform/projects", {"name": "Magnon comb"})[0] == 409
    carol, _ = login(url, "carol", "carolpw", "123456")
    assert carol.call("POST", "/api/platform/projects", {"name": "X"})[0] == 403
    # 數據換專案 → 資料夾跟著換；使用者自己放進專案資料夾的檔不會被刪
    mine = view.parent / "m1_debg.hdf5"
    mine.write_bytes(b"user output")
    alice.ok("PUT", f"/api/platform/catalog/{up['catalog_id']}/project", {"project": newp["id"]})
    state.platform.sync_views()
    assert not view.exists() and mine.exists()
    assert (state.cfg.data_dir / "projects" / "Magnon comb" / "LAB-1" / "2026" / "10" / "m1.hdf5").exists()
    files = alice.ok("GET", f"/api/platform/projects/{newp['id']}/files")
    assert files["folder"] == "Magnon comb" and files["files"][0]["rel"] == "LAB-1/2026/10/m1.hdf5"
    # 論文庫改專案名稱 → 資料夾改名
    next(p for p in pstate.projects if p["name"] == "Magnon comb")["name"] = "Magnon comb v2"
    alice.ok("GET", "/api/platform/projects?refresh=1")
    state.platform.sync_views()
    assert (state.cfg.data_dir / "projects" / "Magnon comb v2" / "LAB-1" / "2026" / "10" / "m1.hdf5").exists()
    assert not (state.cfg.data_dir / "projects" / "Magnon comb").exists()
    assert Client(url, TOKEN).ok("GET", "/api/platform/projects")["projects"]        # 裝置 token：用記住的專案
