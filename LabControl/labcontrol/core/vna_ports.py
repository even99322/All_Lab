"""1.0.10：VNA 有接線的 port 組合（即時數據只顯示這些 S 參數）。

VNA 一連線就用目前的設定把 n×n 個 S 參數都掃一次（4 port = 16 個，一次掃描全部讀出），
穿透 Sij（i ≠ j）的中位數高於門檻（預設 -60 dB，settings.yaml live.port_threshold_db）= 有接線；
有接線的 port 也保留反射 Sii。其他的在即時數據隱藏——儀器伺服器的「S 參數顯示…」可以再打開。

結果記在 LAB/vna_ports.json（每台 VNA 一筆）：
    {"VNA1": {"ports": 4, "levels": {"S21": -12.3, …}, "wired": ["S11", "S21", …],
              "visible": null 或 [...]（伺服器手動選的）, "threshold": -60, "time": …}}
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from ..paths import lab_path
from ..settings import setting
from . import sparams as sp

log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_SCANNING: set = set()


def _path():
    return lab_path("vna_ports.json")


def load() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        try:
            d = json.loads(_path().read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}


def _save(d: Dict[str, Any]) -> None:
    with _LOCK:
        try:
            p = _path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            log.warning("存 vna_ports.json 失敗：%s", e)


def threshold() -> float:
    try:
        return float(setting("live.port_threshold_db", -60.0))
    except (TypeError, ValueError):
        return -60.0


def level_db(z: Any) -> float:
    """一條曲線的代表值：|S| 中位數（dB）。"""
    a = np.abs(np.asarray(z, dtype=complex).ravel())
    a = a[np.isfinite(a)]
    if not a.size:
        return float("-inf")
    return float(20 * np.log10(max(float(np.median(a)), 1e-15)))


def wired(levels: Dict[str, float], n_ports: int, thr: Optional[float] = None) -> List[str]:
    """有接線的 S 參數（矩陣順序）：穿透高於門檻的 Sij，以及這些 port 的反射 Sii。
    一個穿透都沒有（例如 RF 沒開）→ 全部都留著，不要全藏起來。"""
    thr = threshold() if thr is None else float(thr)
    names = sp.sparam_names(n_ports)
    ports: set = set()
    keep: set = set()
    for name in names:
        pp = sp.ports_of(name)
        if not pp or pp[0] == pp[1]:
            continue
        v = levels.get(name)
        if v is not None and np.isfinite(v) and v > thr:
            keep.add(name)
            ports.update(pp)
    if not keep:
        return list(names)
    for i in ports:
        keep.add(f"S{sp._p(i, n_ports)}{sp._p(i, n_ports)}")
    return [n for n in names if n in keep]


def stored(name: str) -> Dict[str, Any]:
    return dict(load().get(name) or {})


def visible(name: str, sparams: Iterable[str]) -> List[str]:
    """即時數據要顯示的 S 參數：伺服器手動選的 → 掃描結果 → 全部。"""
    sparams = list(sparams)
    rec = stored(name)
    pick = rec.get("visible") if isinstance(rec.get("visible"), list) else rec.get("wired")
    if not pick:
        return sparams
    out = [s for s in sparams if s in set(pick)]
    return out or sparams


def merge(name: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """detect_ports() 的結果加上掃描結果（levels、wired、visible、manual）。"""
    info = dict(info or {})
    rec = stored(name)
    names = list(info.get("sparams") or [])
    if rec and int(rec.get("ports") or 0) == int(info.get("ports") or 0):
        info["levels"] = dict(rec.get("levels") or {})
        info["wired"] = list(rec.get("wired") or [])
        info["scanned"] = rec.get("time")
        info["manual"] = isinstance(rec.get("visible"), list)
    info["visible"] = visible(name, names) if names else []
    return info


def set_visible(name: str, params: Optional[List[str]]) -> Dict[str, Any]:
    """伺服器手動選要顯示的 S 參數；None = 回到掃描結果。"""
    with _LOCK:
        d = load()
        rec = dict(d.get(name) or {})
        rec["visible"] = list(params) if params is not None else None
        d[name] = rec
        _save(d)
    return rec


def scan(inst: Any, thr: Optional[float] = None) -> Dict[str, Any]:
    """用目前的設定量 n×n 個 S 參數（一次掃描），記下各自的大小與有接線的組合。"""
    name = inst.name
    info = inst.detect_ports() if hasattr(inst, "detect_ports") else (getattr(inst, "ports", None) or {})
    n = int(info.get("ports") or 0)
    if n <= 0:
        raise RuntimeError(f"{name}：偵測不到 port 數")
    names = sp.sparam_names(n)
    if hasattr(inst, "acquire_many"):
        data = inst.acquire_many(names, temporary=True)
    else:
        data = {s: inst.acquire(s) for s in names}
    levels = {s: round(level_db(z), 2) for s, z in data.items()}
    thr = threshold() if thr is None else float(thr)
    w = wired(levels, n, thr)
    with _LOCK:
        d = load()
        rec = dict(d.get(name) or {})
        if int(rec.get("ports") or 0) != n:
            rec["visible"] = None                       # port 數變了：手動的選擇不再適用
        rec.update(ports=n, levels=levels, wired=w, threshold=thr, time=time.time())
        d[name] = rec
        _save(d)
    log.info("%s：有接線的 S 參數 %s", name, ", ".join(w))
    return merge(name, info)


def is_vna(inst: Any) -> bool:
    return getattr(inst, "MEASURE_KIND", "") == "vna" and hasattr(inst, "detect_ports")


def scan_later(station: Any, name: str, force: bool = False) -> bool:
    """背景掃描（量測正在用這台 → 不掃）。已經掃過且不強制 → 不掃。回傳是否開始掃描。"""
    inst = station.instruments.get(name)
    if inst is None or not is_vna(inst) or name in _SCANNING:
        return False
    if not force and stored(name).get("levels"):
        return False
    if station.lease_holder(name):
        return False
    _SCANNING.add(name)

    def work() -> None:
        try:
            info = scan(inst)
            station.bus.publish("vna.ports", name=name, info=info)
        except Exception as e:  # noqa: BLE001 — 掃不到不影響連線
            log.warning("%s 掃描 port 失敗：%s", name, e)
        finally:
            _SCANNING.discard(name)
    threading.Thread(target=work, name=f"port-scan-{name}", daemon=True).start()
    return True


def install(station: Any) -> None:
    """儀器一連上線就掃描（settings.yaml live.port_scan_on_connect: false 可以關掉）。"""
    if getattr(station, "_vna_ports_hook", False):
        return
    station._vna_ports_hook = True

    def on_connect(_topic: str, p: Dict[str, Any]) -> None:
        if setting("live.port_scan_on_connect", True):
            scan_later(station, p.get("name") or "", force=True)
    station.bus.subscribe("instrument.connected", on_connect)
