"""1.0.29：Hub「網路用量」——路由器 Traffic Meter（Netgear SOAP）、NAS（SNMP）、各台電腦（網卡計數）。"""
from __future__ import annotations

import datetime as dt
import threading
import time

import pytest

from labhub import netusage as nu

TZ = nu._tz("Asia/Taipei")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _t(day: str, hh: int = 12) -> float:
    return dt.datetime.fromisoformat(f"{day}T{hh:02d}:00:00").replace(tzinfo=TZ).timestamp()


# ---- Netgear ----------------------------------------------------------------
TM_XML = """<?xml version="1.0"?><soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/">
<soap-env:Body><m:GetTrafficMeterStatisticsResponse xmlns:m="urn:NETGEAR-ROUTER:service:DeviceConfig:1">
<NewTodayConnectionTime>11:14</NewTodayConnectionTime><NewTodayUpload>1,024.50</NewTodayUpload>
<NewTodayDownload>2048</NewTodayDownload><NewYesterdayUpload>10</NewYesterdayUpload><NewYesterdayDownload>20</NewYesterdayDownload>
<NewMonthUpload>3000/100.0</NewMonthUpload><NewMonthDownload>6000/200.0</NewMonthDownload>
<NewLastMonthUpload>--</NewLastMonthUpload><NewLastMonthDownload>--</NewLastMonthDownload>
</m:GetTrafficMeterStatisticsResponse><ResponseCode>000</ResponseCode></soap-env:Body></soap-env:Envelope>"""
OK_XML = "<?xml version='1.0'?><E><Body><ResponseCode>000</ResponseCode></Body></E>"


def test_meter_value_and_parse():
    assert nu.meter_value("25,350.10") == 25350.1 and nu.meter_value("6.19/0.88") == 6.19
    assert nu.meter_value("--") is None and nu.meter_value("11:14") is None
    code, f = nu.parse_soap(TM_XML)
    assert code == "000" and f["NewTodayUpload"] == "1,024.50"


class FakeRouter:
    def __init__(self, password="pw", ports=(5000,)):
        self.password, self.ports, self.calls = password, ports, []

    def __call__(self, url, data, headers):
        port = int(url.split(":")[2].split("/")[0])
        action = headers["SOAPAction"].split("#")[1]
        self.calls.append((port, action, headers.get("Cookie")))
        if port not in self.ports:
            raise OSError("connection refused")
        if action == "SOAPLogin":
            if b"<Password>" + self.password.encode() + b"</Password>" not in data:
                return 401, {}, "<E><ResponseCode>401</ResponseCode></E>"
            return 200, {"set-cookie": "sess_id=abc; path=/"}, OK_XML
        if headers.get("Cookie") != "sess_id=abc":
            return 401, {}, "<E><ResponseCode>401</ResponseCode></E>"
        if action == "GetTrafficMeterStatistics":
            return 200, {}, TM_XML
        if action == "GetInfo":
            return 200, {}, "<E><ModelName>RAX200</ModelName><Firmwareversion>V1.0</Firmwareversion><ResponseCode>0</ResponseCode></E>"
        return 501, {}, "<E><ResponseCode>501</ResponseCode></E>"


def test_netgear_client_tries_ports_and_reads_meter():
    fr = FakeRouter(ports=(443,))
    cl = nu.NetgearClient("192.0.2.1", "admin", "pw", opener=fr)
    tm = cl.traffic_meter()
    assert cl.port == 443 and cl.ssl is True
    assert tm["TodayUpload"] == 1024.5 and tm["MonthDownload"] == 6000 and tm["LastMonthUpload"] is None
    assert [c[0] for c in fr.calls[:3]] == [5000, 80, 443]
    assert cl.info()["ModelName"] == "RAX200"
    with pytest.raises(nu.NetgearError, match="帳號或密碼"):
        nu.NetgearClient("192.0.2.1", "admin", "bad", port=443, opener=FakeRouter(ports=(443,))).login()


