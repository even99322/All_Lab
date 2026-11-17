"""1.0.46：設備登記（寬限後停網、登記頁、代讀電腦）與流量分析（每小時、各設備、Traffic Analyzer 的 App）。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from labhub import netanalysis as na
from labhub import netreg as nr
from labhub import netsites as ns
from labhub import netusage as nu
from tests.test_143_netsites import Clock, FakeNG, XML2

XML_NEW = XML2.replace("</NewAttachDevice>", """<Device><IP>192.168.50.88</IP><Name>Galaxy-S24</Name><MAC>a6:00:00:00:00:88</MAC>
<ConnectionType>5GHz</ConnectionType><AllowOrBlock>Allow</AllowOrBlock><DeviceTypeNameV2>Smartphone</DeviceTypeNameV2></Device>
<Device><IP>192.168.50.89</IP><Name>ZNA-VNA</Name><MAC>00:90:b8:00:00:89</MAC><ConnectionType>wired</ConnectionType><AllowOrBlock>Allow</AllowOrBlock></Device>
</NewAttachDevice>""")


class Router:
    port, ssl = 5000, False

    def __init__(self, xml):
        self.xml = xml

    def traffic_meter(self):
        return {"TodayUpload": 10.0, "TodayDownload": 100.0}

    def info(self):
        return {}

    def attached_devices(self):
        return nu.parse_attached2(self.xml)


class NG(FakeNG):
    def __init__(self, xml, enabled=True):
        super().__init__(enabled)
        self.r = Router(xml)

    def __getattr__(self, k):
        return getattr(self.r, k)


def test_classify():
    assert nr.classify({"dtype": "10"}) == "phone" and nr.classify({"dtype": "35"}) == "computer"
    assert nr.classify({"dtype": "18"}) == "other"
    assert nr.classify({"dtype": "Smartphone"}) == "phone"
    assert nr.classify({"name": "DESKTOP-AB12CD"}) == "computer"
    assert nr.classify({"name": "HP LaserJet printer"}) == "other"
    assert nr.classify({"mac": "A6:00:00:00:00:01", "conn": "5GHz"}) == "phone"           # 隨機 MAC
    assert nr.classify({"mac": "00:90:B8:00:00:01", "conn": "wired", "name": "ZNA"}) == "unknown"


def test_enforce_main_grace_register_and_guest_expiry(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["router"].update(host="192.0.2.1", enabled=True)
    st.cfg["nas"]["host"] = "192.168.50.2"
    st.poll_router(NG(XML2))                                  # 開之前就在的
    nr.set_enforce(st, "main", True, 15)
    clk.t += 60
    ng = NG(XML_NEW)
    st.poll_router(ng)
    devs = {d["mac"]: d for d in st.recent_view(600)["devices"] if d.get("mac")}
    assert devs["A6:00:00:00:00:88"]["reg"]["state"] == "pending"
    assert devs["00:90:B8:00:00:89"]["reg"]["state"] == "exempt"                        # 認不出 → 不管
    assert devs["AA:BB:CC:DD:EE:FF"]["reg"]["state"] == "none"                          # 開之前就在（隨機 MAC 的手機）
    assert ng.calls == []
    clk.t += 16 * 60
    st.poll_router(ng)
    assert ng.calls == [("block", "A6:00:00:00:00:88")]
    assert "A6:00:00:00:00:88" in st.extra["regblock"]
    # 有人在 Hub 幫它登記 → 下一次讀路由器解除
    import datetime as _dt

    until = (_dt.date.fromisoformat(st.today()) + _dt.timedelta(days=2)).isoformat()
    nr.register(st, "a6-00-00-00-00-88", {"name": "小明的手機", "role": "guest", "until": until}, "ming")
    assert st.alias("mac:A6:00:00:00:00:88") == "小明的手機"
    st.poll_router(ng)
    assert ng.calls[-1] == ("allow", "A6:00:00:00:00:88") and "A6:00:00:00:00:88" not in st.extra["regblock"]
    # 訪客到期 → 立刻停網
    clk.t += 5 * 86400
    st.poll_router(ng)
    assert ng.calls[-1] == ("block", "A6:00:00:00:00:88") and st.extra["regblock"]["A6:00:00:00:00:88"]["why"] == "expired"
    with pytest.raises(Exception):
        nr.register(st, "a6:00:00:00:00:88", {"name": "x", "role": "guest", "until": "2020-01-01"}, "ming")
    # 存取控制沒開 → 記錯誤，不亂動
    ng2 = NG(XML_NEW.replace("a6:00:00:00:00:88", "a6:00:00:00:00:99"), enabled=False)
    st.poll_router(ng2)
    clk.t += 20 * 60
    st.poll_router(ng2)
    assert st.status["netreg_main"]["ok"] is False and "存取控制" in st.status["netreg_main"]["error"]
    # 關掉強制登記 → 全部解除
    nr.set_enforce(st, "main", False, 15)
    st.poll_router(ng)
    assert not st.extra["regblock"]


def test_collector_registration_and_site_analysis(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    ns.set_sites(st, {"sites": [{"id": "lab328", "name": "328", "subnets": ["192.168.10.0/24"],
                                 "router": {"host": "192.168.10.1", "user": "admin", "password": "pw"},
                                 "collector": "nm-col000001"}]})
    nr.set_enforce(st, "lab328", True, 10)
    clk.t += 5
    base = {"id": "nm-col000001", "host": "C", "ip": "192.168.10.11", "on_net": True, "days": {},
            "gw": {"ip": "192.168.10.1", "mac": "7c:10:c9:00:00:01"}}
    sr = {"site": "lab328", "ok": True, "rx": 1, "tx": 1, "t": clk.t,
          "clients": [{"mac": "a6:00:00:00:00:01", "ip": "192.168.10.50", "name": "iPhone", "type": "5GHz", "dtype": "10"},
                      {"mac": "00:11:22:33:44:55", "ip": "192.168.10.11", "name": "C", "type": "wired", "dtype": "1"}],
          "ta": {"t": clk.t, "hourly": [[10, 100], [20, 200]], "clients": [["a6:00:00:00:00:01", 5, 50]],
                 "apps_all": [["YouTube", 5, 50]], "apps": {"A6:00:00:00:00:01": [["YouTube", 5, 50]]}}}
    r = st.report_device(dict(base, site_router=sr), "100.64.0.10")
    assert r["site_block"] == [] and r["reg_status"]["A6:00:00:00:00:01"]["state"] == "pending"
    assert r["reg_status"]["00:11:22:33:44:55"]["state"] == "ok"                       # 代讀電腦自己（流量監看）
    clk.t += 11 * 60
    r = st.report_device(dict(base, site_router=dict(sr, t=clk.t)), "100.64.0.10")
    assert r["site_block"] == ["A6:00:00:00:00:01"]
    r = st.report_device(dict(base, block_state={"ok": True, "macs": ["A6:00:00:00:00:01"]}), "100.64.0.10")
    assert st.extra["regblock"]["A6:00:00:00:00:01"]["site"] == "lab328"
    # 登記頁送來 → 登記、解除
    r = st.report_device(dict(base, regs=[{"id": "r1", "mac": "a6:00:00:00:00:01", "ip": "192.168.10.50",
                                           "name": "小華 iPhone", "role": "student"},
                                          {"id": "r2", "mac": "de:ad:00:00:00:00", "name": "x", "role": "student"}]),
                          "100.64.0.10")
    assert r["regs_ack"][0] == {"id": "r1", "ok": True} and r["regs_ack"][1]["ok"] is False
    assert r["site_block"] == [] and st.extra["reg"]["A6:00:00:00:00:01"]["site"] == "lab328"
    # 不是代讀電腦送的 → 不收
    r = st.report_device({"id": "nm-other00001", "host": "X", "on_net": True, "days": {}, "gw": {"ip": "192.168.10.1"},
                          "regs": [{"id": "r3", "mac": "00:11:22:33:44:55", "name": "x", "role": "student"}]}, "100.64.0.11")
    assert "regs_ack" not in r
    a = na.analysis(st, "lab328")
    assert a["has_apps"] and [h["down"] for h in a["hourly"]] == [200, 100]
    assert a["devices"][0]["name"] == "小華 iPhone" and a["devices"][0]["apps"][0]["name"] == "YouTube"
    assert a["apps"][0] == {"name": "YouTube", "up": 5, "down": 50}


def test_main_analysis_hourly(tmp_path):
    clk = Clock(1_762_660_800.0)
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["router"].update(host="192.0.2.1", enabled=True)
    for i in range(5):
        st.recent.add("router", 100, 1000, clk.t - i * 3600)
    st.report_device({"id": "nm-abc12345", "host": "LAB-1", "ip": "192.168.50.11",
                      "days": {st.today(): {"up": 0, "down": 0}}}, "192.168.50.11")
    clk.t += 60
    st.report_device({"id": "nm-abc12345", "host": "LAB-1", "ip": "192.168.50.11",
                      "days": {st.today(): {"up": 10, "down": 90}}}, "192.168.50.11")
    a = na.analysis(st, "main", "pc:nm-abc12345")
    assert len(a["hourly"]) == 24 and a["total"]["down"] == 5000 and not a["has_apps"]
    assert any(d["key"] == "pc:nm-abc12345" for d in a["devices"])
    assert sum(h["down"] for h in a["dev_hourly"]["hourly"]) == 90


def test_reg_http(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(url, method="GET", body=None, cookie=""):
        req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                     headers={"Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})})
        try:
            with op.open(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st = nu.store(state)
        st.cfg["router"]["subnets"] = ["127.0.0.0/8"]
        st.cfg["lan"] = ["127.0.0.0/8"]
        code, page = call(url + "/reg")
        assert code == 200 and "設備登記" in page
        assert call(url + "/vendor/qrcode.js")[0] == 200
        code, v = call(url + "/api/netreg/self")
        assert code == 200 and v["ok"] is False
        oc = "labhub_token=" + call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]["token"]
        ac = "labhub_token=" + call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]["token"]
        assert call(url + "/api/netreg", "POST", {"mac": "aa:aa:aa:aa:aa:aa", "name": "x", "role": "student"})[0] == 401
        code, j = call(url + "/api/netreg", "POST", {"mac": "aa:aa:aa:aa:aa:aa", "name": "示範機", "role": "teacher"}, ac)
        assert code == 200 and j["reg"]["role"] == "teacher"
        assert call(url + "/api/netreg/enforce", "PUT", {"site": "main", "on": True}, ac)[0] == 403
        assert call(url + "/api/netreg/enforce", "PUT", {"site": "main", "on": True, "grace": 5}, oc)[1]["enforce"]["grace"] == 5
        code, j = call(url + "/api/netreg", cookie=ac)
        assert code == 200 and j["regs"][0]["mac"] == "AA:AA:AA:AA:AA:AA" and j["enforce"]["main"]["on"]
        assert call(url + "/api/netreg/AA:AA:AA:AA:AA:AA", "DELETE", cookie=ac)[0] == 403
        assert call(url + "/api/netreg/AA:AA:AA:AA:AA:AA", "DELETE", cookie=oc)[0] == 200
        code, j = call(url + "/api/netusage/analysis?site=main", cookie=ac)
        assert code == 200 and len(j["hourly"]) == 24
        assert call(url + "/reg/poster", cookie=ac)[0] == 200
    finally:
        srv.shutdown()
        psrv.shutdown()


def test_self_view(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["router"].update(host="192.0.2.1", enabled=True)
    st.poll_router(NG(XML_NEW))
    assert nr.self_view(st, "8.8.8.8")["why"] == "not-lan"
    assert nr.self_view(st, "192.168.50.200")["why"] == "unknown"
    v = nr.self_view(st, "192.168.50.88")
    assert v["ok"] and v["mac"] == "A6:00:00:00:00:88" and v["reg"]["state"] == "none"
    nr.register(st, v["mac"], {"name": "Galaxy", "role": "student"}, "self")
    v = nr.self_view(st, "192.168.50.88")
    assert v["reg"]["state"] == "ok" and v["reg"]["role_text"] == "學生" and v["name"] == "Galaxy"


def test_dhcp_reservation_via_collector(tmp_path):
    from labhub.server import HubHTTPError

    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    ns.set_sites(st, {"sites": [{"id": "lab328", "name": "328", "subnets": ["192.168.10.0/24"],
                                 "router": {"host": "192.168.10.1", "user": "admin", "password": "pw"},
                                 "collector": "nm-col000001"}]})
    base = {"id": "nm-col000001", "host": "C", "ip": "192.168.10.11", "on_net": True, "days": {},
            "gw": {"ip": "192.168.10.1", "mac": "7c:10:c9:00:00:01"}}
    sr = {"site": "lab328", "ok": True, "rx": 1, "tx": 1, "t": clk.t,
          "clients": [{"mac": "00:11:22:33:44:55", "ip": "192.168.10.11", "name": "C", "type": "wired"},
                      {"mac": "a6:00:00:00:00:01", "ip": "192.168.10.50", "name": "iPhone", "type": "5GHz"}]}
    st.report_device(dict(base, site_router=sr), "100.64.0.10")
    with pytest.raises(HubHTTPError):
        nr.dhcp_set(st, "main", "00:11:22:33:44:55", "192.168.50.9", "", "o")             # Netgear 不支援
    with pytest.raises(HubHTTPError):
        nr.dhcp_set(st, "lab328", "00:11:22:33:44:55", "192.168.50.9", "", "o")           # 不在網段
    with pytest.raises(HubHTTPError) as e:
        nr.dhcp_set(st, "lab328", "00:11:22:33:44:55", "192.168.10.50", "", "o")          # 別台正在用
    assert e.value.code == 409
    with pytest.raises(HubHTTPError):
        nr.dhcp_set(st, "lab328", "00:11:22:33:44:55", "192.168.10.1", "", "o")           # 路由器自己
    nr.dhcp_set(st, "lab328", "00-11-22-33-44-55", "192.168.10.11", "DESKTOP-EVEN", "o")
    r = st.report_device(dict(base), "100.64.0.10")
    assert r["site_dhcp"] == {"set": {"00:11:22:33:44:55": {"ip": "192.168.10.11", "name": "DESKTOP-EVEN"}}, "remove": []}
    v = st.recent_view(600, site="lab328")
    d = next(x for x in v["devices"] if x.get("mac") == "00:11:22:33:44:55")
    assert d["fixed"] == {"ip": "192.168.10.11", "managed": True, "pending": True}
    st.report_device(dict(base, dhcp_state={"ok": True, "list": [["00:11:22:33:44:55", "192.168.10.11"],
                                                                 ["aa:aa:aa:aa:aa:aa", "192.168.10.20"]]}), "100.64.0.10")
    v = st.recent_view(600, site="lab328")
    assert next(x for x in v["devices"] if x.get("mac") == "00:11:22:33:44:55")["fixed"]["pending"] is False
    with pytest.raises(HubHTTPError):
        nr.dhcp_set(st, "lab328", "a6:00:00:00:00:01", "192.168.10.20", "", "o")          # 路由器上已經給別台
    # 取消（連路由器上原本就有、不是 Hub 設的也能取消）
    nr.dhcp_set(st, "lab328", "AA:AA:AA:AA:AA:AA", "", "", "o")
    nr.dhcp_set(st, "lab328", "00:11:22:33:44:55", "", "", "o")
    r = st.report_device(dict(base), "100.64.0.10")
    assert r["site_dhcp"] == {"set": {}, "remove": ["AA:AA:AA:AA:AA:AA", "00:11:22:33:44:55"]}
    st.report_device(dict(base, dhcp_state={"ok": True, "list": []}), "100.64.0.10")
    assert st.report_device(dict(base), "100.64.0.10")["site_dhcp"]["remove"] == []


def test_renew_for(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    ns.set_sites(st, {"sites": [{"id": "lab328", "name": "328", "subnets": ["192.168.10.0/24"],
                                 "router": {"host": "192.168.10.1", "user": "admin", "password": "pw"},
                                 "collector": "nm-col000001"}]})
    col = {"id": "nm-col000001", "host": "C", "ip": "192.168.10.11", "on_net": True, "days": {},
           "gw": {"ip": "192.168.10.1", "mac": "7c:10:c9:00:00:01"}}
    pc = {"id": "nm-pc0000001", "host": "DESKTOP-WAYNE", "ip": "192.168.10.165", "on_net": True, "days": {},
          "gw": {"ip": "192.168.10.1", "mac": "7c:10:c9:00:00:01"}}
    sr = {"site": "lab328", "ok": True, "rx": 1, "tx": 1, "t": clk.t,
          "clients": [{"mac": "10:7c:61:b7:60:08", "ip": "192.168.10.165", "name": "W", "type": "wired"}]}
    st.report_device(dict(col, site_router=sr), "100.64.0.10")
    st.report_device(dict(pc), "100.64.0.20")
    nr.dhcp_set(st, "lab328", "10:7c:61:b7:60:08", "192.168.10.14", "W", "o")
    st.report_device(dict(col, dhcp_state={"ok": True, "list": [["10:7c:61:b7:60:08", "192.168.10.14"]]}), "100.64.0.10")
    assert nr.renew_for(st, "nm-pc0000001", [], pc) == {}                                # 沒要求立刻換
    d = next(x for x in st.recent_view(600, site="lab328")["devices"] if x.get("mac") == "10:7C:61:B7:60:08")
    assert d["fixed"]["moved"] is False and d["version"] == "" and d["kind"] == "pc"
    nr.request_renew(st, "lab328", "10:7C:61:B7:60:08", "o")
    assert nr.renew_for(st, "nm-pc0000001", ["desktop-wayne"], pc) == {}                 # 量測中
    assert "量測中" in nr.renew_state(st, "10:7C:61:B7:60:08")["waiting"]
    r = nr.renew_for(st, "nm-pc0000001", [], pc)
    assert r == {"renew": {"mac": "10:7C:61:B7:60:08", "from": "192.168.10.165", "to": "192.168.10.14"}}
    assert nr.renew_for(st, "nm-pc0000001", [], pc) == {}                                # 10 分鐘內不重複
    nr.renew_for(st, "nm-pc0000001", [], dict(pc, renew_result={"mac": "10:7C:61:B7:60:08", "ok": False, "error": "x"}))
    assert nr.renew_state(st, "10:7C:61:B7:60:08")["result"]["ok"] is False
    # 換好了（IP 對了）→ 清掉
    st.report_device(dict(pc, ip="192.168.10.14"), "100.64.0.20")
    st.report_device(dict(col, site_router=dict(sr, clients=[dict(sr["clients"][0], ip="192.168.10.14")])), "100.64.0.10")
    assert nr.renew_for(st, "nm-pc0000001", [], dict(pc, ip="192.168.10.14")) == {}
    assert nr.renew_state(st, "10:7C:61:B7:60:08") is None
