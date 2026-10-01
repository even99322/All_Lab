"""大程式伺服器 + 通信模塊，對真的論文庫與 Lab Control Hub。"""
from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest

from conftest import pl_call
from labcomm import PortalClient
from labcomm.errors import CommError, NotLoggedIn, PermissionDenied


def client(portal, user=None, pw=None):
    c = PortalClient(portal["base"], timeout=20, client_name="pytest")
    if user:
        r = c.login(user, pw)
        assert r["ok"], r
    return c


def boss(portal):
    return client(portal, "boss", "bosspass1")


def amy(portal):
    return client(portal, "amy", "amypass12")


def zip_with_manifest(mid, ver, extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{mid}/module.json", json.dumps({"id": mid, "version": ver, "name": mid}))
        z.writestr(f"{mid}/hello.py", "print('hi')\n")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


# ---- 登入與權限 --------------------------------------------------------------------
def test_login_uses_paperlib_accounts(portal):
    c = client(portal)
    assert c.ping()["server"] == "qel-portal"
    with pytest.raises(CommError, match="帳號或密碼錯誤"):
        c.login("amy", "wrong-password")
    with pytest.raises(NotLoggedIn):
        c.me()
    b = boss(portal)
    me = b.me()
    assert me["owner"] and me["manager"] and me["display_name"] == "老闆"
    assert set(me["modules"]) >= {"paperlib", "labcontrol", "lablogviewer", "monitor"}
    a = amy(portal)
    me = a.me()
    assert not me["manager"]
    assert me["access"] == {"paperlib": True, "labcontrol": False, "lablogviewer": True}
    assert "monitor" not in me["modules"] and "labcomm" in me["modules"]
    assert "monitor" not in [m["id"] for m in a.modules()]


def test_owner_toggles_module_access(portal):
    a = amy(portal)
    with pytest.raises(PermissionDenied, match="量測模塊"):
        a.relay("GET", "state")
    with pytest.raises(PermissionDenied, match="站長"):
        b = client(portal, "amy", "amypass12")
        b.api("GET", "/admin/users")
    bo = boss(portal)
    users = bo.api("GET", "/admin/users")["users"]
    assert {u["username"] for u in users} == {"boss", "amy", "vic"}
    bo.api("PUT", "/admin/users/amy", {"access": {"labcontrol": True}})
    st = a.relay("GET", "state")                  # Hub 的 token 由大程式代填
    assert st["server"] == "labhub"
    bo.api("PUT", "/admin/users/amy", {"access": {"paperlib": False}})
    with pytest.raises(PermissionDenied):
        a.papers(q="magnon")
    audit = bo.api("GET", "/admin/audit")["items"]
    assert any(i["action"] == "access" and "amy" in i["detail"] for i in audit)


def test_default_access_setting(portal):
    bo = boss(portal)
    bo.api("PUT", "/admin/settings", {"default_access": {"labcontrol": True}})
    assert client(portal, "vic", "vicpass12").me()["access"]["labcontrol"] is True


def test_block_user_kills_sessions(portal):
    a = amy(portal)
    a.me()
    boss(portal).api("PUT", "/admin/users/amy", {"blocked": True})
    with pytest.raises(NotLoggedIn):
        a.me()
    with pytest.raises(CommError, match="停用"):
        amy(portal)
    with pytest.raises(CommError, match="自己"):
        boss(portal).api("PUT", "/admin/users/boss", {"blocked": True})
    boss(portal).api("PUT", "/admin/users/amy", {"blocked": False})
    amy(portal).me()


def test_paperlib_disable_propagates(portal):
    v = client(portal, "vic", "vicpass12")
    portal["cfg"].recheck_s = 0
    _, ck = pl_call(portal["paperlib"], "POST", "/api/auth/login", {"username": "boss", "password": "bosspass1"})
    pl_call(portal["paperlib"], "PATCH", "/api/users/3", {"disabled": True}, ck)
    try:
        with pytest.raises(NotLoggedIn):
            v.me()
    finally:
        pl_call(portal["paperlib"], "PATCH", "/api/users/3", {"disabled": False}, ck)
        portal["cfg"].recheck_s = 600


def test_web_cookie_login_sso_and_csrf(portal):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(portal["base"] + "/api/v1/auth/login",
                                 data=json.dumps({"username": "amy", "password": "amypass12"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    r = opener.open(req)
    body = json.loads(r.read())
    assert "token" not in body                      # 網頁不拿 token
    cookies = {c.split("=", 1)[0]: c.split(";", 1)[0].split("=", 1)[1] for c in r.headers.get_all("Set-Cookie")}
    assert "qel_session" in cookies and "plsession" in cookies
    # 論文庫直接認得這個 cookie（同主機共用登入）
    me, _ = pl_call(portal["paperlib"], "GET", "/api/me", cookie=cookies["plsession"])
    assert me["username"] == "amy"
    h = {"Cookie": f"qel_session={cookies['qel_session']}", "Content-Type": "application/json"}
    req = urllib.request.Request(portal["base"] + "/api/v1/tags", data=b'{"name":"X"}', headers=h, method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        opener.open(req)
    assert e.value.code == 403
    req = urllib.request.Request(portal["base"] + "/api/v1/tags", data=b'{"name":"X"}',
                                 headers=dict(h, **{"X-QEL": "1"}), method="POST")
    assert json.loads(opener.open(req).read())["ok"]


def test_sso_ticket_for_desktop(portal):
    a = amy(portal)
    t = a.api("POST", "/sso/ticket")
    no_redirect = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with pytest.raises(urllib.error.HTTPError) as e:
        no_redirect.open(portal["base"] + t["url"] + "&next=" + urllib.request.quote("http://nas.local:8080/#/p/1"))
    assert e.value.code == 302 and e.value.headers["Location"] == "http://nas.local:8080/#/p/1"
    assert any(c.startswith("plsession=") for c in e.value.headers.get_all("Set-Cookie"))
    with pytest.raises(urllib.error.HTTPError) as e:          # 一次性
        no_redirect.open(portal["base"] + t["url"])
    assert e.value.headers["Location"] == "/#/login"
    t2 = a.api("POST", "/sso/ticket")
    with pytest.raises(urllib.error.HTTPError) as e:          # 不能轉到外站
        no_redirect.open(portal["base"] + t2["url"] + "&next=https://evil.example/")
    assert e.value.headers["Location"] == "/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


# ---- 標籤與論文 ------------------------------------------------------------------
def test_shared_tags_and_paper_links(portal):
    a = amy(portal)
    t = a.tags()
    by = {x["name"]: x for x in t["tags"]}
    assert by["BIC"]["category"] == "Project" and by["BIC"]["paperlib_count"] == 1
    assert by["Mirror"]["paper_count"] == 1
    assert [c["key"] for c in t["categories"]][0] == "Project"
    a.save_tag("YIG sphere", category="Board Design", aliases=["yig-sphere"])
    with pytest.raises(PermissionDenied):
        a.save_tag("BIC", category="Other")          # 系統標籤只有站長能改
    bo = boss(portal)
    bo.save_tag("BIC", description="Bound states in the continuum")
    a.link_papers("BIC", [2])
    papers = a.tag_papers("BIC")
    assert {p["id"] for p in papers} == {1, 2}
    p1 = next(p for p in papers if p["id"] == 1)
    assert p1["url"] == "http://nas.local:8080/#/p/1" and not p1["linked"] and p1["year"] == 2024
    assert next(p for p in papers if p["id"] == 2)["linked"]
    assert a.paper_url(3) == "http://nas.local:8080/#/p/3"
    found = a.papers(q="Cavity")
    assert found["items"][0]["id"] == 2
    with pytest.raises(CommError):
        a.api("POST", "/tags/BIC/papers", {"paper_id": 999})
    bo.api("POST", "/tags/BIC/rename", {"name": "BIC-magnon"})
    names = [x["name"] for x in a.tags()["tags"]]
    assert "BIC-magnon" in names
    bo.api("DELETE", "/tags/YIG%20sphere")
    assert "YIG sphere" not in [x["name"] for x in a.tags()["tags"]]


# ---- 數據 ------------------------------------------------------------------------
SCHEME = {"scheme": 2, "name": "BIC 2D", "graph": {"nodes": [], "links": []}}


def test_dataset_flow(portal):
    bo = boss(portal)
    bo.api("PUT", "/admin/users/amy", {"access": {"labcontrol": True}})
    a = amy(portal)
    f = portal["shares"] / "VNA_data" / "2026" / "bic.hdf5"
    f.write_bytes(b"\x89HDF fake")
    ds = a.register_dataset("bic.hdf5", path="\\\\nas\\ccuqel\\VNA_data\\2026\\bic.hdf5", tags=["bic", "Best Data"],
                            scheme=SCHEME, source={"module": "labcontrol", "host": "PC1"}, fingerprint="qfp1:abc")
    assert ds["created"] and ds["tags"] == ["BIC", "Best Data"] and ds["has_scheme"] and ds["downloadable"]
    again = a.register_dataset("bic.hdf5", path="/other/path.hdf5", fingerprint="qfp1:abc", tags=["Flux"])
    assert again["id"] == ds["id"] and not again["created"] and again["tags"] == ["Flux"]
    assert a.find_dataset(fingerprint="qfp1:abc")["scheme"] == SCHEME
    assert a.dataset_scheme(ds["id"]) == SCHEME
    a.set_dataset_tags(ds["id"], ["BIC", "Mirror"])
    groups = a.dataset_papers(ds["id"])
    assert [g["tag"] for g in groups] == ["BIC", "Mirror"]
    assert [p["id"] for p in groups[1]["papers"]] == [3]
    assert groups[0]["tag_url"] == "http://nas.local:8080/#/t/BIC"
    lst = a.datasets(tag="Mirror")
    assert lst["total"] == 1 and lst["items"][0]["id"] == ds["id"]
    # 路徑換成 NAS 掛載點才下載得到；fingerprint 相同時路徑會被更新
    a.register_dataset("bic.hdf5", path="\\\\nas\\ccuqel\\VNA_data\\2026\\bic.hdf5", fingerprint="qfp1:abc")
    out = portal["shares"].parent / "dl.hdf5"
    a.download_dataset(ds["id"], out)
    assert out.read_bytes() == b"\x89HDF fake"
    t = {x["name"]: x for x in a.tags()["tags"]}
    assert t["Mirror"]["datasets"] == 1
    # 沒有量測、讀檔權限 → 不能登錄
    bo.api("PUT", "/admin/users/vic", {"access": {"labcontrol": False, "lablogviewer": False}})
    v = client(portal, "vic", "vicpass12")
    with pytest.raises(PermissionDenied):
        v.register_dataset("x.hdf5")
    # 移除：只有站長或登錄的人
    bo.api("PUT", "/admin/users/vic", {"access": {"lablogviewer": True}})
    with pytest.raises(PermissionDenied, match="登錄的人"):
        v.api("DELETE", f"/datasets/{ds['id']}")
    a.api("DELETE", f"/datasets/{ds['id']}")
    assert a.datasets()["total"] == 0


def test_dataset_file_from_hub(portal):
    bo = boss(portal)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(portal["hub"] + "/api/files/PC1/2026/10/Data_1001/x.hdf5", data=b"HUBDATA",
                                 method="PUT", headers={"Authorization": "Bearer test-hub-token"})
    opener.open(req).read()
    ds = bo.register_dataset("x.hdf5", hub_file={"node": "PC1", "rel": "2026/10/Data_1001/x.hdf5"})
    assert ds["downloadable"]
    out = portal["shares"].parent / "hub.hdf5"
    bo.download_dataset(ds["id"], out)
    assert out.read_bytes() == b"HUBDATA"
    ds2 = bo.register_dataset("local.hdf5", path="C:/Users/QEL/x.hdf5")
    assert not ds2["downloadable"]
    with pytest.raises(CommError, match="量測電腦"):
        bo.download_dataset(ds2["id"], out)


# ---- 事件與傳遞 ------------------------------------------------------------------
def test_events_and_handoff(portal):
    a = amy(portal)
    bo = boss(portal)
    start = a.events(after=-1, wait=0)["last"]
    got = {}

    def poll():
        got["r"] = a.events(after=start, wait=10, topics=["dataset"])

    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.5)
    bo.register_dataset("ev.hdf5", path="/x/ev.hdf5")
    th.join(15)
    assert [e["topic"] for e in got["r"]["events"]] == ["dataset.created"]
    assert got["r"]["events"][0]["by"] == "boss"
    # handoff 只送給本人
    a.handoff("lablogviewer", "open_file", {"dataset_id": 1})
    mine = a.events(after=start, wait=0, topics=["handoff"])["events"]
    assert mine and mine[-1]["data"] == {"module": "lablogviewer", "action": "open_file", "payload": {"dataset_id": 1}}
    assert bo.events(after=start, wait=0, topics=["handoff"])["events"] == []
    with pytest.raises(CommError, match="app."):
        a.publish("dataset.created", {})
    # 沒有任何數據相關模塊的人收不到數據事件
    bo.api("PUT", "/admin/users/vic", {"access": {"paperlib": False, "lablogviewer": False, "labcontrol": False}})
    v = client(portal, "vic", "vicpass12")
    assert v.events(after=start, wait=0, topics=["dataset"])["events"] == []
    assert a.publish("app.test.ping", {"x": 1})["ok"]


def test_relay_commands_carry_user(portal):
    bo = boss(portal)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.open(urllib.request.Request(portal["hub"] + "/api/nodes/NODE1/claim", method="POST",
                                       data=json.dumps({"host": "PC1", "pid": 1, "user": "qel"}).encode(),
                                       headers={"Authorization": "Bearer test-hub-token",
                                                "Content-Type": "application/json"})).read()
    r = bo.relay("POST", "nodes/NODE1/commands", {"cmd": "ping"})
    cid = r["id"]
    req = urllib.request.Request(portal["hub"] + "/api/nodes/NODE1/commands?wait=1",
                                 headers={"Authorization": "Bearer test-hub-token"})
    cmds = json.loads(opener.open(req).read())["commands"]
    assert cmds[0]["id"] == cid and cmds[0]["from"]["portal_user"] == "boss"
    with pytest.raises(PermissionDenied, match="監控程式"):
        bo.relay("POST", "hub/restart", {})


# ---- 發佈 ------------------------------------------------------------------------
def test_module_releases(portal, tmp_path):
    bo = boss(portal)
    a = amy(portal)
    with pytest.raises(PermissionDenied):
        a.publish_release("labcomm", "1.0.1", zip_with_manifest("labcomm", "1.0.1"))
    with pytest.raises(CommError, match="版本"):
        bo.publish_release("labcomm", "1.0.2", zip_with_manifest("labcomm", "1.0.1"))
    with pytest.raises(CommError, match="id"):
        bo.publish_release("labcomm", "1.0.1", zip_with_manifest("lablogviewer", "1.0.1"))
    with pytest.raises(CommError, match="不安全"):
        bo.publish_release("labcomm", "1.0.1", zip_with_manifest("labcomm", "1.0.1", {"../evil.py": "x"}))
    bo.publish_release("labcomm", "1.0.1", zip_with_manifest("labcomm", "1.0.1"), notes="first")
    bo.publish_release("labcomm", "1.0.10", zip_with_manifest("labcomm", "1.0.10"))
    bo.publish_release("labcomm", "1.0.9", zip_with_manifest("labcomm", "1.0.9"))
    with pytest.raises(CommError, match="不能覆寫"):
        bo.publish_release("labcomm", "1.0.9", zip_with_manifest("labcomm", "1.0.9"))
    mods = {m["id"]: m for m in a.modules()}
    assert mods["labcomm"]["latest"] == "1.0.10"
    assert [r["version"] for r in a.releases("labcomm")] == ["1.0.10", "1.0.9", "1.0.1"]
    p = a.download_release("labcomm", "1.0.10", tmp_path / "x.zip")
    assert zipfile.ZipFile(p).read("labcomm/module.json")
    bo.api("DELETE", "/modules/labcomm/releases/1.0.10")
    assert {m["id"]: m for m in a.modules()}["labcomm"]["latest"] == "1.0.9"
    # 量測模塊沒開放 → 不能下載
    bo.publish_release("labcontrol", "0.0.13", zip_with_manifest("labcontrol", "0.0.13"))
    with pytest.raises(PermissionDenied):
        a.download_release("labcontrol", "0.0.13", tmp_path / "y.zip")


def test_health_and_static(portal):
    bo = boss(portal)
    h = bo.health()
    assert h["portal"]["online"] and h["paperlib"]["online"] and h["labhub"]["online"]
    assert h["paperlib"]["version"] == "1.5.1"
    assert "traffic" in h and h["labhub"]["nodes"]["nodes"] >= 0
    assert "traffic" not in amy(portal).health()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    html = opener.open(portal["base"] + "/").read().decode()
    assert "<title>" in html
    for path in ("/static/app.js", "/static/style.css", "/manifest.webmanifest", "/sw.js"):
        assert opener.open(portal["base"] + path).status == 200
    with pytest.raises(urllib.error.HTTPError):
        opener.open(portal["base"] + "/static/../qelportal/app.py")


def test_register_forwards_to_paperlib(portal):
    c = client(portal)
    r = c.register("newbie", "newbiepass1", "新人", "newbie@example.com", "想用量測")
    assert r["ok"]
    with pytest.raises(CommError, match="等站長審核"):
        c.login("newbie", "newbiepass1")
    with pytest.raises(CommError, match="已經有人使用"):
        c.register("newbie", "newbiepass1", "新人", "newbie@example.com")
