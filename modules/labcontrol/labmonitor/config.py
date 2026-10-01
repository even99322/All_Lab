"""Monitor 的設定：%APPDATA%\\LabControlMonitor\\config.json（第一次開啟時從 LAB\\settings.yaml 帶入 Hub 設定）。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    "hub_urls": ["192.168.50.2", "100.114.33.20"],
    "token": "Labqel330",
    "agent_url": "",                                         # Hub 控制代理（空白 = Hub 同一台主機的 8766）
    "refresh_s": 2.0,
}


def config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "LabControlMonitor" / "config.json"


def _from_lab_settings() -> Dict[str, Any]:
    """讀 Lab Control 的 LAB\\settings.yaml 裡的 remote.hub_urls / remote.token（有裝 Lab Control 的電腦）。"""
    home = os.environ.get("LAB_CONTROL_HOME") or str(Path.home() / "LAB")
    f = Path(home) / "settings.yaml"
    if not f.exists():
        return {}
    text = f.read_text(encoding="utf-8", errors="replace")
    try:
        import yaml  # type: ignore

        r = (yaml.safe_load(text) or {}).get("remote") or {}
        out = {}
        if r.get("hub_urls"):
            out["hub_urls"] = [str(u) for u in r["hub_urls"]]
        if r.get("token"):
            out["token"] = str(r["token"])
        return out
    except Exception:  # noqa: BLE001 - 沒有 PyYAML：簡單解析
        out = {}
        m = re.search(r"^\s*token:\s*['\"]?([^'\"#\s]+)", text, re.M)
        if m:
            out["token"] = m.group(1)
        m = re.search(r"^\s*hub_urls:\s*\[([^\]]*)\]", text, re.M)
        if m:
            out["hub_urls"] = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
        return out


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
            return cfg
        except ValueError:
            pass
    cfg.update(_from_lab_settings())
    return cfg


def save(cfg: Dict[str, Any]) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
