"""QEL 流量監看：計量規則、回報內容、控制埠、設定、新版查詢。"""
from __future__ import annotations

import ipaddress
import threading

import pytest

from netmeter import __version__, agent, config, hub
from netmeter.meter import Meter, on_net, today_str

NETS = [ipaddress.ip_network("192.168.50.0/24")]


class Seq:
    """依序回傳的假網卡計數。"""

    def __init__(self):
        self.t = 1_800_000_000.0
        self.boot = "b1"
        self.ifs = {"Ethernet": {"rx": 1000, "tx": 100, "ips": ["192.168.50.31"]},
                    "Wi-Fi": {"rx": 5000, "tx": 500, "ips": ["10.0.0.5"]}}

    def __call__(self):
        return {"t": self.t, "boot": self.boot, "ifaces": {k: dict(v) for k, v in self.ifs.items()}}

    def add(self, name, rx, tx, dt=30):
        self.ifs[name]["rx"] += rx
        self.ifs[name]["tx"] += tx
        self.t += dt


def test_on_net():
    assert on_net(["192.168.50.7"], NETS) and not on_net(["192.168.5.7", "fe80::1%3"], NETS) and not on_net([], NETS)


def test_meter_counts_only_lab_interface(tmp_path):
    s = Seq()
    m = Meter(tmp_path / "state.json", NETS, s)
    assert m.step() == {"up": 0, "down": 0, "vpn_up": 0, "vpn_down": 0, "on": True, "split": {}}          # 第一次：基準
    s.add("Ethernet", 3000, 400)
    s.add("Wi-Fi", 9999, 999, dt=0)                              # 不在實驗室網段的網卡不算
    assert m.step() == {"up": 400, "down": 3000, "vpn_up": 0, "vpn_down": 0, "on": True, "split": {}}
    assert m.rate == {"up": 400 / 30, "down": 3000 / 30}
    assert m.days[today_str(s.t)] == {"up": 400, "down": 3000}
    # 離開實驗室網段 → 這段與回來的第一段都不算
    s.ifs["Ethernet"]["ips"] = ["172.20.1.5"]
    s.add("Ethernet", 7777, 777)
    assert m.step()["on"] is False
    s.ifs["Ethernet"]["ips"] = ["192.168.50.31"]
    s.add("Ethernet", 8888, 888)
    assert m.step()["down"] == 0
    s.add("Ethernet", 100, 10)
    assert m.step()["down"] == 100
    # 重開機 / 計數變小 → 重新取基準
    s.boot = "b2"
    s.ifs["Ethernet"]["rx"] = 50
    s.t += 30
    assert m.step()["down"] == 0
    s.ifs["Ethernet"]["rx"] = 10
    s.t += 30
    assert m.step()["down"] == 0
    s.add("Ethernet", 20, 2)
    assert m.step()["down"] == 20
    assert m.days[today_str(s.t)]["down"] == 3000 + 100 + 20
    m.save()
    m2 = Meter(tmp_path / "state.json", NETS, s)                 # 重開程式：接著算，id 不變
    assert m2.id == m.id and m2.days == m.days
    s.add("Ethernet", 5, 1)
    assert m2.step()["down"] == 5
    assert m2.month(s.t)["down"] == 3125 and today_str(s.t) in m2.recent(3, s.t)


def test_agent_payload_send_and_control(tmp_path, monkeypatch):
    s = Seq()
    sent = []
    cfg = config.load()
    cfg["control_port"] = 0
    ag = agent.Agent(cfg=cfg, sampler=s, reporter=lambda hubs, p: (sent.append((hubs, p)) or ("http://hub", {"ok": True})))
    ag.tick()
    s.add("Ethernet", 1000, 10)
    ag.tick()
    assert ag.send() and sent[-1][1]["days"][today_str(s.t)] == {"up": 10, "down": 1000}
    p = sent[-1][1]
    assert p["id"].startswith("nm-") and p["ip"] == "192.168.50.31" and p["ifaces"] == ["Ethernet"] and p["version"] == __version__
    s.ifs["Ethernet"]["ips"] = []
    s.t += 30
    ag.tick()
    assert ag.send() is False and len(sent) == 1                # 不在實驗室網段：不送
    ag.reporter = lambda hubs, p: (_ for _ in ()).throw(ConnectionError("down"))
    s.ifs["Ethernet"]["ips"] = ["192.168.50.31"]
    s.t += 30
    ag.tick()
    assert ag.send() is False and "down" in ag.status()["error"]
    srv = agent.ControlServer(ag)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        st = agent.control("status", port=port)
        assert st["version"] == __version__ and st["on_net"] and st["today"]["down"] == 1000
        assert agent.control("quit", port=port) == {"ok": True} and ag.stop.is_set()
        ag.cfg["control_port"] = port
        with pytest.raises(OSError):                             # 同一台只能有一個背景程式
            agent.ControlServer(ag)
    finally:
        srv.shutdown()
        srv.server_close()
    assert agent.control("status", port=port) is None


def test_agent_run_loop_saves_on_exit(tmp_path):
    s = Seq()
    cfg = dict(config.load(), control_port=0, sample_s=0.05, report_s=0.05)
    got = []
    ag = agent.Agent(cfg=cfg, sampler=s, reporter=lambda hubs, p: (got.append(p) or ("h", {})))
    th = threading.Thread(target=ag.run, daemon=True)
    th.start()
    import time
    time.sleep(0.4)
    ag.stop.set()
    th.join(5)
    assert got and (config.data_dir() / "state.json").exists()


