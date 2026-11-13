"""1.0.41：NAS 上「誰在用網路」——記帳容器（netacct，privileged、host 網路、host pid）每 20 秒讀一次：

    網卡       /proc/1/net/dev：NAS 主機的每張網卡（含 ovs_eth*、bond、tap / vnet = 虛擬機、docker0、tailscale0）
    容器       每個網路命名空間（Docker 容器各自一個）第一個程式的 /proc/<pid>/net/dev
    連線       /proc/net/nf_conntrack（打開 nf_conntrack_acct 才有 bytes）：每條連線兩個方向的 bytes，
               跟上一次的差 = 這 20 秒傳了多少；用 /proc/net/tcp|udp 的 socket inode 對到 /proc/<pid>/fd → 哪個程式

寫到 data/netacct_top.json：{t, ifaces: {名稱: [收, 送]}（累計）, netns: [...], flows: [這次的前 200 條],
conntrack: {ok, error}}。只讀，不改任何東西（除了打開 nf_conntrack_acct 計數）。只用標準函式庫。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROC = Path("/proc")
TOP_FLOWS = 200
FD_SCAN_S = 60.0
_CT = re.compile(r"(\w+)=(\S+)")


def read_netdev(path: Path) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    try:
        lines = path.read_text().splitlines()[2:]
    except OSError:
        return out
    for line in lines:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        f = rest.split()
        if len(f) >= 9 and name.strip() != "lo":
            out[name.strip()] = (int(f[0]), int(f[8]))
    return out


def _hexaddr(h: str) -> str:
    """/proc/net/tcp 的位址（小端序 hex）→ 文字。"""
    b = bytes.fromhex(h)
    if len(b) == 4:
        return socket.inet_ntop(socket.AF_INET, b[::-1])
    b = b"".join(b[i:i + 4][::-1] for i in range(0, 16, 4))
    a = ipaddress.ip_address(b)
    return str(a.ipv4_mapped) if getattr(a, "ipv4_mapped", None) else str(a)


def read_sockets(base: Path) -> Dict[Tuple[str, str, int, str, int], int]:
    """{(proto, 本機位址, 本機埠, 對方位址, 對方埠): inode}"""
    out: Dict[Tuple[str, str, int, str, int], int] = {}
    for proto in ("tcp", "tcp6", "udp", "udp6"):
        try:
            lines = (base / proto).read_text().splitlines()[1:]
        except OSError:
            continue
        p = proto.rstrip("6")
        for line in lines:
            f = line.split()
            if len(f) < 10:
                continue
            try:
                la, lp = f[1].split(":")
                ra, rp = f[2].split(":")
                out[(p, _hexaddr(la), int(lp, 16), _hexaddr(ra), int(rp, 16))] = int(f[9])
            except (ValueError, OSError):
                continue
    return out


def parse_conntrack(text: str) -> Dict[Tuple[str, str, int, str, int], Tuple[int, int, str, int, str, int]]:
    """{(proto, 起頭 src, sport, dst, dport): (起頭送出 bytes, 回應 bytes, 回應 src, sport, 回應 dst, dport)}"""
    out = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        proto = f[2]
        if proto not in ("tcp", "udp"):
            continue
        kv = _CT.findall(line)
        src = [v for k, v in kv if k == "src"]
        dst = [v for k, v in kv if k == "dst"]
        sp = [v for k, v in kv if k == "sport"]
        dp = [v for k, v in kv if k == "dport"]
        by = [v for k, v in kv if k == "bytes"]
        if len(src) < 2 or len(dst) < 2 or len(sp) < 2 or len(dp) < 2 or len(by) < 2:
            continue
        try:
            out[(proto, src[0], int(sp[0]), dst[0], int(dp[0]))] = (int(by[0]), int(by[1]), src[1], int(sp[1]),
                                                                   dst[1], int(dp[1]))
        except ValueError:
            continue
    return out


def _comm(pid: str) -> str:
    try:
        return (PROC / pid / "comm").read_text().strip()
    except OSError:
        return ""


def _cmd(pid: str) -> str:
    try:
        return (PROC / pid / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:160]
    except OSError:
        return ""


def _container(pid: str) -> str:
    try:
        m = re.search(r"([0-9a-f]{64})", (PROC / pid / "cgroup").read_text())
        return m.group(1)[:12] if m else ""
    except OSError:
        return ""


def _fib_local(pid: str) -> List[str]:
    """這個網路命名空間自己的 IPv4 位址（/proc/<pid>/net/fib_trie 的 LOCAL）。"""
    try:
        t = (PROC / pid / "net" / "fib_trie").read_text()
    except OSError:
        return []
    ips, last = set(), ""
    for line in t.splitlines():
        m = re.search(r"--\s+(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            last = m.group(1)
        elif "/32 host LOCAL" in line and last and not last.startswith("127."):
            ips.add(last)
    return sorted(ips)


class Top:
    def __init__(self) -> None:
        self.prev: Dict[Tuple[str, str, int, str, int], Tuple[int, int]] = {}
        self.inode_pid: Dict[int, str] = {}
        self._scan_t = 0.0
        self.ct_error = ""
        self._acct_on = False

    def _enable_acct(self) -> None:
        if self._acct_on:
            return
        for p in ("/proc/sys/net/netfilter/nf_conntrack_acct",):
            try:
                if Path(p).read_text().strip() != "1":
                    Path(p).write_text("1")
                self._acct_on = True
            except OSError as e:
                self.ct_error = f"沒辦法打開連線計數（{e}）"

    def _scan_fds(self) -> None:
        m: Dict[int, str] = {}
        for d in PROC.iterdir():
            if not d.name.isdigit():
                continue
            try:
                for fd in (d / "fd").iterdir():
                    try:
                        t = os.readlink(fd)
                    except OSError:
                        continue
                    if t.startswith("socket:["):
                        m[int(t[8:-1])] = d.name
            except OSError:
                continue
        self.inode_pid = m
        self._scan_t = time.time()

    def netns(self) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """[{ns, pid, comm, container, ips, rx, tx}]、{容器 IP: 那一筆}（主機的網路命名空間不算）。"""
        try:
            host = os.readlink(PROC / "1" / "ns" / "net")
        except OSError:
            return [], {}
        seen: Dict[str, str] = {}
        for d in sorted(PROC.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
            if not d.name.isdigit():
                continue
            try:
                ns = os.readlink(d / "ns" / "net")
            except OSError:
                continue
            if ns != host and ns not in seen:
                seen[ns] = d.name
        out, by_ip = [], {}
        for ns, pid in seen.items():
            dev = read_netdev(PROC / pid / "net" / "dev")
            rx, tx = sum(v[0] for v in dev.values()), sum(v[1] for v in dev.values())
            x = {"ns": ns, "pid": int(pid), "comm": _comm(pid), "cmd": _cmd(pid), "container": _container(pid),
                 "ips": _fib_local(pid), "rx": rx, "tx": tx}
            out.append(x)
            for ip in x["ips"]:
                by_ip[ip] = x
        return out, by_ip

    def sample(self, local_ips: Optional[List[str]] = None) -> Dict[str, Any]:
        now = time.time()
        ifaces = read_netdev(PROC / "1" / "net" / "dev")
        ns_list, ns_ip = self.netns()
        res: Dict[str, Any] = {"t": now, "ifaces": {k: list(v) for k, v in ifaces.items()},
                               "netns": [{k: v for k, v in x.items() if k != "ns"} for x in ns_list]}
        self._enable_acct()
        try:
            text = Path("/proc/net/nf_conntrack").read_text()
        except OSError as e:
            res["conntrack"] = {"ok": False, "error": f"讀不到連線表（{e}）"}
            return res
        ct = parse_conntrack(text)
        if ct and all(v[0] == 0 and v[1] == 0 for v in list(ct.values())[:50]):
            res["conntrack"] = {"ok": False, "error": self.ct_error or "連線表沒有 bytes（nf_conntrack_acct 沒打開）"}
            self.prev = {}
            return res
        socks = read_sockets(PROC / "1" / "net")
        mine = set(local_ips or [])
        flows = []
        cur: Dict[Tuple[str, str, int, str, int], Tuple[int, int]] = {}
        for k, (ob, rb, rsrc, rsp, rdst, rdp) in ct.items():
            cur[k] = (ob, rb)
            p = self.prev.get(k)
            if p is None:
                d_o, d_r = ob, rb                                 # 新的連線（上次之後才開始）
            else:
                d_o, d_r = ob - p[0], rb - p[1]
                if d_o < 0 or d_r < 0:
                    d_o, d_r = ob, rb
            if d_o + d_r <= 0:
                continue
            proto, osrc, osp, odst, odp = k
            # 哪一邊是 NAS：起頭的是 NAS（或容器）→ NAS 送出 = 起頭的 bytes
            if osrc in mine or osrc in ns_ip or osrc.startswith("172.") or ipaddress.ip_address(osrc).is_loopback:
                local, lport, remote, rport, tx, rx, out_ = osrc, osp, odst, odp, d_o, d_r, True
            else:
                local, lport, remote, rport, tx, rx, out_ = odst, odp, osrc, osp, d_r, d_o, False
            flows.append({"proto": proto, "local": local, "lport": lport, "remote": remote, "rport": rport,
                          "rx": rx, "tx": tx, "out": out_, "rsrc": rsrc})
        self.prev = cur
        flows.sort(key=lambda f: -(f["rx"] + f["tx"]))
        flows = flows[:TOP_FLOWS]
        # 對到程式：主機的 socket（NAT 過的容器連線 → 那個容器）
        need = False
        for f in flows:
            c = ns_ip.get(f["local"]) or ns_ip.get(f.pop("rsrc", ""))      # 容器（NAT 進出的看回應那一邊）
            if c:
                f["proc"] = c["comm"]
                f["container"] = c["container"]
                continue
            ino = socks.get((f["proto"], f["local"], f["lport"], f["remote"], f["rport"])) or \
                socks.get((f["proto"], "0.0.0.0", f["lport"], "0.0.0.0", 0)) or \
                socks.get((f["proto"], "::", f["lport"], "::", 0)) or \
                socks.get((f["proto"], f["local"], f["lport"], "0.0.0.0", 0))
            if ino:
                f["inode"] = ino
                if ino not in self.inode_pid:
                    need = True
        if need and time.time() - self._scan_t > FD_SCAN_S / 3 or time.time() - self._scan_t > FD_SCAN_S:
            self._scan_fds()
        for f in flows:
            f.pop("rsrc", None)
            pid = self.inode_pid.get(f.pop("inode", -1))
            if pid and "proc" not in f:
                f["proc"] = _comm(pid)
                f["pid"] = int(pid)
                f["cmd"] = _cmd(pid)
        res["flows"] = flows
        res["conntrack"] = {"ok": True, "n": len(ct)}
        return res


def write(data: Path, obj: Dict[str, Any]) -> None:
    p = data / "netacct_top.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)

