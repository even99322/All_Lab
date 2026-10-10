"""儀器伺服器的「群組」：每個量測節點（電腦）下轄哪些儀器，加上「未歸屬」。

規則（每台實體儀器 = 一個 key，只出現在一個群組）：
  * Hub 記錄了歸屬（owner）→ 放在那台電腦的群組；
  * 共用網路儀器（兩台以上電腦都看得到）但還沒歸屬 → 未歸屬；
  * 只有一台電腦設定了它 → 那台電腦的群組；
  * 只被掃描偵測到、沒有任何電腦設定 → 未歸屬（標示在哪台偵測到）；
  * 這台電腦的虛擬儀器（magnet_A）→ 這台的群組。
  * instruments.yaml 用 ``node: LAB-1`` 指定給別台的（見 core/nodes.py）→ 只放在那台的群組，
    不算這台的（USB 儀器不會因為共用同一份設定檔就出現在每台電腦）。

移動（move_instrument）：
  * 移到某台電腦的群組 → 那台沒有設定就先把設定複製過去（節點 add_instrument），再請 Hub 記錄歸屬；
    其他電腦上連著的會自動中斷。
  * 移到未歸屬 → 清除歸屬。
  * USB / GPIB 儀器實體接在某台電腦：移動 = 改 instruments.yaml 的 ``node:``（標明它接在哪台），
    不會把設定複製過去。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .instruments import instrument_key, is_network_key

UNASSIGNED = "__unassigned__"


def _low(s: Optional[str]) -> str:
    return str(s or "").lower()


def _pinned_here(entry: Dict[str, Any]) -> bool:
    """節點回報的儀器設定裡 node: 指名的就是它自己。"""
    from ..core.nodes import pins

    p = {_low(x) for x in pins((entry.get("options") or {}))}
    return bool(p) and bool(p & {_low(entry.get("host")), _low(entry.get("node"))})


def build_groups(local: List[Dict[str, Any]], registry: List[Dict[str, Any]], nodes: List[Dict[str, Any]],
                 me: str, owners: Optional[Dict[str, str]] = None, shared: Optional[set] = None) -> List[Dict[str, Any]]:
    """local：這台的儀器 [{name, opts, in_file, connected, lease, status}]；registry：Hub /api/instruments。

    回傳 [{id, title, host, node, local, online, items:[…]}]；第一個是這台電腦，最後一個是未歸屬。
    """
    owners = {k: v for k, v in (owners or {}).items()}
    shared = set(shared or ())
    for r in registry:
        if r.get("owner"):
            owners.setdefault(r["key"], r["owner"])
        if r.get("shared"):
            shared.add(r["key"])
    groups: Dict[str, Dict[str, Any]] = {}

    def group(host: str) -> Dict[str, Any]:
        gid = _low(host)
        g = groups.get(gid)
        if g is None:
            node = next((n for n in nodes if _low(n.get("host")) == gid and n.get("online")), None) or \
                next((n for n in nodes if _low(n.get("host")) == gid), None)
            g = {"id": gid, "host": host, "node": node.get("name") if node else None,
                 "online": bool(node and node.get("online")), "local": gid == _low(me), "items": []}
            groups[gid] = g
        return g

    def node_group(node_name: str) -> Dict[str, Any]:
        """instruments.yaml 的 node: 名稱 → 那台電腦的群組（節點不在 Hub 上時用名稱建一個離線群組）。"""
        n = next((x for x in nodes if _low(x.get("name")) == _low(node_name) and x.get("online")), None) or \
            next((x for x in nodes if _low(x.get("name")) == _low(node_name)), None)
        if n is not None and n.get("host"):
            return group(n["host"])
        if _low(node_name) in groups:
            return groups[_low(node_name)]
        g = {"id": _low(node_name), "host": node_name, "node": node_name, "online": False, "local": False,
             "items": []}
        groups[g["id"]] = g
        return g

    group(me)
    for n in nodes:                                      # 在線的量測節點都列出（空的也可以把儀器拖過去）
        if n.get("online") and n.get("host"):
            group(n["host"])
    unassigned: List[Dict[str, Any]] = []
    local_keys = set()
    pinned_away: List[Dict[str, Any]] = []
    for e in local:
        opts = e.get("opts") or {}
        key = instrument_key(str(opts.get("address") or ""), opts, me)
        if e.get("foreign"):
            pinned_away.append({**e, "key": key})       # 設定檔指定給別台：最後放到那台的群組
            continue
        it = {"key": key, "name": e["name"], "host": me, "local": True, "configured": True,
              "network": is_network_key(key), "driver": opts.get("driver", ""),
              "address": str(opts.get("address") or "") or "、".join(opts.get("sources") or []),
              "label": opts.get("label", ""), "idn": e.get("idn", ""), "connected": bool(e.get("connected")),
              "lease": e.get("lease"), "status": e.get("status", ""), "options": opts,
              "in_file": e.get("in_file", True), "virtual": bool(opts.get("sources")),
              "enabled": opts.get("enabled", True) is not False, "hosts": [me]}
        if key:
            local_keys.add(key)
        own = owners.get(key) if key else None
        if own and _low(own) != _low(me):
            continue                                     # 歸別台：顯示在那台的群組
        if key and not own and key in shared:
            it["shared"] = True
            unassigned.append(it)
            continue
        it["owned"] = bool(own)
        group(me)["items"].append(it)

    pinned_keys = {e["key"]: e["foreign"] for e in pinned_away if e.get("key")}
    for r in registry:
        key = r["key"]
        entries = r.get("entries") or []
        own = owners.get(key)
        hosts = [e["host"] for e in entries]
        if own:
            target = own
        elif key in pinned_keys:
            target = node_group(pinned_keys[key])["host"]
        elif key in shared:
            target = None
        else:
            # USB / GPIB 只可能接在一台：優先連線中的、再來是設定檔有 node: 指定的那台
            conf = sorted((e for e in entries if e.get("configured")),
                          key=lambda e: (not e.get("connected"), not _pinned_here(e)))
            target = conf[0]["host"] if conf else None
        if target is not None and _low(target) == _low(me) and key in local_keys:
            continue                                     # 這台的：上面已用本機資料列出
        if target is None and key in local_keys:
            continue                                     # 已經以本機資料放在未歸屬
        src = next((e for e in entries if target and _low(e["host"]) == _low(target)), None)
        conf_any = next((e for e in entries if e.get("configured")), None)
        e = src or conf_any or (entries[0] if entries else {})
        it = {"key": key, "name": e.get("name") or r.get("model") or key, "host": e.get("host"), "local": False,
              "configured": bool(src and src.get("configured")), "network": bool(r.get("network")),
              "driver": e.get("driver") or "", "address": e.get("address") or "", "label": e.get("label") or "",
              "idn": r.get("idn") or e.get("idn", ""), "connected": bool(src and src.get("connected")),
              "lease": src.get("lease") if src else None, "online": bool(src and src.get("online")),
              "options": (src or conf_any or {}).get("options"), "hosts": hosts,
              "connected_on": r.get("connected_on") or [], "shared": key in shared, "owned": bool(own),
              "detected_only": not any(x.get("configured") for x in entries), "level": e.get("level"),
              "unit": e.get("unit"), "output": e.get("output")}
        if target is None:
            unassigned.append(it)
        else:
            group(target)["items"].append(it)

    for e in pinned_away:
        g = node_group(e["foreign"])
        same = next((i for i in g["items"] if (e["key"] and i.get("key") == e["key"])
                     or _low(i.get("name")) == _low(e["name"])), None)
        if same is not None:                             # 那台已經回報了，用它的即時狀態
            same["pinned"] = e["foreign"]
            same["local_name"] = e["name"]
            continue
        opts = e.get("opts") or {}
        g["items"].append({
            "key": e["key"], "name": e["name"], "host": g["host"], "local": False, "configured": True,
            "network": is_network_key(e["key"]), "driver": opts.get("driver", ""),
            "address": str(opts.get("address") or "") or "、".join(opts.get("sources") or []),
            "label": opts.get("label", ""), "idn": "", "connected": False, "lease": None, "online": g["online"],
            "options": opts, "hosts": [], "virtual": bool(opts.get("sources")), "pinned": e["foreign"],
            "local_name": e["name"],
            "status": f"instruments.yaml 指定接在 {e['foreign']}", "owned": False})

    # 別台量測節點的虛擬儀器（電磁鐵組）：Hub 的儀器登錄不含虛擬儀器，從節點狀態補上
    for n in nodes:
        if not n.get("host") or not n.get("online"):
            continue
        g = group(n["host"])
        if g["local"]:
            continue
        have = {_low(i.get("name")) for i in g["items"]}
        for r in n.get("instruments") or []:
            opts = r.get("options") or {}
            if not (r.get("virtual") or opts.get("sources")) or _low(r.get("name")) in have:
                continue
            g["items"].append({
                "key": None, "name": r["name"], "host": g["host"], "local": False, "configured": True,
                "network": False, "driver": r.get("driver") or opts.get("driver", ""),
                "address": "、".join(opts.get("sources") or []), "label": r.get("label") or opts.get("label", ""),
                "idn": r.get("idn", ""), "connected": bool(r.get("connected")), "lease": r.get("lease"),
                "online": True, "options": opts, "hosts": [g["host"]], "virtual": True, "owned": False,
                "level": r.get("level"), "unit": r.get("unit"), "output": r.get("output")})

    mine = groups.pop(_low(me))
    others = sorted(groups.values(), key=lambda g: (not g["node"], not g["online"], _low(g["host"])))
    out = [mine, *others]
    for g in out:
        nest_members(g["items"])
        g["title"] = (("這台電腦" + (f"（量測節點 {g['node']}）" if g["node"] else "")) if g["local"] else
                      (f"量測節點 {g['node']}" if g["node"] else "電腦") + f" · {g['host']}")
        g["items"].sort(key=lambda i: _low(i["name"]))
    unassigned.sort(key=lambda i: _low(i["name"]))
    out.append({"id": UNASSIGNED, "title": "未歸屬", "host": None, "node": None, "online": True, "local": False,
                "items": unassigned})
    return out


def magnet_members(item: Dict[str, Any]) -> List[str]:
    """電磁鐵組（虛擬儀器）用到的實體儀器名稱：sources [DC5.ch1, DC5.ch2] → [DC5]。"""
    srcs = (item.get("options") or {}).get("sources") or []
    return list(dict.fromkeys(str(x).split(".")[0] for x in srcs))


def nest_members(items: List[Dict[str, Any]]) -> None:
    """配對進電磁鐵組的 DC 收到該組底下（item["members"]），平常只看到電磁鐵組；成員仍可單獨操作。"""
    by_name = {_low(i.get("name")): i for i in items}
    taken = set()
    for it in items:
        if not (it.get("options") or {}).get("sources"):
            continue
        members = []
        for m in magnet_members(it):
            child = by_name.get(_low(m))
            if child is None or child is it or _low(m) in taken or (child.get("options") or {}).get("sources"):
                continue
            taken.add(_low(m))
            members.append(child)
        it["members"] = members
    if taken:
        items[:] = [i for i in items if _low(i.get("name")) not in taken]


def all_names(items: List[Dict[str, Any]]) -> List[str]:
    """群組裡全部儀器名稱（含收在電磁鐵組底下的成員）。"""
    out: List[str] = []
    for i in items:
        out.append(i["name"])
        out.extend(m["name"] for m in i.get("members") or [])
    return out


def move_instrument(item: Dict[str, Any], target: Dict[str, Any], *, me: str, claim: Callable[[str, str], Any],
                    release: Callable[[str], Any], add_local: Callable[[str, Dict[str, Any]], Any],
                    add_remote: Callable[[str, str, Dict[str, Any]], Any],
                    target_has: Callable[[str], bool],
                    pin: Optional[Callable[[str, str], Any]] = None, me_node: str = "") -> str:
    """把儀器（build_groups 的 item）移到 target 群組；回傳說明文字。失敗丟 RuntimeError。

    claim(key, host) / release(key)：Hub 歸屬；add_local(name, opts) / add_remote(node, name, opts)：補設定；
    target_has(key)：target 那台是否已經設定了這台儀器；
    pin(name, node)：把這台 instruments.yaml 裡這台儀器的 ``node:`` 改成 node（USB / GPIB / 虛擬儀器用）。
    """
    key = item.get("key")
    if pin is not None and not item.get("network"):
        msg = _pin_move(item, target, pin, me_node or me)
        if msg:
            return msg
    if not key:
        raise RuntimeError(f"{item.get('name')} 是虛擬儀器，跟著它的電源所在的電腦，不需要移動")
    if not item.get("network"):
        raise RuntimeError(f"{item.get('name')}（{key}）是 USB / GPIB 儀器，實體接在 {item.get('host')}，"
                           "請拖到它實際接著的那台電腦的群組（會寫入 instruments.yaml 的 node:）")
    if target["id"] == UNASSIGNED:
        release(key)
        return f"{item['name']} 已移到未歸屬（清除歸屬；需要時再移到某個群組）"
    host = target["host"]
    added = ""
    if not target_has(key):
        opts = dict(item.get("options") or {})
        if not opts.get("driver"):
            raise RuntimeError(f"沒有 {item['name']} 的設定可以複製到 {host}；請先在那台電腦新增這台儀器")
        opts.pop("enabled", None)
        if opts.get("node"):                             # 設定檔指定了別台 → 改成指定給目標那台
            opts["node"] = target.get("node") or host
        if _low(host) == _low(me):
            add_local(item["name"], opts)
        else:
            if not target.get("node") or not target.get("online"):
                raise RuntimeError(f"{host} 沒有在線的量測節點，無法把設定複製過去")
            add_remote(target["node"], item["name"], opts)
        added = f"（已把設定加到 {host} 的 instruments.yaml）"
    claim(key, host)
    return f"{item['name']} 已移到 {target['title']}{added}；其他電腦上連著的會自動中斷"


def _pin_move(item: Dict[str, Any], target: Dict[str, Any], pin: Callable[[str, str], Any], me_node: str) -> str:
    """USB / GPIB / 虛擬儀器的「移動」= 標明它接在哪台（instruments.yaml node:）。不適用時回傳空字串。"""
    name = item.get("local_name") or item.get("name")
    if item.get("local") and target.get("id") != UNASSIGNED and not target.get("local") and target.get("node"):
        pin(name, target["node"])
        return (f"{name} 標記為接在 {target['node']}（這台 instruments.yaml 寫入 node: {target['node']}）；"
                "這台不再連線、不再列出")
    if item.get("pinned") and target.get("local"):
        node = target.get("node") or me_node
        pin(name, node)
        return f"{name} 標記為接在這台 {node}（instruments.yaml node: {node}）"
    return ""
