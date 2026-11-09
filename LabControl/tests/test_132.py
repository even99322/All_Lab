"""1.0.32：對外流量檢查——Tailscale 路徑統計、Hub 從 GitHub 下載的量與失敗不重抓、節點改回內網。"""
from __future__ import annotations

import threading

from labhub import netusage as nu


class Clock:
    def __init__(self, t=1_762_660_800.0):        # 2025-11-09 12:00 台北
        self.t = t

    def __call__(self):
        return self.t


def _status(rx_lan, tx_lan, rx_relay, tx_relay, exit_node=False, cur_relay=""):
    return {"Version": "1.90.0-t", "Self": {"HostName": "nas", "AllowedIPs": ["100.114.33.20/32"] + (["0.0.0.0/0", "::/0"] if exit_node else []),
                                           "PrimaryRoutes": ["192.168.50.0/24"] if exit_node else None},
            "Peer": {"k1": {"ID": "n1", "HostName": "LAB-1", "OS": "windows", "TailscaleIPs": ["100.64.0.5"], "Online": True,
                            "CurAddr": "192.168.50.11:41641", "Relay": "tok", "RxBytes": rx_lan, "TxBytes": tx_lan},
                     "k2": {"ID": "n2", "HostName": "phone", "OS": "iOS", "TailscaleIPs": ["100.64.0.9"], "Online": True,
                            "CurAddr": cur_relay, "Relay": "tok", "RxBytes": rx_relay, "TxBytes": tx_relay},
                     "k3": {"ID": "n3", "HostName": "old", "OS": "linux", "Online": False, "RxBytes": 0, "TxBytes": 0}}}


def test_ts_path_classification():
    assert nu.ts_path({"CurAddr": "192.168.50.11:41641"}) == "lan"
    assert nu.ts_path({"CurAddr": "140.123.4.5:41641"}) == "wan"
    assert nu.ts_path({"CurAddr": "[2001:4860::8888]:41641"}) == "wan"
    assert nu.ts_path({"CurAddr": "", "Relay": "tok"}) == "relay"
    assert nu.ts_path({"CurAddr": "100.70.1.2:41641"}) == "wan"          # CGNAT 不是區網


def test_poll_tailscale_counts_by_path(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    r = st.poll_tailscale(_status(1000, 2000, 10, 20))
    assert r["ok"] and not r["exit_node"] and r["online"] == {"lan": 1, "wan": 0, "relay": 1}
    clk.t += 60
    st.poll_tailscale(_status(1000 + 5 * 2**30, 2000 + 2**30, 10 + 3 * 2**20, 20 + 7 * 2**20))
    v = st.view(7)
    t = v["tailscale"]
    day = v["today"]
    assert t["by_path"][day]["lan"] == {"up": 2**30, "down": 5 * 2**30}
    assert t["by_path"][day]["relay"] == {"up": 7 * 2**20, "down": 3 * 2**20}
    assert t["ext"][day] == {"up": 7 * 2**20, "down": 3 * 2**20}            # 只有中繼那台算經外網
    names = [p["name"] for p in t["peers"]]
    assert names == ["LAB-1", "phone"]                                       # 沒流量又離線的不列
    phone = t["peers"][1]
    assert phone["path"] == "relay" and phone["ext"][day] == 10 * 2**20
    # 計數歸零（tailscaled 重新啟動）：從 0 接著算；出口節點 → 警告
    clk.t += 60
    r = st.poll_tailscale(_status(100, 100, 50, 50, exit_node=True, cur_relay="1.2.3.4:5"))
    assert r["exit_node"] and r["routes"] == ["192.168.50.0/24"]
    assert st.view(7)["tailscale"]["by_path"][day]["lan"]["down"] == 5 * 2**30 + 100
    # 存檔後讀回
    st.save(force=True)
    st2 = nu.NetUsage(tmp_path, now=clk)
    assert st2.view(7)["tailscale"]["month"]["down"] > 5 * 2**30
    # 讀不到 socket → 狀態是錯誤，不會丟例外
    assert st2.poll_tailscale()["ok"] is False or nu.ts_socket()


def test_github_bytes_and_failed_files_not_refetched(tmp_path, monkeypatch):
    from labhub.server import Config, make_server
    from tests import fake_paperlib
    from tests.test_113 import _fake_github

    assets = {"LabControl_v1.0.40_windows-setup.exe": b"MZ" + b"x" * 3000,
              "LabControl_v1.0.40_macos-arm64.dmg": b"D" * 5000}
    tags = {"labcontrol-v1.0.40": list(assets)}
    gsrv, gurl, hits = _fake_github(assets, tags)
    monkeypatch.setenv("LABHUB_GITHUB_API", gurl)
    monkeypatch.setenv("LABHUB_GITHUB_REPO", "o/r")
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="t", paperlib_url=purl, agent_url="http://127.0.0.1:9"),
                             "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        con = state.console
        gh = con.github
        orig = con.stage_path

        def bad_stage(path, name, by):
            if name.endswith(".dmg"):
                raise ValueError("壞掉的 dmg")
            return orig(path, name, by)

        monkeypatch.setattr(con, "stage_path", bad_stage)
        gh._save_cfg(auto=False, auto_by="boss")
        job = gh.fetch("boss", wait=True, auto=True)
        assert sorted(f["status"][:1] for f in job["files"]) == ["❌", "已"]
        n1 = len([h for h in hits if h.startswith("/blob/") and h.endswith(".dmg")])
        assert n1 == 1
        down = nu.store(state).view(3)["github"]["days"]
        assert sum(v["down"] for v in down.values()) == 8002
        gh._cache = None
        job = gh.fetch("boss", wait=True, auto=True)                    # 自動模式：失敗的檔 24 小時內不重抓
        assert job["message"] == "線上都是最新版" or not any(f["name"].endswith(".dmg") for f in job["files"])
        assert len([h for h in hits if h.startswith("/blob/") and h.endswith(".dmg")]) == n1
        gh._cache = None
        gh.fetch("boss", wait=True, names=["LabControl_v1.0.40_macos-arm64.dmg"])  # 手動按：照樣會試
        assert len([h for h in hits if h.startswith("/blob/") and h.endswith(".dmg")]) == n1 + 1
    finally:
        srv.shutdown()
        psrv.shutdown()
        gsrv.shutdown()


def test_better_hub_prefers_lan(monkeypatch):
    from labcontrol.remote import hub as rh

    monkeypatch.setattr(rh, "hub_urls", lambda: ["http://lan:8765", "http://vpn:8765"])
    up = {"http://lan:8765": False}

    def ping(self):
        if self.url == "http://lan:8765" and not up[self.url]:
            raise rh.HubError("timeout")
        return {"server": "labhub", "auth": True}

    monkeypatch.setattr(rh.Hub, "ping", ping)
    vpn = rh.Hub("http://vpn:8765", "tok")
    assert rh.better_hub(vpn) is None                     # 內網還是連不到
    up["http://lan:8765"] = True
    h = rh.better_hub(vpn)
    assert h is not None and h.url == "http://lan:8765" and h.token == "tok"
    assert rh.better_hub(h) is None                       # 已經是第一優先
    assert rh.better_hub(None) is None