def test_poll_router_stores_today_and_yesterday(tmp_path):
    st = nu.NetUsage(tmp_path, now=Clock(_t("2026-11-09")))
    st.cfg["router"].update(enabled=True, host="192.0.2.1")
    res = st.poll_router(nu.NetgearClient("192.0.2.1", "admin", "pw", opener=FakeRouter()))
    assert res["ok"] and res["info"]["ModelName"] == "RAX200"
    assert st.router["days"]["2026-11-09"] == {"up": int(1024.5 * nu.MB), "down": 2048 * nu.MB}
    assert st.router["days"]["2026-11-08"] == {"up": 10 * nu.MB, "down": 20 * nu.MB}
    v = st.view(7)
    assert v["router"]["month"]["down"] == 6000 * nu.MB and v["dates"][-1] == "2026-11-09"
    bad = st.poll_router(nu.NetgearClient("192.0.2.1", "admin", "pw", opener=FakeRouter(ports=())))
    assert not bad["ok"] and "連不到" in bad["error"]


def test_router_password_kept_private(tmp_path):
    st = nu.NetUsage(tmp_path)
    st.set_config({"router": {"enabled": True, "host": "192.0.2.1", "user": "admin", "password": "s3cret"}})
    assert "s3cret" not in (tmp_path / "netusage.json").read_text(encoding="utf-8")
    assert st._password() == "s3cret" and st.public_config()["router"]["has_password"]
    assert "s3cret" not in str(st.public_config()) and "s3cret" not in str(st.view())
    st.set_config({"router": {"host": "192.0.2.9"}})          # 密碼留空 = 不改
    assert st._password() == "s3cret" and nu.NetUsage(tmp_path).cfg["router"]["host"] == "192.0.2.9"