def test_config_normalize_and_defaults(tmp_path):
    assert config.normalize_hub("192.168.50.2") == "http://192.168.50.2:8765"
    assert config.normalize_hub("https://hub.example:8443/") == "https://hub.example:8443"
    assert config.valid_subnet("192.168.50.0/24") and not config.valid_subnet("abc")
    cfg = config.load()
    cfg["subnets"] = ["10.1.0.0/16", "bad"]
    config.save(cfg)
    assert config.load()["subnets"] == ["10.1.0.0/16"]
    cfg["subnets"] = ["bad"]
    config.save(cfg)
    assert config.load()["subnets"] == config.DEFAULTS["subnets"]


def test_latest_from_hub_info(monkeypatch):
    info = {"apps": [{"id": "labcontrol", "downloads": []},
                     {"id": "netmeter", "downloads": [
                         {"platform": "windows", "version": "1.0.1", "url": "/get/netmeter/1.0.1/QELNetMeter_v1.0.1_windows.exe",
                          "name": "QELNetMeter_v1.0.1_windows.exe", "sha256": "ab"},
                         {"platform": "windows", "version": "1.0.0", "url": "/x"},
                         {"platform": "macos", "version": "1.0.2", "url": "/y"}]}]}
    monkeypatch.setattr(hub, "_req", lambda url, body=None, timeout=8.0: info)
    r = hub.latest("http://h:8765", "windows")
    assert r["version"] == "1.0.1" and r["url"] == "http://h:8765/get/netmeter/1.0.1/QELNetMeter_v1.0.1_windows.exe"
    assert hub.latest("http://h:8765", "macos")["version"] == "1.0.2"
    assert hub.vkey("1.0.10") > hub.vkey("1.0.9")


def test_report_tries_hubs_in_order(monkeypatch):
    calls = []

    def fake(url, body=None, timeout=8.0):
        calls.append(url)
        if "bad" in url:
            raise OSError("refused")
        return {"ok": True}
    monkeypatch.setattr(hub, "_req", fake)
    used, r = hub.report(["http://bad:1", "http://good:8765"], {"id": "x"})
    assert used == "http://good:8765" and calls == ["http://bad:1/api/netusage/report", "http://good:8765/api/netusage/report"]
    with pytest.raises(ConnectionError):
        hub.report(["http://bad:1"], {})


def test_self_test_runs(monkeypatch, capsys):
    from netmeter import app

    monkeypatch.setenv("NETMETER_SELFTEST_GUI", "0")
    assert app.self_test() == 0
    assert "網卡計數" in capsys.readouterr().out


def test_meter_counts_tailscale_separately(tmp_path):
    """1.0.2：Tailscale（100.x）網卡另外記成 vpn_up / vpn_down（在實驗室網段時才記）。"""
    clk = {"t": 1_000_000.0}
    cnt = {"eth": [0, 0], "ts": [0, 0]}
    lab = {"on": True}

    def sampler():
        return {"t": clk["t"], "boot": "b", "ifaces": {
            "eth": {"rx": cnt["eth"][0], "tx": cnt["eth"][1], "ips": ["192.168.50.11"] if lab["on"] else ["10.0.0.5"]},
            "Tailscale": {"rx": cnt["ts"][0], "tx": cnt["ts"][1], "ips": ["100.67.66.99"]}}}

    m = Meter(tmp_path / "s.json", NETS, sampler)
    m.step()
    clk["t"] += 30
    cnt["eth"] = [1000, 500]
    cnt["ts"] = [300, 200]
    r = m.step()
    assert r["down"] == 1000 and r["vpn_down"] == 300 and r["vpn_up"] == 200
    lab["on"] = False                                        # 不在實驗室：都不算
    clk["t"] += 30
    cnt["eth"] = [5000, 5000]
    cnt["ts"] = [900, 900]
    r = m.step()
    assert r["down"] == 0 and r["vpn_down"] == 0
    day = list(m.days.values())[0]
    assert day == {"up": 500, "down": 1000, "vpn_up": 200, "vpn_down": 300}
    m.save()
    assert Meter(tmp_path / "s.json", NETS, sampler).days == m.days


def test_windows_boot_task_xml():
    """1.0.3：開機（不用登入）以 SYSTEM 啟動、每 10 分鐘檢查、不限時間、不受電池限制。"""
    import xml.etree.ElementTree as ET
    from pathlib import PureWindowsPath

    from netmeter import install

    x = install.task_xml(PureWindowsPath(r"C:\Program Files\QELNetMeter\QELNetMeter.exe"))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(x.split("?>", 1)[1])
    assert root.find("t:Triggers/t:BootTrigger", ns) is not None
    assert root.find("t:Triggers/t:CalendarTrigger/t:Repetition/t:Interval", ns).text == "PT10M"
    assert root.find("t:Principals/t:Principal/t:UserId", ns).text == "S-1-5-18"
    st = root.find("t:Settings", ns)
    assert st.find("t:ExecutionTimeLimit", ns).text == "PT0S"
    assert st.find("t:DisallowStartIfOnBatteries", ns).text == "false"
    assert st.find("t:MultipleInstancesPolicy", ns).text == "IgnoreNew"
    ex = root.find("t:Actions/t:Exec", ns)
    assert ex.find("t:Command", ns).text.endswith("QELNetMeter.exe") and ex.find("t:Arguments", ns).text == "--agent"


