"""1.0.37：NAS 分流記帳（區網 / VPN / 外網）與電腦跟 NAS 之間的流量。"""
from __future__ import annotations

import json

from labhub import netacct as na
from labhub import netusage as nu


class Clock:
    def __init__(self, t=1_762_660_800.0):
        self.t = t

    def __call__(self):
        return self.t


def test_wanted_rules_and_parse():
    r = na.wanted_rules(["ovs_eth0"], [], ["192.168.50.0/24"], ["192.168.50.11"], v6=False)
    names = [n for n, _ in r[na.CHAIN_IN]]
    assert names == ["lan", "wg_lan", "vpn100", "wg", "all", "h:192.168.50.11"]
    assert r[na.CHAIN_OUT][0][1] == ["-o", "ovs_eth0", "-d", "192.168.50.0/24"]
    assert ["-i", "ovs_eth0", "-p", "udp", "--dport", "41641"] in [a for _, a in r[na.CHAIN_IN]]
    r6 = na.wanted_rules(["eth0"], ["tailscale0"], ["192.168.50.0/24"], [], v6=True)
    assert [n for n, _ in r6[na.CHAIN_IN]] == ["lan", "wg_lan", "lan", "wg_lan", "wg", "all", "tun"]
    ext, tun = na.pick_ifaces({"ovs_eth0": "192.168.50.2", "docker0": "172.17.0.1", "eth1": None,
                               "tailscale0": "100.114.33.20"}, ["192.168.50.0/24"], "ovs_eth0")
    assert ext == ["ovs_eth0"] and tun == ["tailscale0"]
    assert na.pick_ifaces({"eth0": "10.0.0.2"}, ["192.168.50.0/24"], "eth0") == (["eth0"], [])


class FakeIpt:
    """假的 iptables：記住鏈與規則，-nvxL 回傳設定好的計數。"""

    def __init__(self):
        self.chains = {"INPUT": [], "OUTPUT": [], "FORWARD": []}
        self.bytes = {}

    def __call__(self, cmd):
        a = cmd[cmd.index("-w") + 1:] if "-w" in cmd else cmd[1:]
        op, rest = a[0], a[1:]
        if op == "-nL":
            return (0, "") if rest[0] in self.chains else (1, "No chain")
        if op == "-N":
            self.chains[rest[0]] = []
            return 0, ""
        if op == "-F":
            self.chains[rest[0]] = []
            return 0, ""
        if op == "-X":
            self.chains.pop(rest[0], None)
            return 0, ""
        if op == "-A":
            self.chains[rest[0]].append(rest[1:])
            return 0, ""
        if op == "-S":
            return 0, "\n".join(["-N " + rest[0]] + ["-A " + rest[0] + " " + " ".join(x) for x in self.chains[rest[0]]])
        if op == "-C":
            return (0, "") if ["-j", rest[2]] in [x for x in self.chains[rest[0]]] else (1, "")
        if op == "-I":
            self.chains[rest[0]].insert(0, rest[2:])
            return 0, ""
        if op == "-D":
            self.chains[rest[0]].remove(rest[1:])
            return 0, ""
        if op == "-nvxL":
            ch = rest[0]
            lines = [f"Chain {ch} (2 references)", "    pkts      bytes target     prot opt in     out     source               destination"]
            for i, r in enumerate(self.chains[ch]):
                lines.append(f"       1 {self.bytes.get((ch, i), 0)}            all  --  *      *       0.0.0.0/0            0.0.0.0/0")
            return 0, "\n".join(lines)
        return 1, "?"


def test_accounting_ensure_read_remove(tmp_path):
    f = FakeIpt()
    acc = na.Accounting(["iptables", "-w"], [], run=f, state=tmp_path / "st.json")
    rules = na.wanted_rules(["eth0"], [], ["192.168.50.0/24"], [], v6=False)
    assert acc.ensure(4, rules) is True
    assert ["-j", na.CHAIN_IN] in f.chains["INPUT"] and ["-j", na.CHAIN_OUT] in f.chains["FORWARD"]
    f.bytes[(na.CHAIN_IN, 0)] = 1000                       # lan
    f.bytes[(na.CHAIN_IN, 4)] = 5000                       # all
    got = acc.read(4, rules)
    assert got["in"]["lan"] == 1000 and got["in"]["all"] == 5000
    assert acc.ensure(4, rules) is False                     # 沒變不重建
    assert na.Accounting(["iptables", "-w"], [], run=f, state=tmp_path / "st.json").ensure(4, rules) is False
    f.chains["INPUT"].clear()                                # DSM 防火牆重新載入 → 跳轉補回來
    acc.ensure(4, rules)
    assert ["-j", na.CHAIN_IN] in f.chains["INPUT"]
    acc.remove()
    assert na.CHAIN_IN not in f.chains and ["-j", na.CHAIN_IN] not in f.chains["INPUT"]


def _acct(t, lan_in, wgl_in, wg_in, all_in, lan_out, wgl_out, wg_out, all_out, host=(0, 0)):
    c = {"in": {"lan": lan_in, "wg_lan": wgl_in, "vpn100": 0, "wg": wg_in, "all": all_in, "h:192.168.50.11": host[0]},
         "out": {"lan": lan_out, "wg_lan": wgl_out, "vpn100": 0, "wg": wg_out, "all": all_out, "h:192.168.50.11": host[1]}}
    return {"t": t, "ok": True, "mode": "nsenter", "ifaces": ["ovs_eth0"], "v4": c}