# ---- SNMP -------------------------------------------------------------------
def _response(rid: int, vbs) -> bytes:
    out = b""
    for oid, tag, val in vbs:
        if tag == 0x04:
            enc = nu._tlv(0x04, val.encode())
        elif tag == 0x46:
            enc = nu._tlv(0x46, val.to_bytes(max(1, (val.bit_length() + 7) // 8), "big"))
        else:
            enc = bytes([tag, 0])
        out += nu._tlv(0x30, nu._oid(oid) + enc)
    pdu = nu._int(rid) + nu._int(0) + nu._int(0) + nu._tlv(0x30, out)
    return nu._tlv(0x30, nu._int(1) + nu._tlv(0x04, b"public") + nu._tlv(0xA2, pdu))


class FakeAgent:
    """ifName 1=lo 2=eth0 3=eth1 4=docker0 5=bond0；bond0 計數可改。"""

    def __init__(self):
        self.names = {1: "lo", 2: "eth0", 3: "eth1", 4: "docker0", 5: "bond0"}
        self.cnt = {i: [1000 * i, 500 * i] for i in self.names}
        self.big = 2 ** 40

    def __call__(self, data: bytes) -> bytes:
        rid, _err, vbs = nu.snmp_parse(data)
        pdu = data[data.index(b"public") + 6]
        out = []
        for oid, _ in vbs:
            if pdu == 0xA1:                                 # GETNEXT over ifName
                base = nu.SnmpClient.IF_NAME
                idx = int(oid.rsplit(".", 1)[1]) if oid.startswith(base + ".") else 0
                nxt = [i for i in sorted(self.names) if i > idx]
                if nxt:
                    out.append((f"{base}.{nxt[0]}", 0x04, self.names[nxt[0]]))
                else:
                    out.append(("1.3.6.1.2.1.31.1.1.1.2.1", 0x04, "x"))
            else:
                i = int(oid.rsplit(".", 1)[1])
                col = oid.rsplit(".", 2)[1]
                v = self.cnt[i][0 if col == "6" else 1] + (self.big if i == 5 else 0)
                out.append((oid, 0x46, v))
        return _response(rid, out)


def test_snmp_roundtrip_and_interface_pick():
    ag = FakeAgent()
    cl = nu.SnmpClient("192.0.2.2", transport=ag)
    names = cl.interfaces()
    assert names == ag.names
    assert nu.pick_interfaces(names) == {5: "bond0"}            # bond 優先，不重複算 eth
    assert nu.pick_interfaces({2: "eth0", 3: "eth1", 1: "lo"}) == {2: "eth0", 3: "eth1"}
    assert nu.pick_interfaces(names, ["eth1"]) == {3: "eth1"}
    c = cl.counters([5])
    assert c[5] == (5000 + 2 ** 40, 2500 + 2 ** 40)             # Counter64


def test_poll_nas_accumulates_deltas(tmp_path):
    clk = Clock(_t("2026-11-09", 10))
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["nas"].update(enabled=True, host="192.0.2.2")
    ag = FakeAgent()
    cl = nu.SnmpClient("192.0.2.2", transport=ag)
    assert st.poll_nas(cl)["ok"]                                # 第一次：只取基準
    assert st.nas["days"] == {}
    ag.cnt[5] = [ag.cnt[5][0] + 3000, ag.cnt[5][1] + 700]
    clk.t += 60
    r = st.poll_nas(cl)
    assert r["interfaces"] == ["bond0"]
    assert st.nas["days"]["2026-11-09"] == {"up": 700, "down": 3000}
    ag.big = 0                                                  # NAS 重開機：計數歸零
    clk.t += 60
    st.poll_nas(cl)
    assert st.nas["days"]["2026-11-09"]["down"] == 3000 + 5000 + 3000


# ---- 各台電腦 -----------------------------------------------------------------
def test_report_pc_deltas_reset_stale_and_gap(tmp_path):
    clk = Clock(_t("2026-11-09", 9))
    st = nu.NetUsage(tmp_path, now=clk)
    st.report_pc("LAB-1", {"rx": 10_000, "tx": 2_000})
    clk.t += 30
    st.report_pc("LAB-1", {"rx": 15_000, "tx": 2_500})
    assert st.pcs["LAB-1"]["days"]["2026-11-09"] == {"up": 500, "down": 5000}
    clk.t += 1
    st.report_pc("LAB-1", {"rx": 14_900, "tx": 2_500})          # 舊的一筆晚到 → 略過
    assert st.pcs["LAB-1"]["days"]["2026-11-09"]["down"] == 5000
    clk.t += 30
    st.report_pc("LAB-1", {"rx": 100, "tx": 50})                # 重開機
    assert st.pcs["LAB-1"]["days"]["2026-11-09"] == {"up": 550, "down": 5100}
    clk.t = _t("2026-11-10", 15)                                # 隔天、超過 2 小時沒回報 → 重新取基準
    st.report_pc("LAB-1", {"rx": 9_000_000, "tx": 50})
    assert "2026-11-10" not in st.pcs["LAB-1"]["days"]
    clk.t += 30
    st.report_pc("LAB-1", {"rx": 9_000_400, "tx": 80})
    assert st.pcs["LAB-1"]["days"]["2026-11-10"] == {"up": 30, "down": 400}
    st.report_pc("bad", {"rx": "x"})
    assert "bad" not in st.pcs
    v = st.view(3)
    assert v["pcs"][0]["host"] == "LAB-1" and v["pcs"][0]["online"] and v["pcs"][0]["month"]["down"] == 5500
    st.set_label("LAB-1", "量測 1")
    assert nu.NetUsage(tmp_path).pcs["LAB-1"]["label"] == "量測 1"


def test_keep_days_trimmed(tmp_path):
    clk = Clock(_t("2026-11-09"))
    st = nu.NetUsage(tmp_path, now=clk)
    st.pcs["X"] = {"days": {"2024-01-01": {"up": 1, "down": 1}}, "last": {"rx": 0, "tx": 0, "t": clk.t}}
    clk.t += 10
    st.report_pc("X", {"rx": 5, "tx": 5})
    assert "2024-01-01" not in st.pcs["X"]["days"]


def test_client_counters_filters_virtual():
    from labcontrol.remote import netcount

    for n in ("Ethernet", "Wi-Fi", "乙太網路", "eth0", "en0", "enp3s0"):
        assert netcount.is_physical(n), n
    for n in ("Loopback Pseudo-Interface 1", "lo", "vEthernet (WSL)", "Tailscale", "VMware Network Adapter VMnet8",
              "docker0", "utun3", "awdl0", "ZeroTier One", "wg0", "Bluetooth Network Connection"):
        assert not netcount.is_physical(n), n
    c = netcount.counters()
    assert c is None or (c["rx"] >= 0 and c["tx"] >= 0)
    mac = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                        1000     0     200000     1000     0     200000     0
lo0        16384 127           127.0.0.1         1000     -     200000     1000     -     200000     -
en0        1500  <Link#6>    a4:83:e7:00:00:01  900000     0 1200000000   500000     0   80000000     0
en0        1500  192.168.50    192.168.50.31    900000     - 1100000000   500000     -   70000000     -
utun3      1380  <Link#20>                        500     0      90000      500     0      90000     0
awdl0      1484  <Link#11>   9a:00:00:00:00:02       1     0        100        1     0        100     0"""
    r = netcount.parse_netstat_ibn(mac)
    assert r == {"rx": 1200000000, "tx": 80000000, "nics": ["en0"]}


# ---- HTTP -------------------------------------------------------------------
def test_http_routes_and_heartbeat(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib
    from tests.test_122 import _call

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    dev = {"Authorization": "Bearer dev"}
    try:
        ow = _call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]
        oc = f"labhub_token={ow['token']}"
        al = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]
        ac = f"labhub_token={al['token']}"
        assert _call(url + "/api/netusage")[0] == 401
        code, page = _call(url + "/network", cookie=ac)
        assert code == 200 and "網路用量" in page and 'href="/network"' in page
        hb = {"id": "u@LAB-1", "host": "LAB-1", "user": "u", "net": {"rx": 1000, "tx": 100}}
        assert _call(url + "/api/devices/heartbeat", "POST", hb, headers=dev)[0] == 200
        hb["net"] = {"rx": 4000, "tx": 300}
        _call(url + "/api/devices/heartbeat", "POST", hb, headers=dev)
        st = {"host": "LAB-2", "user": "u", "state": "idle", "instruments": [], "net": {"rx": 50, "tx": 5}}
        assert _call(url + "/api/nodes/LAB-2/status", "POST", st, headers=dev)[0] == 200
        v = _call(url + "/api/netusage?days=7", cookie=ac)[1]
        pcs = {p["host"]: p for p in v["pcs"]}
        assert pcs["LAB-1"]["days"][v["today"]] == {"up": 200, "down": 3000} and "LAB-2" in pcs
        assert "config" not in v
        assert "net" not in str(state.devices.get("u@LAB-1", {}).keys())
        # 設定只有站長
        assert _call(url + "/api/netusage/config", "PUT", {"router": {"host": "x"}}, ac)[0] == 403
        code, r = _call(url + "/api/netusage/config", "PUT",
                        {"router": {"enabled": False, "host": "192.0.2.1", "password": "pw"}}, oc)
        assert code == 200 and r["config"]["router"]["has_password"] and "pw" not in str(r)
        v = _call(url + "/api/netusage", cookie=oc)[1]
        assert v["config"]["router"]["host"] == "192.0.2.1"
        assert _call(url + "/api/netusage/label", "PUT", {"host": "LAB-1", "label": "量測 1"}, ac)[0] == 200
        assert _call(url + "/api/netusage/pc/LAB-2", "DELETE", None, ac)[0] == 403
        assert _call(url + "/api/netusage/pc/LAB-2", "DELETE", None, oc)[0] == 200
    finally:
        srv.shutdown()
        psrv.shutdown()
        nu_store = getattr(state, "netusage", None)
        if nu_store is not None:
            nu_store.stop()
        time.sleep(0.05)
