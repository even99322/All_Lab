"""設定檔讀取（YAML 或 JSON）與 Source 共用選項解析。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import ConfigError
from .safety import Limits, RampPolicy


def load_config(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"找不到設定檔 {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as e:  # pragma: no cover
            raise ConfigError("讀取 YAML 需要 pyyaml：pip install pyyaml") from e
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 最外層必須是 mapping")
    data.setdefault("_source_path", str(path.resolve()))
    return data


def source_settings(options: Dict[str, Any], channel_key: Optional[str] = None) -> Dict[str, Any]:
    """把 YAML 裡的 source: {...} 轉成 Source.init_source() 的參數。

    多通道儀器可在 channels.<key>.source 覆寫。
    source:
        unit: A
        limits: [-0.2, 0.2]
        ramp_rate: 5.0e-4     # 單位/秒
        max_jump: 1.0e-4      # 超過這個差值自動走斜坡
        ramp_dt: 0.1
        resolution: 1.0e-6
    """
    base = dict(options.get("source") or {})
    if channel_key:
        ch_opts = (options.get("channels") or {}).get(channel_key) or {}
        base.update(ch_opts.get("source") or {})
    lim = base.get("limits")
    limits = Limits(float(lim[0]), float(lim[1])) if lim else Limits()
    policy = RampPolicy(
        rate=_opt_float(base.get("ramp_rate")),
        max_jump=_opt_float(base.get("max_jump")),
        dt=float(base.get("ramp_dt", 0.1)),
    )
    return dict(unit=base.get("unit", "A"), limits=limits, ramp_policy=policy,
                resolution=_opt_float(base.get("resolution")))


def _opt_float(v: Any) -> Optional[float]:
    return None if v is None else float(v)
