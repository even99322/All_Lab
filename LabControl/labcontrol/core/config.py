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


#: 電流斜坡預設速率（A/s）：0.0.31 起 10 mA/s；設定檔裡還是舊預設 0.5 mA/s 的視為 10 mA/s
DEFAULT_RAMP_RATE = 1.0e-2
LEGACY_RAMP_RATE = 5.0e-4


def migrate_instruments_text(text: str) -> str:
    """instruments.yaml 裡舊預設 ramp_rate: 5.0e-4（0.5 mA/s）→ 1.0e-2（10 mA/s）；其他值不動。"""
    import re

    return re.sub(r"(?m)^(\s*ramp_rate:\s*)(5(?:\.0+)?e-0?4|0\.0005)\b(\s*#[^\n]*)?$",
                  lambda m: m.group(1) + "1.0e-2" + "        # A/s（10 mA/s；0.0.31 由 0.5 mA/s 調整）", text)


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
    rate = _opt_float(base.get("ramp_rate"))
    if rate is not None and abs(rate - LEGACY_RAMP_RATE) < 1e-12:
        rate = DEFAULT_RAMP_RATE                    # 0.0.30 以前的預設 0.5 mA/s → 10 mA/s（0.0.31）
    policy = RampPolicy(
        rate=rate,
        max_jump=_opt_float(base.get("max_jump")),
        dt=float(base.get("ramp_dt", 0.1)),
    )
    return dict(unit=base.get("unit", "A"), limits=limits, ramp_policy=policy,
                resolution=_opt_float(base.get("resolution")))


def _opt_float(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def set_instrument_label(text: str, name: str, label: str) -> str:
    """instruments.yaml 的文字裡，把儀器 name 的 label 改成 label（保留註解與排版；沒有 label 就加一行）。"""
    import json as _json
    import re

    lines = text.splitlines(keepends=True)
    key = re.compile(rf"^(\s*){re.escape(name)}:\s*(#.*)?$")
    for i, line in enumerate(lines):
        m = key.match(line.rstrip("\r\n"))
        if not m:
            continue
        ind = len(m.group(1))
        child = None
        j = i + 1
        while j < len(lines):
            s = lines[j].rstrip("\r\n")
            if s.strip() and not s.lstrip().startswith("#"):
                cur = len(s) - len(s.lstrip())
                if cur <= ind:
                    break
                child = cur if child is None else child
                if cur == child and re.match(r"\s*label:", s):
                    nl = lines[j][len(s):] or "\n"
                    comment = re.search(r"\s+#.*$", s)
                    lines[j] = " " * cur + "label: " + _json.dumps(label, ensure_ascii=False) + \
                        (comment.group(0) if comment else "") + nl
                    return "".join(lines)
            j += 1
        nl = "\r\n" if lines[i].endswith("\r\n") else "\n"
        lines.insert(i + 1, " " * (child if child is not None else ind + 2) + "label: "
                     + _json.dumps(label, ensure_ascii=False) + nl)
        return "".join(lines)
    raise ConfigError(f"instruments.yaml 裡找不到 {name}")
