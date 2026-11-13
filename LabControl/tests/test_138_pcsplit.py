"""1.0.38：流量監看 1.0.4 回報的 內網 / 外網 / VPN 通道；回報時告訴它 https 網域（內網用 hosts 指到 NAS）。"""
from __future__ import annotations

import json
import threading
import urllib.request

from labhub import netusage as nu

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Clock:
    def __init__(self, t=1_762_660_800.0):
        self.t = t

    def __call__(self):
        return self.t


def test_report_split_view_and_recent(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    day = st.today()
    st.report_device({"id": "nm-abc12345", "host": "PC", "days": {day: {
        "up": 1000, "down": 10000, "lan_up": 500, "lan_down": 4000, "wan_up": 300, "wan_down": 5000,
        "tun_up": 100, "tun_down": 900, "tunw_up": 50, "tunw_down": 400, "bogus": 7, "wan_x": "y"}}})
    clk.t += 60
    st.report_device({"id": "nm-abc12345", "host": "PC", "days": {day: {
        "up": 1100, "down": 13000, "lan_up": 500, "lan_down": 4000, "wan_up": 400, "wan_down": 8000,
        "tun_up": 100, "tun_down": 900, "tunw_up": 50, "tunw_down": 400}}})
    d = st.pcs["nm-abc12345"]["days"][day]
    assert d["wan_down"] == 8000 and "bogus" not in d
    v = st.view(7)["pcs"][0]
    t = v["split_today"]
    assert t["lan"] == {"up": 500, "down": 4000} and t["wan"]["down"] == 8000 and t["tunw"]["down"] == 400
    assert t["other"] == {"up": 1100 - 500 - 400 - 100, "down": 13000 - 4000 - 8000 - 900}
    assert v["split_month"]["wan"]["down"] == 8000
    r = [s for s in st.recent_view(600, "")["sources"] if s["key"] == "pc:nm-abc12345"][0]
    assert r["split"]["wan"] == {"up": 100, "down": 3000} and r["split"]["lan"] == {"up": 0, "down": 0}
    # 舊版（沒有分）→ None
    st.report_device({"id": "nm-old00001", "host": "Old", "days": {day: {"up": 1, "down": 2}}})
    assert [p for p in st.view(7)["pcs"] if p["key"] == "nm-old00001"][0]["split_today"] is None
    st.remove_pc("nm-abc12345")
    assert "pcx:nm-abc12345:wan" not in st.recent.data


def test_report_gives_lan_https_domain(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/api/netusage/report"
    body = json.dumps({"id": "nm-abc12345", "host": "PC", "days": {}}).encode()

    def post():
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with OP.open(req, timeout=10) as r:
            return json.loads(r.read())
    try:
        assert "lan_https" not in post()                     # 內網 https 沒開
        tls = state.tls
        tls.domain = "nas.tailabc12.ts.net"
        tls.listeners["hub"] = {"port": 8443, "ok": True}
        assert post()["lan_https"] == {"domain": "nas.tailabc12.ts.net", "url": "https://nas.tailabc12.ts.net:8443"}
    finally:
        srv.shutdown()
        psrv.shutdown()