def test_hub_splits_nas_traffic_and_pc(tmp_path):
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    st.report_device({"id": "nm-abc12345", "host": "PC1", "ip": "192.168.50.11",
                      "days": {st.today(): {"up": 10_000, "down": 900_000, "vpn_up": 100, "vpn_down": 200}}})
    p = tmp_path / "netacct.json"
    assert st.poll_netacct(p)["ok"] is False                       # 還沒有記帳資料
    assert json.loads((tmp_path / "netacct_hosts.json").read_text())["hosts"] == ["192.168.50.11"]
    p.write_text(json.dumps(_acct(clk.t, 0, 0, 0, 0, 0, 0, 0, 0)))
    assert st.poll_netacct(p)["ok"]
    clk.t += 20
    # NAS 收到：區網 1000（其中 VPN 100）、外網直接 3000、VPN 經外網 400；送出：區網 2000、外網 500
    p.write_text(json.dumps(_acct(clk.t, 1000, 100, 500, 4400, 2000, 0, 0, 2500, host=(300, 700))))
    st.poll_netacct(p)
    day = st.nasdir["days"][st.today()]
    assert day["lan"] == {"up": 2000, "down": 900} and day["vpn"] == {"up": 0, "down": 500}
    assert day["wan"] == {"up": 500, "down": 3000}
    pc = st.pcs["nm-abc12345"]
    assert pc["nas_days"][st.today()] == {"up": 300, "down": 700}           # 電腦上傳到 NAS 300、從 NAS 下載 700
    v = st.view(7)
    pcv = v["pcs"][0]
    assert pcv["vpn_month"] == {"up": 100, "down": 200} and pcv["nas_month"]["down"] == 700
    assert v["nasdir"]["days"][st.today()]["wan"]["down"] == 3000
    # DERP 中繼（Tailscale 計數）從外網移到 VPN
    st.ts["days"][st.today()] = {"relay": {"up": 100, "down": 1000}}
    nd = st.view(7)["nasdir"]["days"][st.today()]
    assert nd["wan"] == {"up": 400, "down": 2000} and nd["vpn"] == {"up": 100, "down": 1500}
    rv = st.recent_view(600, "nasdir:wan")
    keys = {s["key"]: s for s in rv["sources"]}
    assert keys["nasdir:lan"]["down"] == 900 and keys["nasdir:wan"]["down"] == 3000
    assert keys["pc:nm-abc12345"]["nas"] == {"up": 300, "down": 700}
    # 規則重建（計數歸零）：從 0 開始算，不會變負的
    clk.t += 20
    p.write_text(json.dumps(_acct(clk.t, 50, 0, 0, 60, 0, 0, 0, 0)))
    st.poll_netacct(p)
    assert st.nasdir["days"][st.today()]["lan"]["down"] == 950
    # 太久沒更新
    clk.t += 500
    assert "沒有更新" in st.poll_netacct(p)["error"]


def test_agent_netacct_container_spec():
    from labhub.agent import Agent, AgentConfig

    class D:
        def __init__(self):
            self.created, self.started = [], []
            self.hub = {"Id": "h1", "Config": {"Image": "python:3.12-slim", "Env": ["TZ=Asia/Taipei", "LABHUB_TOKEN=x"]},
                        "Mounts": [{"Destination": "/app/labhub", "Source": "/volume1/docker/hub/labhub"},
                                   {"Destination": "/data", "Source": "/volume1/docker/hub/data"},
                                   {"Destination": "/tailscale", "Source": "/var/packages/Tailscale/var"}]}
            self.acct = None

        def inspect(self, name):
            return self.hub if name == "labcontrol-hub" else self.acct

        def req(self, method, path, body=None, timeout=60):
            self.created.append((path, body))
            self.acct = {"Id": "a1", "State": {"Running": False}}
            return 201, {"Id": "a1"}

        def _ok(self, st, data, what, allow=(200, 201, 204)):
            return data

        def start(self, cid):
            self.started.append(cid)
            self.acct["State"] = {"Running": True}

    cfg = AgentConfig("/hubdir", "labcontrol-hub", "http://x", "unix:///x", "t")
    ag = Agent(cfg)
    ag.docker = D()
    assert ag.ensure_netacct() == "created"
    path, spec = ag.docker.created[0]
    assert "name=labcontrol-netacct" in path
    hc = spec["HostConfig"]
    assert hc["Privileged"] and hc["NetworkMode"] == "host" and hc["PidMode"] == "host"
    assert sorted(hc["Binds"]) == ["/volume1/docker/hub/data:/data", "/volume1/docker/hub/labhub:/app/labhub:ro"]
    assert spec["Cmd"][-2:] == ["--data", "/data"] and "TZ=Asia/Taipei" in spec["Env"]
    assert not any(e.startswith("LABHUB_TOKEN") for e in spec["Env"])         # 不需要 Hub 的 token
    assert ag.ensure_netacct() == "ok"
    ag.docker.acct["State"] = {"Running": False}
    assert ag.ensure_netacct() == "started"
    cfg.netacct = False
    assert ag.ensure_netacct() == "off"
