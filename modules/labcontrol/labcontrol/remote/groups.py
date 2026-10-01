"""儀器伺服器的「群組」：每個量測節點（電腦）下轄哪些儀器，加上「未歸屬」。

規則（每台實體儀器 = 一個 key，只出現在一個群組）：
  * Hub 記錄了歸屬（owner）→ 放在那台電腦的群組；
  * 共用網路儀器（兩台以上電腦都看得到）但還沒歸屬 → 未歸屬；
  * 只有一台電腦設定了它 → 那台電腦的群組；
  * 只被掃描偵測到、沒有任何電腦設定 → 未歸屬（標示在哪台偵測到）；
  * 這台電腦的虛擬儀器（magnet_A）→ 這台的群組。

移動（move_instrument）：
  * 移到某台電腦的群組 → 那台沒有設定就先把設定複製過去（節點 add_instrument），再請 Hub 記錄歸屬；
    其他電腦上連著的會自動中斷。
  * 移到未歸屬 → 清除歸屬。
  * USB / GPIB 儀器實體接在某台電腦，不能用軟體移動。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .instruments import instrument_key, is_network_key

UNASSIGNED = "__unassigned__"


def _low(s: Optional[str]) -> str:
    return str(s or "").lower()


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

    group(me)
    unassigned: List[Dict[str, Any]] = []
    local_keys = set()
    for e in local:
        opts = e.get("opts") or {}
        key = instrument_key(str(opts.get("address") or ""), opts, me)
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

    for r in registry:
        key = r["key"]
        entries = r.get("entries") or []
        own = owners.get(key)
        hosts = [e["host"] for e in entries]
        if own:
            target = own
        elif key in shared:
            target = None
        else:
            conf = [e for e in entries if e.get("configured")]
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

    mine = groups.pop(_low(me))
    others = sorted(groups.values(), key=lambda g: (not g["node"], not g["online"], _low(g["host"])))
    out = [mine, *others]
    for g in out:
        g["title"] = (("這台電腦" + (f"（量測節點 {g['node']}）" if g["node"] else "")) if g["local"] else
                      (f"量測節點 {g['node']}" if g["node"] else "電腦") + f" · {g['host']}")
        g["items"].sort(key=lambda i: _low(i["name"]))
    unassigned.sort(key=lambda i: _low(i["name"]))
    out.append({"id": UNASSIGNED, "title": "未歸屬", "host": None, "node": None, "online": True, "local": False,
                "items": unassigned})
    return out


def move_instrument(item: Dict[str, Any], target: Dict[str, Any], *, me: str, claim: Callable[[str, str], Any],
                    release: Callable[[str], Any], add_local: Callable[[str, Dict[str, Any]], Any],
                    add_remote: Callable[[str, str, Dict[str, Any]], Any],
                    target_has: Callable[[str], bool]) -> str:
    """把儀器（build_groups 的 item）移到 target 群組；回傳說明文字。失敗丟 RuntimeError。

    claim(key, host) / release(key)：Hub 歸屬；add_local(name, opts) / add_remote(node, name, opts)：補設定；
    target_has(key)：target 那台是否已經設定了這台儀器。
    """
    key = item.get("key")
    if not key:
        raise RuntimeError(f"{item.get('name')} 是虛擬儀器，跟著它的電源所在的電腦，不需要移動")
    if not item.get("network"):
        raise RuntimeError(f"{item.get('name')}（{key}）是 USB / GPIB 儀器，實體接在 {item.get('host')}，"
                           "不能用軟體移動（請改接線）")
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
        if _low(host) == _low(me):
            add_local(item["name"], opts)
        else:
            if not target.get("node") or not target.get("online"):
                raise RuntimeError(f"{host} 沒有在線的量測節點，無法把設定複製過去")
            add_remote(target["node"], item["name"], opts)
        added = f"（已把設定加到 {host} 的 instruments.yaml）"
    claim(key, host)
    return f"{item['name']} 已移到 {target['title']}{added}；其他電腦上連著的會自動中斷"
