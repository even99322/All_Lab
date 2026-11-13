"""1.0.37：NAS 流量分流記帳——NAS 跟「實驗室區網」「VPN（Tailscale）」「外網」各傳了多少。

Hub 跑在 Docker 容器裡，看不到 NAS 主機的流量；這個程式跑在另一個小容器「labcontrol-netacct」
（Hub 控制代理自動建立：privileged、network_mode host、pid host），在 NAS 主機的 iptables 加「只計數、不擋封包」的規則：

    QEL_ACCT_IN   ← INPUT、FORWARD 跳進來（從外部網卡進來的封包）
    QEL_ACCT_OUT  ← OUTPUT、FORWARD 跳進來（從外部網卡出去的封包）

每張外部網卡（有實驗室網段 IPv4 位址的那張，例如 ovs_eth0 / eth0 / bond0）一組規則：
    lan     對方在實驗室網段（預設 192.168.50.0/24；跟 Hub「網路用量 → 限流與壓縮設定」的區網網段一樣）
    wg      Tailscale 的 WireGuard（UDP 41641）——VPN 直連的封包
    wg_lan  其中對方在實驗室網段的（VPN 但其實走區網）
    all     這張網卡全部
另外 tailscale0（Tailscale 用 TUN 模式時才有）整張算 VPN。Hub 再算：
    區網（不含 VPN）= lan − wg_lan；外網（不含 VPN）= all − lan − (wg − wg_lan) − DERP 中繼（Tailscale 的計數）；
    VPN = wg + tun ＋ 中繼。
每台裝了流量監看的電腦（Hub 寫在 data/netacct_hosts.json）另外各一條：NAS 跟那台電腦之間傳了多少——
Hub 用它把電腦的流量分成「跟 NAS 之間（區網）」和「其餘（大多是外網）」。

每 20 秒讀一次計數寫到 data/netacct.json；規則不見了（DSM 防火牆重新載入）會自己補回去。
規則不擋任何封包（沒有 -j ACCEPT / DROP），拿掉：python -m labhub.netacct --remove。只用標準函式庫。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("labhub.netacct")

CHAIN_IN, CHAIN_OUT = "QEL_ACCT_IN", "QEL_ACCT_OUT"
JUMPS = (("INPUT", CHAIN_IN), ("FORWARD", CHAIN_IN), ("OUTPUT", CHAIN_OUT), ("FORWARD", CHAIN_OUT))
WG_PORT = 41641
EVERY = 20.0
VPN4 = "100.64.0.0/10"
LAN6 = ("fe80::/10", "fc00::/7")

Runner = Callable[[List[str]], Tuple[int, str]]


def _run(cmd: List[str]) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def find_iptables(run: Runner = _run) -> Tuple[List[str], List[str], str]:
    """(iptables 指令, ip6tables 指令, 方式)。優先用 NAS 主機自己的 iptables（nsenter 進主機的檔案系統）。"""
    for pre, mode in ((["nsenter", "-t", "1", "-m", "--"], "nsenter"), ([], "direct")):
        rc, _ = run(pre + ["iptables", "-w", "-nL", "INPUT"])
        if rc == 0:
            rc6, _ = run(pre + ["ip6tables", "-w", "-nL", "INPUT"])
            return pre + ["iptables", "-w"], (pre + ["ip6tables", "-w"]) if rc6 == 0 else [], mode
    raise RuntimeError("找不到可以用的 iptables（容器要 privileged、pid: host，NAS 上要有 iptables）")


# ---- 網卡 ----
def _ipv4_of(name: str) -> Optional[str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        r = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))    # SIOCGIFADDR
        return socket.inet_ntoa(r[20:24])
    except OSError:
        return None
    finally:
        s.close()


def interfaces(sys_net: str = "/sys/class/net") -> Dict[str, Optional[str]]:
    try:
        names = sorted(os.listdir(sys_net))
    except OSError:
        names = []
    return {n: _ipv4_of(n) for n in names if n != "lo"}


def default_iface(route: str = "/proc/net/route") -> Optional[str]:
    try:
        for line in Path(route).read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                return f[0]
    except OSError:
        pass
    return None


def pick_ifaces(ifaces: Dict[str, Optional[str]], lan: Sequence[str], default: Optional[str]) -> Tuple[List[str], List[str]]:
    """(外部網卡, tailscale 網卡)。外部 = 有實驗室網段位址的；沒有 → 預設路由那張。docker / veth / 橋接不算。"""
    nets = [ipaddress.ip_network(n, strict=False) for n in lan]
    skip = ("docker", "veth", "br-", "virbr", "lxc", "tailscale", "tun", "wg")
    ext = [n for n, ip in ifaces.items() if ip and not n.startswith(skip)
           and any(ipaddress.ip_address(ip) in net for net in nets if net.version == 4)]
    if not ext and default and not default.startswith(skip):
        ext = [default]
    tun = [n for n in ifaces if n.startswith("tailscale")]
    return sorted(ext), sorted(tun)


# ---- 規則 ----
def wanted_rules(ext: List[str], tun: List[str], lan: Sequence[str], hosts: Sequence[str], v6: bool) -> Dict[str, List[Tuple[str, List[str]]]]:
    """每條鏈要有的規則：[(名稱, iptables 參數)]；名稱相同的計數會加在一起。"""
    lan_v = [n for n in lan if ipaddress.ip_network(n, strict=False).version == (6 if v6 else 4)]
    if v6:
        lan_v = list(dict.fromkeys(lan_v + list(LAN6)))
    out: Dict[str, List[Tuple[str, List[str]]]] = {CHAIN_IN: [], CHAIN_OUT: []}
    for chain, io, peer, port in ((CHAIN_IN, "-i", "-s", "--dport"), (CHAIN_OUT, "-o", "-d", "--sport")):
        r = out[chain]
        for x in ext:
            for n in lan_v:
                r.append(("lan", [io, x, peer, n]))
                r.append(("wg_lan", [io, x, peer, n, "-p", "udp", port, str(WG_PORT)]))
            if not v6:
                r.append(("vpn100", [io, x, peer, VPN4]))
            r.append(("wg", [io, x, "-p", "udp", port, str(WG_PORT)]))
            r.append(("all", [io, x]))
            if not v6:
                for h in hosts:
                    r.append((f"h:{h}", [io, x, peer, f"{h}/32"]))
        for t in tun:
            r.append(("tun", [io, t]))
    return out


def signature(rules: Dict[str, List[Tuple[str, List[str]]]]) -> str:
    return hashlib.sha1(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:12]


class Accounting:
    def __init__(self, ipt: List[str], ip6t: List[str], run: Runner = _run, state: Optional[Path] = None) -> None:
        self.cmd = {4: ipt, 6: ip6t}
        self.run = run
        self.state = state                       # 記住上次建的規則（重新啟動容器時不用重建，計數不歸零）
        self.sig: Dict[int, str] = {}
        if state is not None:
            self.sig = {int(k): str(v) for k, v in (_read_json(state).get("sig") or {}).items()}

    def _ipt(self, v: int, *args: str) -> Tuple[int, str]:
        return self.run(self.cmd[v] + list(args))

    def ensure(self, v: int, rules: Dict[str, List[Tuple[str, List[str]]]]) -> bool:
        """鏈與跳轉在、規則跟想要的一樣；不一樣就重建（計數會歸零，Hub 會當成重新開始）。回傳有沒有重建。"""
        if not self.cmd[v]:
            return False
        rebuilt = False
        sig = signature(rules)
        for chain, rs in rules.items():
            rc, _ = self._ipt(v, "-nL", chain)
            if rc != 0:
                self._ipt(v, "-N", chain)
                rebuilt = True
            n = self._count_rules(v, chain)
            if rebuilt or self.sig.get(v) != sig or n != len(rs):
                self._ipt(v, "-F", chain)
                for _name, args in rs:
                    self._ipt(v, "-A", chain, *args)
                rebuilt = True
        for builtin, chain in JUMPS:
            if self._ipt(v, "-C", builtin, "-j", chain)[0] != 0:
                self._ipt(v, "-I", builtin, "1", "-j", chain)
        if self.sig.get(v) != sig:
            self.sig[v] = sig
            if self.state is not None:
                try:
                    self.state.write_text(json.dumps({"sig": self.sig}), encoding="utf-8")
                except OSError:
                    pass
        return rebuilt

    def _count_rules(self, v: int, chain: str) -> int:
        rc, out = self._ipt(v, "-S", chain)
        return sum(1 for line in out.splitlines() if line.startswith("-A ")) if rc == 0 else -1

    def read(self, v: int, rules: Dict[str, List[Tuple[str, List[str]]]]) -> Dict[str, Dict[str, int]]:
        """{"in": {名稱: bytes}, "out": {...}}（規則順序對應）。"""
        res: Dict[str, Dict[str, int]] = {"in": {}, "out": {}}
        for chain, key in ((CHAIN_IN, "in"), (CHAIN_OUT, "out")):
            rc, out = self._ipt(v, "-nvxL", chain)
            if rc != 0:
                raise RuntimeError(f"讀不到 {chain}：{out.strip()[:200]}")
            counts = []
            for line in out.splitlines()[2:]:
                f = line.split()
                if len(f) >= 2 and f[0].isdigit() and f[1].isdigit():
                    counts.append(int(f[1]))
            rs = rules[chain]
            if len(counts) != len(rs):
                raise RuntimeError(f"{chain} 的規則數不對（{len(counts)} / {len(rs)}）")
            for (name, _args), b in zip(rs, counts):
                res[key][name] = res[key].get(name, 0) + b
        return res

    def remove(self) -> None:
        for v in (4, 6):
            if not self.cmd[v]:
                continue
            for builtin, chain in JUMPS:
                while self._ipt(v, "-C", builtin, "-j", chain)[0] == 0:
                    if self._ipt(v, "-D", builtin, "-j", chain)[0] != 0:
                        break
            for chain in (CHAIN_IN, CHAIN_OUT):
                self._ipt(v, "-F", chain)
                self._ipt(v, "-X", chain)


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def lan_subnets(data: Path) -> List[str]:
    cfg = (_read_json(data / "traffic.json").get("cfg") or {})
    nets = []
    for s in cfg.get("lan_subnets") or ["192.168.50.0/24"]:
        try:
            nets.append(str(ipaddress.ip_network(str(s), strict=False)))
        except ValueError:
            continue
    return nets or ["192.168.50.0/24"]


def hosts(data: Path, lan: Sequence[str]) -> List[str]:
    nets = [ipaddress.ip_network(n) for n in lan]
    out = []
    for h in (_read_json(data / "netacct_hosts.json").get("hosts") or [])[:64]:
        try:
            a = ipaddress.ip_address(str(h))
        except ValueError:
            continue
        if a.version == 4 and any(a in n for n in nets if n.version == 4) and str(a) not in out:
            out.append(str(a))
    return sorted(out)


def write_status(data: Path, st: Dict[str, Any]) -> None:
    p = data / "netacct.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def once(acc: Accounting, data: Path, mode: str, ifaces_fn=interfaces, default_fn=default_iface) -> Dict[str, Any]:
    lan = lan_subnets(data)
    ext, tun = pick_ifaces(ifaces_fn(), lan, default_fn())
    if not ext:
        raise RuntimeError("找不到 NAS 接實驗室網段的網卡")
    hs = hosts(data, lan)
    st: Dict[str, Any] = {"t": time.time(), "ok": True, "mode": mode, "ifaces": ext, "tun": tun, "lan": lan,
                          "hosts": hs, "pid": os.getpid()}
    for v in (4, 6):
        if not acc.cmd[v]:
            continue
        rules = wanted_rules(ext, tun, lan, hs, v == 6)
        st[f"rebuilt{v}"] = acc.ensure(v, rules)
        st[f"v{v}"] = acc.read(v, rules)
    return st


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="NAS 流量分流記帳（區網 / VPN / 外網）")
    ap.add_argument("--data", default=os.environ.get("LABHUB_DATA", "/data"))
    ap.add_argument("--every", type=float, default=EVERY)
    ap.add_argument("--remove", action="store_true", help="拿掉記帳規則後結束")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data = Path(a.data)
    try:
        ipt, ip6t, mode = find_iptables()
    except RuntimeError as e:
        log.error("%s", e)
        if not a.remove:
            write_status(data, {"t": time.time(), "ok": False, "error": str(e)})
        return 2
    acc = Accounting(ipt, ip6t, state=data / "netacct_state.json")
    if a.remove:
        acc.remove()
        log.info("已拿掉記帳規則")
        return 0
    log.info("NAS 流量分流記帳啟動（%s）", mode)
    from . import nastop                          # 1.0.41：NAS 上誰在用網路（網卡、容器、每條連線 → 程式）

    top = nastop.Top()
    while True:
        try:
            write_status(data, once(acc, data, mode))
        except Exception as e:  # noqa: BLE001
            log.warning("記帳失敗：%s", e)
            try:
                write_status(data, {"t": time.time(), "ok": False, "error": str(e), "mode": mode})
            except OSError:
                pass
        try:
            ips = [ip for ip in interfaces().values() if ip]
            nastop.write(data, top.sample(ips))
        except Exception as e:  # noqa: BLE001
            log.warning("讀連線失敗：%s", e)
        if a.once:
            return 0
        time.sleep(max(5.0, a.every))


if __name__ == "__main__":
    sys.exit(main())
