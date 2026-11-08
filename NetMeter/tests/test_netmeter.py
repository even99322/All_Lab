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
    assert m.step() == {"up": 0, "down": 0, "on": True}          # 第一次：基準
    s.add("Ethernet", 3000, 400)
    s.add("Wi-Fi", 9999, 999, dt=0)                              # 不在實驗室網段的網卡不算
    assert m.step() == {"up": 400, "down": 3000, "on": True}
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
