"""1.0.43：裝置改名、路由器封鎖、另一間實驗室（華碩路由器代讀）、提醒沒裝流量監看。"""
from __future__ import annotations

import pytest

from labhub import netsites as ns
from labhub import netusage as nu
from labhub.server import HubHTTPError

XML2 = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"><soap-env:Body>
<m:GetAttachDevice2Response xmlns:m="urn:NETGEAR-ROUTER:service:DeviceInfo:1"><NewAttachDevice>
<Device><IP>192.168.50.2</IP><Name>NAS</Name><MAC>00:11:32:aa:bb:cc</MAC><ConnectionType>wired</ConnectionType><AllowOrBlock>Allow</AllowOrBlock></Device>
<Device><IP>192.168.50.11</IP><Name>LAB-1</Name><MAC>c8:9e:43:00:00:01</MAC><ConnectionType>wired</ConnectionType><AllowOrBlock>Allow</AllowOrBlock></Device>
<Device><IP>192.168.50.77</IP><Name>&lt;unknown&gt;</Name><MAC>aa:bb:cc:dd:ee:ff</MAC><ConnectionType>5GHz</ConnectionType><AllowOrBlock>Block</AllowOrBlock></Device>
</NewAttachDevice></m:GetAttachDevice2Response><ResponseCode>000</ResponseCode></soap-env:Body></soap-env:Envelope>"""
MAIN_GW = "A0:00:00:00:00:01"
SITE_GW = "B0:00:00:00:00:02"


class Clock:
    def __init__(self, t=1_762_660_800.0):
        self.t = t

    def __call__(self):
        return self.t


class Router:
    port, ssl = 5000, False

    def traffic_meter(self):
        return {"TodayUpload": 10.0, "TodayDownload": 100.0}

    def info(self):
        return {}

    def attached_devices(self):
        return nu.parse_attached2(XML2)


class FakeNG:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.calls = []

    def block_enabled(self):
        return self.enabled

    def set_block_enabled(self, on):
        self.calls.append(("enable", on))
        self.enabled = on

    def set_block(self, mac, on):
        self.calls.append(("block" if on else "allow", mac))


def _store(tmp_path, clk):
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["router"].update(host="192.0.2.1", enabled=True)
    st.cfg["nas"]["host"] = "192.168.50.2"
    return st


def test_parse_allow_and_soap_session():
    d = nu.parse_attached2(XML2)
    assert [x["allow"] for x in d] == ["Allow", "Allow", "Block"]
    assert nu.parse_attached1("1@1;192.168.50.9;PC;11:22:33:44:55:66;wired;1000;100;Block")[0]["allow"] == "Block"


def test_netgear_config_sequence():
    sent = []

    class C(nu.NetgearClient):
        def __init__(self):
            pass

        def _call(self, service, method, params=None):
            sent.append((method, dict(params or {})))
            if method == "GetBlockDeviceEnableStatus":
                return {"NewBlockDeviceEnable": "0"}, ""
            return {}, ""
    c = C()
    assert c.block_enabled() is False
    c.set_block("aa:bb:cc:dd:ee:ff", True)
    names = [m for m, _ in sent]
    assert names[-3:] == ["ConfigurationStarted", "SetBlockDeviceByMAC", "ConfigurationFinished"]
    assert sent[-2][1] == {"NewAllowOrBlock": "Block", "NewMACAddress": "AA:BB:CC:DD:EE:FF"}


def test_alias_and_block(tmp_path):
    clk = Clock()
    st = _store(tmp_path, clk)
    st.report_device({"id": "nm-abc12345", "host": "LAB-1$", "ip": "192.168.50.11",
                      "days": {st.today(): {"up": 0, "down": 0}}}, "192.168.50.11")
    st.poll_router(Router())
    ns.set_alias(st, "mac:AA:BB:CC:DD:EE:FF", "訪客手機")
    ns.set_alias(st, "pc:nm-abc12345", "量測一號")
    with pytest.raises(HubHTTPError):
        ns.set_alias(st, "evil:x", "y")
    with pytest.raises(KeyError):
        ns.set_alias(st, "pc:nm-nothere000", "x")
    rv = st.recent_view(600)
    devs = {d["ip"]: d for d in rv["devices"]}
    assert devs["192.168.50.77"]["name"] == "訪客手機" and devs["192.168.50.77"]["allow"] == "Block"
    assert devs["192.168.50.11"]["kind"] == "pc" and devs["192.168.50.11"]["name"] == "量測一號"
    assert devs["192.168.50.2"]["kind"] == "nas" and rv["site"] == "main"
    st2 = nu.NetUsage(tmp_path, now=clk)                 # 存檔後還在
    assert st2.alias("mac:AA:BB:CC:DD:EE:FF") == "訪客手機"
    # 封鎖
    ng = FakeNG()
    with pytest.raises(HubHTTPError) as e:
        ns.block(st, "192.168.50.2", True, "192.168.50.11", client=ng)
    assert e.value.code == 400                          # MAC 格式不對
    with pytest.raises(HubHTTPError) as e:
        ns.block(st, "00:11:32:aa:bb:cc", True, "192.168.50.11", client=ng)
    assert "NAS" in str(e.value)
    with pytest.raises(HubHTTPError) as e:
        ns.block(st, "c8:9e:43:00:00:01", True, "192.168.50.11", client=ng)
    assert e.value.code == 409                          # 封鎖自己
    with pytest.raises(HubHTTPError) as e:
        ns.block(st, "aa-bb-cc-dd-ee-ff", True, "192.168.50.11", client=ng)
    assert e.value.code == 409 and ng.calls == []       # 存取控制沒開
    r = ns.block(st, "aa-bb-cc-dd-ee-ff", True, "192.168.50.11", enable_ok=True, client=ng)
    assert r["allow"] == "Block" and ng.calls == [("enable", True), ("block", "AA:BB:CC:DD:EE:FF")]
    r = ns.block(st, "AA:BB:CC:DD:EE:FF", False, "192.168.50.11", client=ng)
    assert r["allow"] == "Allow" and ng.calls[-1] == ("allow", "AA:BB:CC:DD:EE:FF")
    with pytest.raises(HubHTTPError) as e:
        ns.block(st, "12:34:56:78:9A:BC", True, "192.168.50.11", client=ng)
    assert e.value.code == 404


def test_other_lab_site(tmp_path):
    clk = Clock()
    st = _store(tmp_path, clk)
    day = st.today()
    # 本實驗室的電腦從內網回報 → 記下本實驗室路由器
    r = st.report_device({"id": "nm-main00001", "host": "LAB-1", "ip": "192.168.50.11", "on_net": True,
                          "gw": {"ip": "192.168.50.1", "mac": MAIN_GW.lower()}, "days": {day: {"up": 0, "down": 0}}},
                         "192.168.50.11")
    assert r["gateways"] == [MAIN_GW] and "192.168.50.0/24" in r["subnets"] and "collect" not in r
    ns.set_sites(st, {"sites": [{"id": "lab2", "name": "二館", "subnets": ["192.168.50.0/24", "10.20.0.0/16"],
                                 "router": {"host": "10.20.0.1", "user": "admin", "password": "s3cret"},
                                 "collector": "nm-site0001"}]})
    pub = ns.public_sites(st)
    assert pub[0]["router"]["has_password"] is True and "password" not in pub[0]["router"]
    assert "s3cret" not in (tmp_path / "netusage.json").read_text(encoding="utf-8")
    with pytest.raises(HubHTTPError):
        ns.set_sites(st, {"sites": [{"id": "main"}]})
    # 別的電腦經 VPN 回報：不給帳密
    r = st.report_device({"id": "nm-other0001", "host": "X", "ip": "192.168.50.20", "on_net": True,
                          "gw": {"ip": "192.168.50.1", "mac": SITE_GW}, "days": {day: {"up": 0, "down": 0}}},
                         "100.64.0.9")
    assert "collect" not in r and st.pcs["nm-other0001"]["site"] == ""
    # 代讀電腦經 VPN 回報 → 拿到帳密、記下二館路由器
    base = {"id": "nm-site0001", "host": "L2", "ip": "192.168.50.30", "on_net": True,
            "gw": {"ip": "192.168.50.1", "mac": SITE_GW}, "days": {day: {"up": 0, "down": 0}}}
    r = st.report_device(dict(base), "100.64.0.10")
    assert r["collect"]["password"] == "s3cret" and r["collect"]["host"] == "10.20.0.1"
    assert set(r["gateways"]) == {MAIN_GW, SITE_GW} and "10.20.0.0/16" in r["subnets"]
    assert st.pcs["nm-site0001"]["site"] == "lab2"
    # 之後這台也認得出是二館
    st.report_device({"id": "nm-other0001", "host": "X", "on_net": True, "gw": {"mac": SITE_GW},
                      "days": {day: {"up": 0, "down": 0}}}, "100.64.0.9")
    assert st.pcs["nm-other0001"]["site"] == "lab2"
    # 代讀路由器的數字：第一次當基準，之後算差值
    t0 = clk.t
    sr = {"site": "lab2", "ok": True, "rx": 1000, "tx": 100, "t": t0, "model": "RT-AX88U",
          "clients": [{"mac": "cc:cc:cc:cc:cc:01", "ip": "192.168.50.30", "name": "L2", "type": "wired"},
                      {"mac": "cc:cc:cc:cc:cc:02", "ip": "192.168.50.40", "name": "phone", "type": "5GHz"}]}
    st.report_device(dict(base, site_router=sr), "100.64.0.10")
    clk.t += 60
    st.report_device(dict(base, site_router=dict(sr, rx=6000, tx=600, t=clk.t)), "100.64.0.10")
    c = st.extra["sites"]["lab2"]
    assert c["days"][st.today()] == {"up": 500, "down": 5000} and c["status"]["model"] == "RT-AX88U"
    # 冒名：別台送 site_router 不收
    st.report_device({"id": "nm-other0001", "host": "X", "on_net": True, "gw": {"mac": SITE_GW},
                      "site_router": dict(sr, rx=10 ** 9, t=clk.t), "days": {day: {"up": 0, "down": 0}}}, "100.64.0.9")
    assert c["days"][st.today()] == {"up": 500, "down": 5000}
    v = st.recent_view(600, site="lab2")
    assert v["site"] == "lab2" and v["sources"][0]["down"] == 5000
    assert {s["key"] for s in v["sources"]} >= {"site:lab2", "pc:nm-site0001", "pc:nm-other0001", "unknown"}
    devs = {d["ip"]: d for d in v["devices"]}
    assert devs["192.168.50.30"]["kind"] == "pc" and devs["192.168.50.40"]["watched"] is False
    assert [s["id"] for s in v["sites"]] == ["main", "lab2"]
    main = st.recent_view(600)
    assert not any(s.get("id") in ("nm-site0001", "nm-other0001") for s in main["sources"])
    # 刪掉這間 → 密碼、路由器 MAC 一起清掉
    ns.set_sites(st, {"sites": []})
    assert st.secret_get("site:lab2:password") == "" and SITE_GW not in st.extra["gateways"]


class Notif:
    def __init__(self):
        self.sent = []

    def notify_user(self, user, cat, title, body, url="/", tag=""):
        self.sent.append((user, cat, title))
        return 1


def test_selfcheck(tmp_path):
    clk = Clock()
    st = _store(tmp_path, clk)
    win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"
    n = Notif()
    assert ns.selfcheck(st, "192.168.50.11", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile", "ming", n)["show"] is False
    assert ns.selfcheck(st, "203.0.113.5", win, "ming", n)["why"] == "unknown-ip"
    r = ns.selfcheck(st, "192.168.50.11", win, "ming", n)
    assert r["show"] is True and n.sent == [("ming", "netmeter", "這台電腦還沒裝流量監看")]
    ns.selfcheck(st, "192.168.50.11", win, "ming", n)
    assert len(n.sent) == 1                               # 一天最多一次
    st.report_device({"id": "nm-abc12345", "host": "LAB-1", "ip": "192.168.50.11", "days": {st.today(): {"up": 0, "down": 0}}},
                     "192.168.50.11")
    assert ns.selfcheck(st, "192.168.50.11", win, "ming", n) == {"show": False, "why": "ok", "pc": "LAB-1"}
    assert ns.selfcheck(st, "100.64.0.9", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)", "", n)["os"] == "mac"
    clk.t += 86400 + 10
    ns.selfcheck(st, "192.168.50.12", win, "ming", n)
    assert len(n.sent) == 2


def test_notify_user_category(tmp_path):
    from labhub.notify import CATS, Notifier

    assert CATS["netmeter"]["default"] is True
    n = Notifier(tmp_path, sender=lambda *a, **k: (201, ""))
    assert n.notify_user("nobody", "netmeter", "t", "b") == 0


def test_http_routes(tmp_path):
    import json
    import threading
    import urllib.error
    import urllib.request

    from labhub.server import Config, make_server
    from tests import fake_paperlib

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(url, method="GET", body=None, cookie="", ua=""):
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                     headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {}),
                                              **({"User-Agent": ua} if ua else {})})
        try:
            with op.open(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        oc = "labhub_token=" + call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]["token"]
        ac = "labhub_token=" + call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]["token"]
        assert call(url + "/api/netusage/alias", "PUT", {"key": "mac:AA:BB:CC:DD:EE:FF", "name": "x"})[0] == 401
        assert call(url + "/api/netusage/alias", "PUT", {"key": "mac:AA:BB:CC:DD:EE:FF", "name": "印表機"}, ac) == (200, {"ok": True})
        assert call(url + "/api/netusage/sites", cookie=ac)[0] == 403
        assert call(url + "/api/netusage/block", "POST", {"mac": "AA:BB:CC:DD:EE:FF", "block": True}, ac)[0] == 403
        code, j = call(url + "/api/netusage/block", "POST", {"mac": "AA:BB:CC:DD:EE:FF", "block": True}, oc)
        assert code == 404                                          # 路由器沒看過
        code, j = call(url + "/api/netusage/sites", "PUT", {"sites": [{"id": "lab2", "name": "二館", "router": {"host": "10.0.0.1", "password": "pw"}}]}, oc)
        assert code == 200 and j["sites"][0]["router"]["has_password"] and "pw" not in json.dumps(j)
        code, j = call(url + "/api/netusage/sites", cookie=oc)
        assert code == 200 and j["sites"][0]["name"] == "二館" and "pcs" in j
        code, j = call(url + "/api/netusage/recent?window=600&site=lab2", cookie=ac)
        assert code == 200 and j["site"] == "lab2" and [s["id"] for s in j["sites"]] == ["main", "lab2"]
        code, j = call(url + "/api/netusage/selfcheck", cookie=ac, ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        assert code == 200 and "show" in j
        code, j = call(url + "/api/netusage/report", "POST", {"id": "nm-abc12345", "host": "PC", "ip": "127.0.0.1",
                                                              "gw": {"ip": "192.168.50.1", "mac": "a0:00:00:00:00:01"},
                                                              "days": {}})
        assert code == 200 and "subnets" in j and "gateways" in j
    finally:
        srv.shutdown()
        psrv.shutdown()
