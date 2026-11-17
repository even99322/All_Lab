"""1.0.44：別間實驗室網段不同（192.168.10.x）→ 依閘道位址認得；站長移除離線的量測電腦。"""
from __future__ import annotations

import pytest

from labhub import netsites as ns
from labhub import netusage as nu
from labhub.server import HubHTTPError
from tests.test_143_netsites import Clock


def test_site_by_gateway_subnet(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    ns.set_sites(st, {"sites": [{"id": "lab328", "name": "328", "subnets": ["192.168.10.0/24"]},
                                {"id": "same", "name": "同網段", "subnets": ["192.168.50.0/24"]}]})
    hb = {"id": "nm-328000001", "host": "PC328", "on_net": False, "days": {},
          "gw": {"ip": "192.168.10.1", "mac": "cc:cc:cc:cc:cc:01"}}
    r = st.report_device(dict(hb), "100.64.0.20")                 # 經 VPN 的 heartbeat
    assert st.pcs["nm-328000001"]["site"] == "lab328"
    assert "CC:CC:CC:CC:CC:01" in r["gateways"] and "192.168.10.0/24" in r["subnets"]
    # 跟本實驗室同網段的：閘道位址不能用來認
    st.report_device({"id": "nm-other0002", "host": "X", "on_net": True, "days": {},
                      "gw": {"ip": "192.168.50.1", "mac": "dd:dd:dd:dd:dd:01"}}, "100.64.0.21")
    assert st.pcs["nm-other0002"]["site"] == "" and "DD:DD:DD:DD:DD:01" not in st.extra["gateways"]
    # 之後正常回報 → 算在 328
    st.report_device(dict(hb, on_net=True, ip="192.168.10.23", days={st.today(): {"up": 100, "down": 900}}), "100.64.0.20")
    v = st.recent_view(600, site="lab328")
    assert any(s.get("id") == "nm-328000001" for s in v["sources"])
    assert not any(s.get("id") == "nm-328000001" for s in st.recent_view(600)["sources"])


def test_forget_node(tmp_path):
    from labhub.server import Config, HubState as Hub

    hub = Hub(Config(tmp_path / "hub", token="dev"))
    hub.claim("LAB-3", {"host": "LAB-3", "user": "u", "version": "1.0.27"}, "192.168.50.13")
    with pytest.raises(HubHTTPError) as e:
        hub.forget_node("LAB-3")
    assert e.value.code == 409
    hub.node_offline("LAB-3")
    assert hub.forget_node("LAB-3") == {"ok": True, "name": "LAB-3"}
    assert "LAB-3" not in hub.nodes
    assert "LAB-3" not in Hub(Config(tmp_path / "hub", token="dev")).nodes     # 存檔後也不在
    with pytest.raises(HubHTTPError) as e:
        hub.forget_node("NOPE")
    assert e.value.code == 404
