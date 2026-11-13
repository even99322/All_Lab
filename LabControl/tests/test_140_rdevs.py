"""1.0.40：路由器上連著的裝置（標出沒被監看的）；即時頁「路由器有、監看不到的」。"""
from __future__ import annotations

from labhub import netusage as nu

XML2 = """<?xml version="1.0" encoding="UTF-8"?>
<soap-env:Envelope xmlns:soap-env="http://schemas.xmlsoap.org/soap/envelope/"><soap-env:Body>
<m:GetAttachDevice2Response xmlns:m="urn:NETGEAR-ROUTER:service:DeviceInfo:1"><NewAttachDevice>
<Device><IP>192.168.50.2</IP><Name>NAS</Name><MAC>00:11:32:aa:bb:cc</MAC><ConnectionType>wired</ConnectionType><SSID></SSID><Linkspeed>1000</Linkspeed><SignalStrength>100</SignalStrength></Device>
<Device><IP>192.168.50.11</IP><Name>LAB-1</Name><MAC>c8:9e:43:00:00:01</MAC><ConnectionType>wired</ConnectionType></Device>
<Device><IP>192.168.50.77</IP><Name>&lt;unknown&gt;</Name><MAC>aa:bb:cc:dd:ee:ff</MAC><ConnectionType>5GHz</ConnectionType><SSID>QEL-guest</SSID><Linkspeed>866</Linkspeed><SignalStrength>62</SignalStrength></Device>
</NewAttachDevice></m:GetAttachDevice2Response><ResponseCode>000</ResponseCode></soap-env:Body></soap-env:Envelope>"""


class Clock:
    def __init__(self, t=1_762_660_800.0):
        self.t = t

    def __call__(self):
        return self.t


def test_parse_attached():
    d = nu.parse_attached2(XML2)
    assert [x["ip"] for x in d] == ["192.168.50.2", "192.168.50.11", "192.168.50.77"]
    assert d[2]["name"] == "" and d[2]["mac"] == "AA:BB:CC:DD:EE:FF" and d[2]["type"] == "5GHz" and d[2]["ssid"] == "QEL-guest"
    assert nu.parse_attached2("<x/>") is None and nu.parse_attached2("not xml") is None
    v1 = nu.parse_attached1("2@1;192.168.50.2;NAS;00:11:32:AA:BB:CC;wired;1000;100;Allow@2;192.168.50.9;&lt;unknown&gt;;11:22:33:44:55:66;2.4GHz;144;80;Allow")
    assert [x["ip"] for x in v1] == ["192.168.50.2", "192.168.50.9"] and v1[1]["name"] == "" and v1[1]["type"] == "2.4GHz"
    assert nu.parse_attached1("0") == []


class Router:
    port, ssl = 5000, False

    def __init__(self):
        self.up, self.down = 10.0, 100.0
        self.devs = nu.parse_attached2(XML2)

    def traffic_meter(self):
        return {"TodayUpload": self.up, "TodayDownload": self.down}

    def info(self):
        return {}

    def attached_devices(self):
        return list(self.devs)


def test_router_devices_and_unknown(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["router"].update(host="192.0.2.1", enabled=True)
    st.cfg["nas"]["host"] = "192.168.50.2"
    day = st.today()
    st.report_device({"id": "nm-abc12345", "host": "LAB-1$", "ip": "192.168.50.11", "days": {day: {"up": 0, "down": 0}}})
    r = Router()
    st.poll_router(r)
    v = {x["ip"]: x for x in st.view(7)["router_devices"]["devices"]}
    assert v["192.168.50.2"]["watched"] == "NAS" and v["192.168.50.11"]["watched"].startswith("流量監看")
    assert v["192.168.50.77"]["watched"] == "" and v["192.168.50.77"]["online"]
    first = v["192.168.50.77"]["first"]
    # 兩分鐘後：路由器下載 30 MB；LAB-1 外網只有 5 MB → 監看不到 25 MB；手機離線了
    clk.t += 60
    st.report_device({"id": "nm-abc12345", "host": "LAB-1$", "ip": "192.168.50.11",
                      "days": {day: {"up": 0, "down": 6 * nu.MB, "wan_down": 5 * nu.MB, "lan_down": 1 * nu.MB}}})
    clk.t += 60
    r.down += 30.0
    r.devs = r.devs[:2]
    st.poll_router(r)
    unk = [s for s in st.recent_view(600, "")["sources"] if s["key"] == "unknown"][0]
    assert unk["down"] == 25 * nu.MB and unk["known"]["down"] == 5 * nu.MB and unk["nochart"]
    assert st.recent_view(600, "unknown")["chart"]["key"] == "router"                 # 不能選來畫圖
    v = {x["ip"]: x for x in st.view(7)["router_devices"]["devices"]}
    assert not v["192.168.50.77"]["online"] and v["192.168.50.77"]["first"] == first
    st.save(force=True)
    assert "AA:BB:CC:DD:EE:FF" in nu.NetUsage(tmp_path, now=clk).rdev


def test_router_devices_error_does_not_break_meter(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    st.cfg["router"]["host"] = "192.0.2.1"

    class R(Router):
        def attached_devices(self):
            raise nu.NetgearError("不支援")
    assert st.poll_router(R())["ok"] and st.status["router_devices"]["ok"] is False
