"""跨電腦的儀器識別與回報（Hub 的「儀器登錄」用）。

儀器識別碼（key）由位址推得，讓不同電腦上、不同名稱的同一台實體儀器對得起來：

    TCPIP0::192.168.1.11::INSTR / TCPIP::192.168.1.11::hislip0::INSTR   → net:192.168.1.11
    TCPIP0::192.168.1.20::5025::SOCKET                                  → net:192.168.1.20:5025
    USB0::0x0B21::0x0039::90ZC38697::0::INSTR                           → usb:0b21:0039:90zc38697
    GPIB0::5::INSTR（只接在這台電腦）                                    → gpib@<電腦>:0:5
    SHFQC（options.device: dev12345）                                    → zi:dev12345
    虛擬儀器（magnet_A）                                                 → None（不登錄）

網路儀器（net:）可能同時被兩台電腦看到 →「共用」，需要主動「拉取」到某一台（Hub 記錄歸屬）。
USB 以廠商 / 產品 / 序號識別，接到別台電腦仍是同一個 key（Hub 會記錄它掛在哪台電腦）。
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from ..core.capabilities import Source
from ..core.station import Station
from ..settings import setting
from .hub import hostname


def instrument_key(address: str, options: Optional[Dict[str, Any]] = None, host: Optional[str] = None) -> Optional[str]:
    options = options or {}
    host = (host or hostname()).lower()
    a = str(address or "").strip()
    if not a:
        if options.get("sources"):
            return None
        dev = options.get("device") or options.get("serial")
        return f"zi:{str(dev).lower()}" if dev else None
    parts = [p for p in a.split("::")]
    head = parts[0].upper()
    if head.startswith("TCPIP"):
        h = parts[1].lower() if len(parts) > 1 else ""
        if len(parts) > 2 and parts[-1].upper() == "SOCKET" and parts[2].isdigit():
            return f"net:{h}:{parts[2]}"
        return f"net:{h}"
    if head.startswith("USB") and len(parts) >= 4:
        vid, pid, sn = (re.sub(r"^0x", "", p.lower()) for p in parts[1:4])
        return f"usb:{vid.zfill(4)}:{pid.zfill(4)}:{sn}"
    m = re.match(r"GPIB(\d*)", head)
    if m and len(parts) > 1:
        return f"gpib@{host}:{m.group(1) or 0}:{parts[1]}"
    if head.startswith("ASRL") or head.startswith("COM"):
        return f"serial@{host}:{a.lower()}"
    if re.match(r"^dev\d+$", a, re.I):
        return f"zi:{a.lower()}"
    return f"local@{host}:{a.lower()}"


def is_network_key(key: Optional[str]) -> bool:
    return bool(key) and str(key).startswith(("net:", "zi:"))


def parse_idn(idn: str) -> Dict[str, str]:
    p = [x.strip() for x in str(idn or "").split(",")]
    return {"maker": p[0] if p else "", "model": p[1] if len(p) > 1 else "", "serial": p[2] if len(p) > 2 else "",
            "firmware": p[3] if len(p) > 3 else ""}


def json_safe_options(opts: Dict[str, Any], driver: str) -> Dict[str, Any]:
    """instruments.yaml 的設定（可 JSON 化的部分）：把儀器移到別台節點時用來補設定。"""
    import json

    out: Dict[str, Any] = {"driver": driver}
    for k, v in (opts or {}).items():
        if k.startswith("_") or k in ("real_driver",):
            continue
        try:
            json.dumps(v)
        except (TypeError, ValueError):
            continue
        out[k] = v
    return out


def instrument_report(station: Station, host: Optional[str] = None) -> List[Dict[str, Any]]:
    """這台電腦的儀器（設定檔中的）狀態：給 Hub 的心跳 / 節點狀態。"""
    host = host or hostname()
    out = []
    for n, inst in station.instruments.items():
        opts = inst.options or {}
        addr = str(opts.get("address") or "")
        d: Dict[str, Any] = {"name": n, "connected": bool(inst.connected), "lease": station.lease_holder(n),
                             "driver": getattr(inst, "config_driver", inst.driver_name),
                             "label": opts.get("label", ""), "address": addr,
                             "key": instrument_key(addr, opts, host), "idn": getattr(inst, "_idn", "") or "",
                             "virtual": bool(opts.get("sources")), "simulate": bool(station.simulate),
                             "enabled": opts.get("enabled", True) is not False,
                             "options": json_safe_options(opts, getattr(inst, "config_driver", inst.driver_name))}
        src = inst if isinstance(inst, Source) else Station._sole_source(inst)
        if src is not None:
            try:
                d["level"] = src.parameters["level"].cache  # type: ignore[attr-defined]
                d["unit"] = src.unit
            except Exception:  # noqa: BLE001
                pass
            out_p = getattr(src, "parameters", {}).get("output")
            if out_p is not None:
                d["output"] = out_p.cache
        out.append(d)
    return out


#: 最近一次 VISA 掃描結果（儀器伺服器「掃描 VISA」或節點 scan 指令），會一起回報給 Hub
last_scan: Dict[str, Any] = {"time": 0.0, "items": []}


def record_scan(resources, station: Optional[Station] = None, host: Optional[str] = None) -> List[Dict[str, Any]]:
    host = host or hostname()
    by_key = {}
    if station is not None:
        for n, inst in station.instruments.items():
            k = instrument_key(str(inst.options.get("address") or ""), inst.options, host)
            if k:
                by_key[k] = n
    items = []
    for r in resources:
        k = instrument_key(r.address, None, host)
        items.append({"address": r.address, "key": k, "idn": r.idn or "", "error": r.error or "",
                      "suggested": list(r.suggested or []), "configured_as": by_key.get(k)})
    last_scan["time"] = time.time()
    last_scan["items"] = items
    return items


def describe_instrument(station: Station, name: str) -> Dict[str, Any]:
    """一台儀器的參數表（遠端控制視窗用）：名稱、標籤、單位、種類、可否讀寫、上下限、選項、快取值。"""
    from ..diagnostics import param_unit

    inst = station.instruments[name]
    groups = []
    nodes = [("", inst)] + [(k, ch) for k, ch in getattr(inst, "channels", {}).items()]
    for ck, node in nodes:
        params = []
        for k, p in node.parameters.items():
            sp = p.spec
            ref = f"{name}.{ck}.{k}" if ck else f"{name}.{k}"
            du = param_unit(p)
            if isinstance(node, Source) and k == "level":        # 電源輸出值：與儀器控制視窗相同（預設 mA）
                opt = str(inst.options.get("display_unit") or "")
                if opt.endswith(p.unit or "A") and opt:
                    du = opt
                elif (p.unit or "A") == "A":
                    du = str(setting("editor.new_blocks.dc_set.unit", "mA"))
            kind = sp.kind if sp is not None else ("bool" if k == "output" else "float" if p.unit else "str")
            params.append({"ref": ref, "name": k, "label": (sp.label if sp is not None and sp.label else
                                                            {"level": "輸出值", "output": "輸出"}.get(k, k)),
                           "unit": p.unit or "", "display_unit": du,
                           "kind": kind,
                           "choices": list(sp.choices) if sp is not None and sp.choices else None,
                           "limits": list(sp.limits) if sp is not None and sp.limits else None,
                           "gettable": bool(p.gettable), "settable": bool(p.settable), "value": _plain(p.cache),
                           "doc": sp.doc if sp is not None else (p.doc or "")})
        if params:
            groups.append({"channel": ck, "source": isinstance(node, Source), "params": params})
    opts = inst.options or {}
    return {"name": name, "label": opts.get("label", ""), "driver": getattr(inst, "config_driver", inst.driver_name),
            "address": opts.get("address", ""), "connected": bool(inst.connected), "lease": station.lease_holder(name),
            "idn": getattr(inst, "_idn", "") or "", "groups": groups}


def _plain(v: Any) -> Any:
    if hasattr(v, "item") and not hasattr(v, "__len__"):
        return v.item()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
