"""設定與資料夾。

Windows：%LOCALAPPDATA%\\QELNetMeter（程式、config.json、state.json、netmeter.log）
macOS  ：~/Library/Application Support/QELNetMeter
其他    ：~/.qelnetmeter
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from . import APP_ID

DEFAULTS: Dict[str, Any] = {
    # 實驗室網段：裝置有網卡拿到這個網段的位址時才計算那張網卡的流量
    "subnets": ["192.168.50.0/24"],
    # Hub（NAS）位址：依序試，第一個連得到的
    "hubs": ["http://192.168.50.2:8765"],
    "sample_s": 30,          # 多久讀一次網卡計數
    "report_s": 60,          # 多久送一次到 Hub
    "update_check_h": 6,     # 多久檢查一次新版（Windows 自動更新）
    "control_port": 47630,   # 本機控制埠（只聽 127.0.0.1；視窗用來查狀態 / 叫背景程式結束）
    "keep_days": 62,
}


def data_dir() -> Path:
    env = os.environ.get("QELNETMETER_HOME")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_ID
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_ID
    else:
        p = Path.home() / ".qelnetmeter"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read(p: Path) -> Dict[str, Any]:
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(p: Path, obj: Dict[str, Any]) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in _read(data_dir() / "config.json").items() if k in DEFAULTS})
    cfg["subnets"] = [s for s in (cfg.get("subnets") or []) if valid_subnet(s)] or list(DEFAULTS["subnets"])
    cfg["hubs"] = [normalize_hub(h) for h in (cfg.get("hubs") or []) if str(h).strip()] or list(DEFAULTS["hubs"])
    return cfg


def save(cfg: Dict[str, Any]) -> None:
    write_json(data_dir() / "config.json", {k: cfg[k] for k in DEFAULTS if k in cfg})


def valid_subnet(s: Any) -> bool:
    try:
        ipaddress.ip_network(str(s).strip(), strict=False)
        return True
    except ValueError:
        return False


def normalize_hub(h: Any) -> str:
    h = str(h).strip().rstrip("/")
    if h and "://" not in h:
        h = "http://" + h
    if h.count(":") == 1:                      # 沒寫埠 → Hub 預設 8765
        h += ":8765"
    return h


def networks(cfg: Dict[str, Any]) -> List[Any]:
    return [ipaddress.ip_network(s, strict=False) for s in cfg["subnets"]]
