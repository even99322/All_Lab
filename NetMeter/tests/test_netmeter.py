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


# ---- 1.0.4：內網 / 外網 / VPN；內網 https（hosts）----
def test_split_flows_and_scale():
    from netmeter.meter import scale_split, split_flows

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
    x = scale_split(4200, p, 1)                                 # 網卡計數（含標頭）依比例分
    assert x["lan"] + x["wan"] + x["tun"] == 4200 and x["wan"] == 2400 and x["tunw"] == 480
    assert scale_split(100, {"lan": [0, 0], "wan": [0, 0], "tun": [0, 0], "tunw": [0, 0]}, 0) is None


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
    assert d["lan_down"] == 1000 and d["wan_down"] == 2000 and d["lan_up"] == 200 and "tun_up" not in d
    assert m.month(s.t)["wan_down"] == 2000 and m.rate_split["wan"]["down"] == 2000 / 30
    m.save()
    assert Meter(tmp_path / "state.json", NETS, s).days[today_str(s.t)]["wan_up"] == 200


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