def test_migrate_old_state(tmp_path):
    """1.0.3：舊版（使用者底下）的紀錄搬到 ProgramData；新的已經有就不動。"""
    import os
    import time

    a, b, dst = tmp_path / "a", tmp_path / "b", tmp_path / "new"
    for d in (a, b, dst):
        d.mkdir()
    (a / "state.json").write_text('{"id": "nm-old"}')
    (b / "state.json").write_text('{"id": "nm-newer"}')
    (b / "config.json").write_text('{"hubs": ["http://h"]}')
    os.utime(a / "state.json", (time.time() - 100, time.time() - 100))
    assert config.migrate_old(dst, [a, b, tmp_path / "none"]) == b
    assert "nm-newer" in (dst / "state.json").read_text() and (dst / "config.json").exists()
    (b / "state.json").write_text('{"id": "nm-other"}')
    assert config.migrate_old(dst, [a, b]) is None and "nm-newer" in (dst / "state.json").read_text()


def test_ensure_running_uses_boot_task(monkeypatch):
    """1.0.3：裝了系統排程 → 叫排程啟動，不再登記「登入時啟動」。"""
    from netmeter import app, install

    calls = []
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setattr(install, "service_installed", lambda *a, **k: True)
    monkeypatch.setattr(install, "run_service", lambda: calls.append("run") or True)
    monkeypatch.setattr(install, "enable_autostart", lambda *a, **k: calls.append("runkey"))
    monkeypatch.setattr(install, "start_agent", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(app, "_wait_agent", lambda *a, **k: True)
    monkeypatch.setattr(app.agent, "control", lambda *a, **k: None)
    assert "開機" in app.ensure_running() and calls == ["run"]
    monkeypatch.setattr(app.agent, "control", lambda *a, **k: {"version": __version__, "pid": 1})
    calls.clear()
    assert app.ensure_running() == "" and calls == []
    # 1.0.8：在跑的是使用者身分的（以前「登入時啟動」開的）→ 叫它結束，換系統排程的
    monkeypatch.setattr(app.agent, "control", lambda cmd, **k: calls.append(cmd) or {"version": __version__, "pid": 1,
                                                                                       "as_system": False})
    monkeypatch.setattr(app.agent, "wait_gone", lambda *a, **k: True)
    calls.clear()
    app.ensure_running()
    assert calls == ["status", "quit", "run"]


# ---- 1.0.4：內網 / 外網 / VPN；內網 https（hosts）----
def test_split_flows():
    from netmeter.meter import split_flows

    local = {"192.168.50.11", "fe80::1"}
    fl = {("192.168.50.11", "192.168.50.2", False): [100, 1000],      # NAS → 內網
          ("140.123.1.1", "192.168.50.11", False): [50, 2000],        # 學校 / 網際網路（位址順序反過來也認得）→ 外網
          ("192.168.50.11", "192.168.50.2", True): [10, 100],         # VPN 直連區網裡的 NAS
          ("192.168.50.11", "203.0.113.9", True): [40, 400],          # VPN 經外網（DERP / 外面的裝置）
          ("192.168.50.11", "224.0.0.251", False): [5, 0],            # 多點傳送 → 內網
          ("100.67.66.99", "100.64.0.1", False): [999, 999],          # Tailscale 網卡上的（另外算 vpn_*）→ 不算
          ("127.0.0.1", "127.0.0.1", False): [7, 7]}
    p = split_flows(fl, local, NETS)
    assert p == {"lan": [105, 1000], "wan": [50, 2000], "tun": [50, 500], "tunw": [40, 400]}


def test_meter_records_split(tmp_path):
    class F:
        ok, kind = True, "test"

        def __init__(self):
            self.q = {}

        def drain(self):
            q, self.q = self.q, {}
            return q

    s, f = Seq(), F()
    m = Meter(tmp_path / "state.json", NETS, s, flows=f)
    m.step()
    s.add("Ethernet", 3000, 400)
    f.q = {("192.168.50.31", "192.168.50.2", False): [100, 1000], ("192.168.50.31", "8.8.8.8", False): [100, 2000]}
    r = m.step()
    assert r["split"]["down"] == {"lan": 1000, "wan": 2000, "tun": 0, "tunw": 0}
    d = m.days[today_str(s.t)]
    assert d["lan_down"] == 1000 and d["wan_down"] == 2000 and d["lan_up"] == 100 and "tun_up" not in d
    assert m.month(s.t)["wan_down"] == 2000 and m.rate_split["wan"]["down"] == 2000 / 30
    # 連線資料晚到（網卡這次沒變）也照記；離開實驗室網段時不記
    s.t += 30
    f.q = {("192.168.50.31", "8.8.8.8", False): [0, 500]}
    assert m.step()["split"]["down"]["wan"] == 500 and m.days[today_str(s.t)]["wan_down"] == 2500
    s.ifs["Ethernet"]["ips"] = ["10.9.9.9"]
    s.t += 30
    f.q = {("10.9.9.9", "8.8.8.8", False): [0, 500]}
    assert m.step()["split"] == {}
    m.save()
    assert Meter(tmp_path / "state.json", NETS, s).days[today_str(s.t)]["wan_up"] == 100


def test_etw_event_parse_and_nettop():
    import struct

    from netmeter import flows

    data = struct.pack("<II", 1234, 1500) + bytes([192, 168, 50, 2]) + bytes([192, 168, 50, 11]) + b"\x00" * 20
    pid, size, a, b, send = flows._parse(11, data)
    assert (pid, size, send) == (1234, 1500, False) and a == bytes([192, 168, 50, 2])
    v6 = struct.pack("<II", 9, 60) + bytes(15) + b"\x01" + bytes(16) + b"\x00" * 12
    assert flows._parse(58, v6)[4] is True and len(flows._parse(58, v6)[2]) == 16
    assert flows._parse(12, data) is None and flows._parse(10, b"\x00" * 8) is None
    assert flows.is_tunnel_name("tailscaled.exe") and not flows.is_tunnel_name("chrome.exe")
    txt = ("time,,bytes_in,bytes_out,\n"
           "10:00:00.1,Google Chrome H.512,,0,0,\n"
           "10:00:00.1,tcp4 192.168.50.11:52429<->140.112.1.1:443,123456,789,\n"
           "10:00:00.1,udp6 fe80::1%en0.5353<->ff02::fb.5353,10,20,\n"
           "10:00:00.1,tailscaled.88,,0,0,\n"
           "10:00:00.1,udp4 192.168.50.11:41641<->203.0.113.9:41641,500,600,\n"
           "10:00:00.1,udp4 *:5353<->*:*,1,1,\n")
    seq = [txt, txt.replace("123456,789", "123556,799").replace("500,600", "700,600")]
    nf = flows.NettopFlows(runner=lambda: seq.pop(0))
    assert nf.start()
    out = nf.drain()
    assert out == {("192.168.50.11", "140.112.1.1", False): [10, 100], ("192.168.50.11", "203.0.113.9", True): [0, 200]}


def test_hosts_render_apply_and_validate(tmp_path):
    from netmeter import hosts

    assert hosts.valid("192.168.50.2", "ccu-nas.tailabc12.ts.net", NETS)
    assert not hosts.valid("8.8.8.8", "ccu-nas.tailabc12.ts.net", NETS)            # 不在實驗室網段
    assert not hosts.valid("192.168.50.2", "www.google.com", NETS)                 # 只接受 *.ts.net
    assert not hosts.valid("192.168.50.2", "x.ts.net\n1.2.3.4 evil.com", NETS)
    assert not hosts.valid("192.168.50.2", "ccu-nas.tailabc12.ts.net\n", NETS)
    p = tmp_path / "hosts"
    orig = "# Copyright\r\n127.0.0.1 localhost\r\n10.0.0.9 printer\r\n"
    p.write_bytes(orig.encode())
    assert hosts.apply([("192.168.50.2", "ccu-nas.tailabc12.ts.net")], p)
    t = p.read_bytes().decode()
    assert "\r\n192.168.50.2 ccu-nas.tailabc12.ts.net\r\n" in t and t.startswith(orig) and t.count(hosts.BEGIN) == 1
    assert not hosts.apply([("192.168.50.2", "ccu-nas.tailabc12.ts.net")], p)         # 一樣就不寫
    assert hosts.apply([("192.168.50.3", "ccu-nas.tailabc12.ts.net")], p) and p.read_bytes().decode().count(hosts.BEGIN) == 1
    assert hosts.apply([], p) and p.read_bytes().decode() == orig                    # 拿掉後回到原狀
    assert not hosts.apply([], p)


def test_agent_learns_https_domain_and_syncs_hosts(tmp_path, monkeypatch):
    from netmeter import hosts

    monkeypatch.setenv("QELNETMETER_HOME", str(tmp_path / "home"))
    hp = tmp_path / "hosts"
    hp.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(hosts, "hosts_path", lambda: hp)
    monkeypatch.setattr(agent.sys, "platform", "win32")
    s = Seq()
    cfg = dict(config.DEFAULTS, subnets=["192.168.50.0/24"], hubs=["http://192.168.50.2:8765"])
    ag = agent.Agent(cfg=cfg, sampler=s,
                     reporter=lambda hubs, p: (hubs[0], {"ok": True, "lan_https": {"domain": "ccu-nas.tailabc12.ts.net",
                                                                                 "url": "https://ccu-nas.tailabc12.ts.net:8443"}}))
    ag.tick()
    assert ag.send() and "192.168.50.2 ccu-nas.tailabc12.ts.net" in hp.read_text()
    assert ag.status()["lan_https"]["hosts"] is True
    assert agent.Agent(cfg=cfg, sampler=s).lan_https["domain"] == "ccu-nas.tailabc12.ts.net"     # 記住了
    s.ifs["Ethernet"]["ips"] = ["10.1.2.3"]                                                    # 離開實驗室
    ag.tick()
    ag.sync_hosts()
    assert hosts.BEGIN not in hp.read_text()
    s.ifs["Ethernet"]["ips"] = ["192.168.50.31"]
    ag.tick()
    ag.sync_hosts()
    assert hosts.BEGIN in hp.read_text()
    ag.sync_hosts(force_off=True)                                                              # 程式結束
    assert hp.read_text() == "127.0.0.1 localhost\n"


# ---- 1.0.6：閘道、另一間實驗室、華碩代讀 ----
def test_gateway_parsers():
    from netmeter import gateway

    rp = """===========================================================================
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      10.0.0.1        10.0.0.5     50
          0.0.0.0          0.0.0.0     192.168.50.1   192.168.50.31     25
"""
    assert gateway.parse_route_print(rp, ["192.168.50.31"]) == "192.168.50.1"
    assert gateway.parse_route_print(rp, []) == "192.168.50.1"            # metric 小的
    arp = "Interface: 192.168.50.31 --- 0x5\n  Internet Address      Physical Address      Type\n  192.168.50.1          a0-36-bc-1a-2b-3c     dynamic\n"
    assert gateway.parse_arp(arp, "192.168.50.1") == "A0:36:BC:1A:2B:3C"
    assert gateway.parse_arp("? (192.168.50.1) at a0:36:bc:1a:2b:3c on en0 ifscope [ethernet]", "192.168.50.1") == "A0:36:BC:1A:2B:3C"
    assert gateway.parse_arp("? (192.168.50.10) at 0:1:2:3:4:5 on en0", "192.168.50.1") == ""


def test_meter_ignores_unknown_gateway(tmp_path):
    s = Seq()
    m = Meter(tmp_path / "state.json", NETS, s)
    m.allowed_gw = {"AA:AA:AA:AA:AA:AA"}
    m.gw_mac = "BB:BB:BB:BB:BB:BB"                       # 家裡也是 192.168.50.x，但路由器不是實驗室的
    m.step()
    s.add("Ethernet", 1000, 1000)
    assert m.step()["on"] is False and m.on_ifaces == []
    m.gw_mac = "AA:AA:AA:AA:AA:AA"
    m.step()
    s.add("Ethernet", 1000, 1000)
    assert m.step()["down"] == 1000


def test_asus_parsers_and_login():
    from netmeter import asus

    assert asus.parse_netdev({"netdev": {"INTERNET_rx": "0x00000000ab", "INTERNET_tx": "0x10"}}) == {"rx": 171, "tx": 16}
    assert asus.parse_netdev({"netdev": {"WAN_rx": "100", "WAN_tx": "5"}}) == {"rx": 100, "tx": 5}
    cl = asus.parse_clients({"get_clientlist": {"maclist": ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"],
                                                "aa:bb:cc:dd:ee:ff": {"ip": "192.168.50.20", "nickName": "PC", "isWL": "2", "isOnline": "1"},
                                                "11:22:33:44:55:66": {"ip": "192.168.50.30", "isOnline": "0"}}})
    assert cl == [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.50.20", "name": "PC", "type": "5GHz", "dtype": "", "vendor": ""}]
    assert asus._loose_json('{"a": 1,}') == {"a": 1}

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []

    class Op:
        def open(self, req, timeout=0):
            calls.append((req.full_url, req.headers.get("Cookie"), req.data))
            if req.full_url.endswith("/login.cgi"):
                return Resp('{"asus_token": "TOK"}')
            if "netdev" in req.full_url:
                return Resp('{"netdev": {"INTERNET_rx": "0x400", "INTERNET_tx": "0x200",}}')
            if "clientlist" in req.full_url:
                return Resp('{"get_clientlist": {"maclist": []}}')
            return Resp('{"productid": "RT-AX88U"}')
    r = asus.Asus("192.168.1.1", "admin", "pw", opener=Op()).read()
    assert r == {"ok": True, "rx": 1024, "tx": 512, "clients": [], "model": "RT-AX88U"}
    assert b"login_authorization=YWRtaW46cHc%3D" == calls[0][2] and calls[1][1] == "asus_token=TOK"
    with pytest.raises(asus.AsusError):
        asus.Asus("bad host!", "a", "b")


def test_agent_hub_cfg_collector_and_hosts_only_via_lan(tmp_path, monkeypatch):
    from netmeter import asus, hosts

    monkeypatch.setenv("QELNETMETER_HOME", str(tmp_path / "home"))
    hp = tmp_path / "hosts"
    hp.write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(hosts, "hosts_path", lambda: hp)
    monkeypatch.setattr(agent.sys, "platform", "win32")
    monkeypatch.setattr(agent.gateway, "detect", lambda prefer=None: {"ip": "192.168.50.1", "mac": "AA:AA:AA:AA:AA:AA"})
    class FakeAsus:                                       # 真的 Asus 在假裝成 Windows 時建不出 opener
        def __init__(self, host, user, password, port=0, use_ssl=False):
            assert (host, user, password) == ("10.20.0.1", "admin", "pw")

        def read(self):
            return {"ok": True, "rx": 5, "tx": 6, "clients": [], "model": "X"}
    monkeypatch.setattr(asus, "Asus", FakeAsus)
    sent = []
    used = ["http://192.168.50.2:8765"]
    resp = {"ok": True, "subnets": ["192.168.50.0/24", "10.20.0.0/16"], "gateways": ["aa:aa:aa:aa:aa:aa"],
            "lan_https": {"domain": "ccu-nas.tailabc12.ts.net", "url": "https://ccu-nas.tailabc12.ts.net:8443"},
            "collect": {"site": "lab2", "kind": "asus", "host": "10.20.0.1", "user": "admin", "password": "pw"}}

    def rep(hubs, p):
        sent.append(p)
        return used[0], resp
    s = Seq()
    cfg = dict(config.DEFAULTS, subnets=["192.168.50.0/24"], hubs=["http://192.168.50.2:8765", "http://100.64.0.2:8765"],
               reg_port=0)
    ag = agent.Agent(cfg=cfg, sampler=s, reporter=rep)
    ag.tick()
    assert ag.send() and sent[0]["gw"]["mac"] == "AA:AA:AA:AA:AA:AA"
    assert any(str(n) == "10.20.0.0/16" for n in ag.meter.nets) and ag.meter.allowed_gw == {"AA:AA:AA:AA:AA:AA"}
    assert ag.collect["host"] == "10.20.0.1"
    assert "ccu-nas.tailabc12.ts.net" in hp.read_text()
    ag.send()
    assert sent[1]["site_router"]["rx"] == 5 and sent[1]["site_router"]["site"] == "lab2"
    assert ag.status()["collect"]["ok"] is True
    assert agent.Agent(cfg=cfg, sampler=s).meter.allowed_gw == {"AA:AA:AA:AA:AA:AA"}         # 記住了
    # 改經 VPN 回報（另一間實驗室也是 192.168.50.x）→ 不把 Hub 網址指到 192.168.50.2
    used[0] = "http://100.64.0.2:8765"
    ag.send()
    ag.sync_hosts()
    assert hosts.BEGIN not in hp.read_text()
    # 不在實驗室網段：只有 heartbeat 才送
    s.ifs["Ethernet"]["ips"] = ["172.16.0.9"]
    ag.tick()
    n = len(sent)
    assert not ag.send() and ag.send(heartbeat=True) and len(sent) == n + 1 and sent[-1]["on_net"] is False


# ---- 1.0.7：在別間實驗室（不同網段）經 VPN 回報 ----
def test_default_vpn_hub_and_last_good_first(tmp_path, monkeypatch):
    monkeypatch.setenv("QELNETMETER_HOME", str(tmp_path / "home"))
    config.save(dict(config.DEFAULTS, hubs=["http://192.168.50.2:8765"]))        # 1.0.6 以前存的設定
    cfg = config.load()
    assert cfg["hubs"] == ["http://192.168.50.2:8765", "http://100.114.33.20:8765"]
    tried = []

    def rep(hubs, p):
        tried.append(list(hubs))
        return "http://100.114.33.20:8765", {"ok": True}
    monkeypatch.setattr(agent.gateway, "detect", lambda prefer=None: {})
    ag = agent.Agent(cfg=cfg, sampler=Seq(), reporter=rep)
    ag.tick()
    assert ag.send(heartbeat=True) and ag.send(heartbeat=True)
    assert tried[0][0] == "http://192.168.50.2:8765" and tried[1][0] == "http://100.114.33.20:8765"


def test_gw_unknown_status(tmp_path, monkeypatch):
    monkeypatch.setenv("QELNETMETER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(agent.gateway, "detect", lambda prefer=None: {"ip": "192.168.10.1", "mac": "CC:CC:CC:CC:CC:CC"})
    s = Seq()
    s.ifs["Ethernet"]["ips"] = ["192.168.10.23"]
    ag = agent.Agent(cfg=dict(config.DEFAULTS), sampler=s, reporter=lambda h, p: (h[0], {
        "subnets": ["192.168.50.0/24", "192.168.10.0/24"], "gateways": ["AA:AA:AA:AA:AA:AA"]}))
    ag.tick()
    assert ag.send(heartbeat=True)
    ag.tick()
    st = ag.status()
    assert st["on_net"] is False and st["gw_unknown"] is True and "192.168.10.0/24" in st["subnets"]
    ag.reporter = lambda h, p: (h[0], {"subnets": ["192.168.50.0/24", "192.168.10.0/24"],
                                       "gateways": ["AA:AA:AA:AA:AA:AA", "CC:CC:CC:CC:CC:CC"]})
    ag.send(heartbeat=True)
    ag.tick()
    s.add("Ethernet", 1000, 1000)
    assert ag.tick()["on"] is True and ag.status()["gw_unknown"] is False


# ---- 1.0.8：系統排程看得到、Hub 有新版馬上更新 ----
def test_service_marker_and_task_sddl(tmp_path, monkeypatch):
    from netmeter import install

    monkeypatch.setenv("QELNETMETER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(install.sys, "platform", "win32")
    assert "<SecurityDescriptor>D:(A;;FA;;;BA)(A;;FA;;;SY)(A;;GRGX;;;AU)</SecurityDescriptor>" in install.task_xml(tmp_path / "x.exe")
    assert "SecurityDescriptor" not in install.task_xml(tmp_path / "x.exe", "")
    calls = []

    class R:
        def __init__(self, code):
            self.returncode, self.stdout, self.stderr = code, "", "bad"

    def fake(*args):
        calls.append(args)
        if args[0] == "/Create":
            xml = open(args[4], encoding="utf-16").read()
            return R(1 if "SecurityDescriptor" in xml else 0)          # 這版 Windows 不收 → 不帶再試
        return R(1)                                                      # 一般使用者查不到排程
    monkeypatch.setattr(install, "_schtasks", fake)
    monkeypatch.setattr(install, "_remove_run_key", lambda: None)
    install._SVC_CACHE.clear()
    assert not install.service_installed()
    install.install_service(tmp_path / "x.exe")
    assert [a[0] for a in calls].count("/Create") == 2
    install._SVC_CACHE.clear()
    assert install.service_installed()                                   # 靠安裝時留的記號
    install.uninstall_service()
    install._SVC_CACHE.clear()
    assert not install.service_installed()


def test_hub_latest_triggers_update_offnet(tmp_path, monkeypatch):
    monkeypatch.setenv("QELNETMETER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(agent.gateway, "detect", lambda prefer=None: {})
    s = Seq()
    s.ifs["Ethernet"]["ips"] = ["172.16.0.9"]                            # 不在實驗室網段（例如別間實驗室）
    ag = agent.Agent(cfg=dict(config.DEFAULTS), sampler=s, reporter=lambda h, p: (h[0], {"latest": "99.0.0"}))
    ag.tick()
    assert not ag.update_now.is_set()
    ag.send(heartbeat=True)
    assert ag.update_now.is_set()
    monkeypatch.setattr(agent.sys, "platform", "darwin")
    monkeypatch.setattr(agent.hub, "latest", lambda h, plat: {"version": "99.0.0", "url": h + "/x.dmg", "name": "x.dmg"})
    info = ag.check_update()                                             # 1.0.7 以前不在實驗室網段就不查
    assert info and ag.status()["update"]["version"] == "99.0.0"
    ag.update_now.clear()
    ag.send(heartbeat=True)
    assert not ag.update_now.is_set()                                    # 已經知道這一版 → 不重複叫


# ---- 1.0.9：Traffic Analyzer、家長電腦控制停網、設備登記頁 ----
def test_parse_js_array_and_plan_block():
    from netmeter import asus

    js = "var array_statistics = new Array();\narray_statistics = [['AA:BB:CC:DD:EE:01', 10, 20],['YouTube',3,4],];"
    assert asus.parse_js_array(js) == [["AA:BB:CC:DD:EE:01", 10, 20], ["YouTube", 3, 4]]
    assert asus.parse_js_array("var array_statistics = new Array();\narray_statistics = new Array();") == []
    nv = {"MULTIFILTER_ALL": "0", "MULTIFILTER_ENABLE": "1", "MULTIFILTER_MAC": "11:11:11:11:11:11",
          "MULTIFILTER_DEVICENAME": "kid", "MULTIFILTER_MACFILTER_DAYTIME_V2": "W03E21000700"}
    p = asus.plan_block(nv, ["aa:aa:aa:aa:aa:aa"])
    assert p["MULTIFILTER_MAC"] == "11:11:11:11:11:11>AA:AA:AA:AA:AA:AA" and p["MULTIFILTER_ENABLE"] == "1>2"
    assert p["MULTIFILTER_DEVICENAME"] == "kid>" + asus.TAG and p["MULTIFILTER_MACFILTER_DAYTIME_V2"] == "W03E21000700>"
    assert p["MULTIFILTER_ALL"] == "1"
    nv2 = {k: v for k, v in p.items()}
    assert asus.plan_block(nv2, ["AA:AA:AA:AA:AA:AA"]) is None                         # 已經是這樣
    q = asus.plan_block(nv2, [])                                                       # 登記了 → 拿掉我們的那條
    assert q["MULTIFILTER_MAC"] == "11:11:11:11:11:11" and q["MULTIFILTER_ENABLE"] == "1" and "MULTIFILTER_ALL" not in q
    assert asus.plan_block({"MULTIFILTER_MAC": ""}, []) is None


def test_traffic_analyzer_reads():
    from netmeter import asus

    seen = []

    class A(asus.Asus):
        def __init__(self):
            self.token = "x"

        def _req(self, path, data=None):
            seen.append(path)
            if "mode=hour" in path:
                return "array_statistics = [[100, 1000],[50, 500]];"
            if path.startswith("/getAppTraffic.asp?client=all"):
                return "array_statistics = [['aa:aa:aa:aa:aa:01', 5, 50],['aa:aa:aa:aa:aa:02', 1, 900]];"
            if "client=all&mode=detail" in path:
                return "array_statistics = [['YouTube', 3, 600],['General', 3, 350]];"
            return "array_statistics = [['Garena', 1, 800],['QUIC', 0, 100]];"
    ta = A().traffic_analyzer(1_800_000_000.0)
    assert ta["hourly"] == [[100, 1000], [50, 500]]
    assert ta["clients"][0][0] == "aa:aa:aa:aa:aa:02"                                 # 用量大的在前
    assert ta["apps_all"][0][0] == "YouTube" and ta["apps"]["AA:AA:AA:AA:AA:02"][0] == ["Garena", 1, 800]
    assert any("client=aa%3Aaa%3Aaa%3Aaa%3Aaa%3A01" in p for p in seen)


def test_collector_block_and_registration_page():
    import json as _json
    import urllib.request

    from netmeter.collector import Collector

    calls = []

    class R:
        def __init__(self, *a):
            pass

        def read(self):
            return {"ok": True, "rx": 1, "tx": 2, "model": "RT", "clients": [{"mac": "AA:AA:AA:AA:AA:09", "ip": "127.0.0.1"}]}

        def traffic_analyzer(self, now):
            return {"t": now, "hourly": [[1, 2]], "clients": [], "apps_all": [], "apps": {}}

        def set_blocked(self, macs):
            calls.append(list(macs))
            return {"ok": True, "changed": True}
    kicks = []
    c = Collector(kick=lambda: kicks.append(1), port=0, asus_factory=R)
    c.configure({"site": "lab328", "host": "192.168.10.1", "user": "u", "password": "p"})
    try:
        sr = c.read(1000.0)
        assert sr["site"] == "lab328" and sr["ta"]["hourly"] == [[1, 2]]
        assert "ta" not in c.read(1060.0)                                                # 10 分鐘讀一次
        c.on_hub({"site_block": ["aa:aa:aa:aa:aa:01"], "reg_status": {"AA:AA:AA:AA:AA:09": {"state": "pending", "left": 300}},
                  "enforce": True})
        assert calls == [["AA:AA:AA:AA:AA:01"]] and c.payload()["block_state"]["macs"] == ["AA:AA:AA:AA:AA:01"]
        c.on_hub({"site_block": ["AA:AA:AA:AA:AA:01"]})
        assert len(calls) == 1                                                           # 沒變 → 不重設
        port = c.srv.server_address[1]
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        html = op.open(f"http://127.0.0.1:{port}/", timeout=5).read().decode("utf-8")
        assert "設備登記" in html
        v = _json.loads(op.open(f"http://127.0.0.1:{port}/api/self", timeout=5).read())
        assert v["mac"] == "AA:AA:AA:AA:AA:09" and v["reg"]["state"] == "pending"
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/self", method="POST",
                                     data=_json.dumps({"name": "小明的手機", "role": "guest", "until": "2026-12-31"}).encode())
        r = _json.loads(op.open(req, timeout=5).read())
        assert r["ok"] and kicks
        regs = c.payload()["regs"]
        assert regs[0]["mac"] == "AA:AA:AA:AA:AA:09" and regs[0]["role"] == "guest"
        c.on_hub({"regs_ack": [{"id": regs[0]["id"], "ok": True}], "site_block": []})
        assert "regs" not in c.payload() and calls[-1] == []
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/api/self", method="POST", data=b'{"name": "", "role": "student"}')
        with pytest.raises(urllib.error.HTTPError):
            op.open(bad, timeout=5)
    finally:
        c.configure({})
    assert c.srv is None


# ---- 1.0.10：手動指定 IP（華碩 DHCP 保留）----
def test_plan_dhcp():
    from netmeter import asus

    nv = {"dhcp_static_x": "0", "dhcp_staticlist": "<11:11:11:11:11:11>192.168.10.5>>printer<22:22:22:22:22:22>192.168.10.11"}
    rows = asus.parse_staticlist(nv["dhcp_staticlist"])
    assert rows == [["11:11:11:11:11:11", "192.168.10.5", "", "printer"], ["22:22:22:22:22:22", "192.168.10.11", "", ""]]
    p = asus.plan_dhcp(nv, {"aa:aa:aa:aa:aa:aa": {"ip": "192.168.10.11", "name": "DESKTOP-EVEN"}}, [])
    assert p == {"dhcp_staticlist": "<11:11:11:11:11:11>192.168.10.5>>printer<AA:AA:AA:AA:AA:AA>192.168.10.11>>DESKTOP-EVEN",
                 "dhcp_static_x": "1"}                                                  # 同一個 IP 的舊保留讓出來
    nv2 = {"dhcp_static_x": "1", "dhcp_staticlist": p["dhcp_staticlist"]}
    assert asus.plan_dhcp(nv2, {"AA:AA:AA:AA:AA:AA": {"ip": "192.168.10.11", "name": "x"}}, []) is None
    q = asus.plan_dhcp(nv2, {"AA:AA:AA:AA:AA:AA": {"ip": "192.168.10.12", "name": "x"}}, ["11:11:11:11:11:11"])
    assert q == {"dhcp_staticlist": "<AA:AA:AA:AA:AA:AA>192.168.10.12>>DESKTOP-EVEN", "dhcp_static_x": "1"}
    assert asus.plan_dhcp(nv2, {}, ["AA:AA:AA:AA:AA:AA"]) == {"dhcp_staticlist": "<11:11:11:11:11:11>192.168.10.5>>printer"}


def test_collector_dhcp():
    from netmeter.collector import Collector

    calls = []

    class R:
        def __init__(self, *a):
            pass

        def read(self):
            return {"ok": True, "rx": 1, "tx": 2, "clients": []}

        def set_blocked(self, macs):
            return {"ok": True}

        def set_reservations(self, want, remove):
            calls.append((want, remove))
            return {"ok": True, "changed": True, "list": [[m, v["ip"]] for m, v in want.items()]}
    c = Collector(port=0, asus_factory=R)
    c.cfg = {"site": "lab328", "host": "192.168.10.1"}
    c.on_hub({"site_dhcp": {"set": {"aa:aa:aa:aa:aa:aa": {"ip": "192.168.10.11", "name": "PC"}}, "remove": []}})
    assert calls == [({"AA:AA:AA:AA:AA:AA": {"ip": "192.168.10.11", "name": "PC"}}, [])]
    assert c.payload()["dhcp_state"]["list"] == [["AA:AA:AA:AA:AA:AA", "192.168.10.11"]]
    c.on_hub({"site_dhcp": {"set": {"AA:AA:AA:AA:AA:AA": {"ip": "192.168.10.11", "name": "PC"}}, "remove": []}})
    assert len(calls) == 1                                                              # 沒變 → 不重設
