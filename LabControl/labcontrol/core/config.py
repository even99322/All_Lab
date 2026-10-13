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


def set_instrument_key(text: str, name: str, key: str, value: str) -> str:
    """instruments.yaml 的文字裡，把儀器 name 的 ``key:`` 設成 value（已經是 YAML 文字，例如 ``"LAB-1"``、
    ``[LAB-1, LAB-2]``）；value 為空字串 = 刪掉這一行。保留註解與排版；沒有這個 key 就加在儀器名稱下一行。"""
    import re

    lines = text.splitlines(keepends=True)
    head = re.compile(rf"^(\s*){re.escape(name)}:\s*(#.*)?$")
    for i, line in enumerate(lines):
        m = head.match(line.rstrip("\r\n"))
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
                if cur == child and re.match(rf"\s*{re.escape(key)}:", s):
                    if value == "":
                        del lines[j]
                        return "".join(lines)
                    nl = lines[j][len(s):] or "\n"
                    comment = re.search(r"\s+#.*$", s)
                    lines[j] = " " * cur + f"{key}: {value}" + (comment.group(0) if comment else "") + nl
                    return "".join(lines)
            j += 1
        if value == "":
            return "".join(lines)
        nl = "\r\n" if lines[i].endswith("\r\n") else "\n"
        lines.insert(i + 1, " " * (child if child is not None else ind + 2) + f"{key}: {value}" + nl)
        return "".join(lines)
    raise ConfigError(f"instruments.yaml 裡找不到 {name}")


def set_instrument_label(text: str, name: str, label: str) -> str:
    """instruments.yaml 的文字裡，把儀器 name 的 label 改成 label（保留註解與排版；沒有 label 就加一行）。"""
    import json as _json

    return set_instrument_key(text, name, "label", _json.dumps(label, ensure_ascii=False))


def node_yaml(nodes) -> str:
    """node: 的值 → YAML 文字（空 = 不指定）。"""
    names = [str(n).strip() for n in (nodes if isinstance(nodes, (list, tuple)) else [nodes]) if str(n or "").strip()]
    if not names:
        return ""
    return names[0] if len(names) == 1 else "[" + ", ".join(names) + "]"


def set_instrument_node(text: str, name: str, nodes) -> str:
    """0.0.32：儀器接在哪台量測電腦（``node:``）；nodes 空 = 不指定。"""
    return set_instrument_key(text, name, "node", node_yaml(nodes))


#: 0.0.32：實驗室已知的儀器 → 量測電腦（依位址辨認；只用在還沒寫過任何 node: 的 instruments.yaml）
KNOWN_NODES = (
    ("90ZC38697", "LAB-1"), ("90ZC38696", "LAB-1"), ("9017D5818", "LAB-1"), ("9017D5816", "LAB-1"),
    ("192.168.1.11", "LAB-1"), ("192.168.1.12", "LAB-2"),
)


def migrate_instrument_nodes(text: str) -> str:
    """還沒寫過任何 ``node:`` 的 instruments.yaml：依位址補上 node（DC1–DC4、VNA1 → LAB-1；VNA2 → LAB-2；
    SHFQC → [LAB-1, LAB-2]）。電磁鐵組不用寫（跟著電源）。已經有 node: 的檔案不動。"""
    import re

    import yaml

    if re.search(r"(?m)^\s+node:", text):
        return text
    try:
        conf = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return text
    out = text
    for name, opts in ((conf.get("instruments") or {}) if isinstance(conf, dict) else {}).items():
        if not isinstance(opts, dict) or str(name).startswith("_"):
            continue
        address = str(opts.get("address") or "")
        node = next((n for key, n in KNOWN_NODES if key in address), None)
        if node is None and str(opts.get("driver") or "") == "zi.shfqc":
            node = ["LAB-1", "LAB-2"]
        if node:
            try:
                out = set_instrument_node(out, str(name), node)
            except ConfigError:
                continue
    return out
