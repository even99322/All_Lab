"""1.0.41：NAS 上誰在用網路——網卡、容器、每條連線 → 程式（記帳容器讀 /proc）。"""
from __future__ import annotations

import json

from labhub import nastop
from labhub import netusage as nu

CT = """ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.50.2 dst=203.0.113.5 sport=40000 dport=443 packets=10 bytes=2000 src=203.0.113.5 dst=192.168.50.2 sport=443 dport=40000 packets=900 bytes=9000000 [ASSURED] mark=0 use=1
ipv4     2 tcp      6 431999 ESTABLISHED src=192.168.50.11 dst=192.168.50.2 sport=50000 dport=5001 packets=5 bytes=300000 src=192.168.50.2 dst=192.168.50.11 sport=5001 dport=50000 packets=5 bytes=5000 [ASSURED] mark=0 use=1
ipv4     2 udp      17 29 src=172.17.0.5 dst=198.51.100.7 sport=6881 dport=51413 packets=3 bytes=100 src=198.51.100.7 dst=192.168.50.2 sport=51413 dport=6881 packets=3 bytes=700000 mark=0 use=1
ipv4     2 icmp     1 29 src=192.168.50.2 dst=8.8.8.8 type=8 code=0 id=1 packets=1 bytes=84 src=8.8.8.8 dst=192.168.50.2 type=0 code=0 id=1 packets=1 bytes=84 mark=0 use=1
"""


def test_parsers(tmp_path):
    ct = nastop.parse_conntrack(CT)
    assert len(ct) == 3 and ct[("tcp", "192.168.50.2", 40000, "203.0.113.5", 443)][:2] == (2000, 9000000)
    assert nastop._hexaddr("0232A8C0") == "192.168.50.2"
    assert nastop._hexaddr("0000000000000000FFFF00000232A8C0") == "192.168.50.2"
    (tmp_path / "tcp").write_text("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
                                  "   0: 0232A8C0:9C40 0571CBCB:01BB 01 00000000:00000000 00:00000000 00000000  1026        0 4242 1\n")
    s = nastop.read_sockets(tmp_path)
    assert s[("tcp", "192.168.50.2", 40000, "203.203.113.5", 443)] == 4242
    (tmp_path / "dev").write_text("Inter-|   Receive\n face |bytes\n    lo: 5 0 0 0 0 0 0 0 5 0 0 0 0 0 0 0\n"
                                  "  eth0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0\n")
    assert nastop.read_netdev(tmp_path / "dev") == {"eth0": (1000, 2000)}


def test_hub_aggregates_top(tmp_path):
    class Clock:
        t = 1_762_660_800.0

        def __call__(self):
            return self.t
    clk = Clock()
    st = nu.NetUsage(tmp_path, now=clk)
    p = tmp_path / "netacct_top.json"

    def put(t, eth_rx, flows, nsrx):
        p.write_text(json.dumps({"t": t, "ifaces": {"ovs_eth0": [eth_rx, 1000], "docker0": [0, 0]},
                                 "netns": [{"pid": 9, "comm": "qbittorrent", "cmd": "qbittorrent-nox", "container": "abc123def456",
                                            "ips": ["172.17.0.5"], "rx": nsrx, "tx": 10}],
                                 "flows": flows, "conntrack": {"ok": True, "n": 3}}))
    put(clk.t, 0, [], 0)
    st._top_step(p)
    clk.t += 20
    put(clk.t, 60_000_000, [
        {"proto": "udp", "local": "172.17.0.5", "lport": 6881, "remote": "45.33.10.7", "rport": 51413, "rx": 50_000_000,
         "tx": 100, "out": True, "proc": "qbittorrent", "container": "abc123def456"},
        {"proto": "tcp", "local": "192.168.50.2", "lport": 5001, "remote": "192.168.50.11", "rport": 50000, "rx": 300000,
         "tx": 5000, "out": False, "proc": "nginx", "pid": 77, "cmd": "nginx: worker"}], 50_000_000)
    st._top_step(p)
    v = st.nastop_view(600)
    assert v["ifaces"][0]["name"] == "ovs_eth0" and v["ifaces"][0]["rx"] == 60_000_000
    assert v["ifaces"][0]["rate"]["rx"] == 3_000_000
    assert v["procs"][0]["name"] == "容器 abc123def456" and v["procs"][0]["wan_rx"] == 50_000_000
    assert v["remotes"][0]["where"] == "wan" and v["remotes"][1]["where"] == "lan" and v["remotes"][1]["dir"] == "連進 NAS"
    assert v["containers"][0]["comm"] == "qbittorrent" and v["containers"][0]["rx"] == 50_000_000
    assert any(s["key"] == "nif:ovs_eth0" for s in st.recent_view(600, "")["sources"])
    clk.t += 4000                                                       # 一小時前的連線不算
    assert st.nastop_view(3600)["procs"] == []
