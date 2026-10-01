"""簡單的單位換算：設定檔可以寫 "5.0198 GHz"、"0.5 mA/s"，框架內部一律用 SI。"""
from __future__ import annotations

import re
from typing import Any, Tuple

from .errors import ConfigError

_PREFIX = {"": 1.0, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9}
_BASE = ("A", "V", "Hz", "s", "dBm", "dB", "Ohm", "T", "K", "W", "deg", "rad")


def split_unit(unit: str) -> Tuple[str, float]:
    """'mA' → ('A', 1e-3)；'mA/s' → ('A/s', 1e-3)；未知單位 → (unit, 1.0)。"""
    if not unit:
        return "", 1.0
    head, sep, tail = unit.partition("/")
    for base in sorted(_BASE, key=len, reverse=True):
        if head.endswith(base):
            pre = head[: -len(base)]
            if pre in _PREFIX and base not in ("dBm", "dB") or (pre == "" and base in ("dBm", "dB")):
                return base + (sep + tail if sep else ""), _PREFIX[pre]
    return unit, 1.0


_Q = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([A-Za-zµ/]*)\s*$")


def parse_quantity(value: Any, default_unit: str = "") -> float:
    """數字直接回傳；字串 '5.0198 GHz' → 5.0198e9。沒寫單位時套用 default_unit。"""
    if value is None:
        raise ConfigError("缺少數值")
    if isinstance(value, (int, float)):
        return float(value) * split_unit(default_unit)[1]
    m = _Q.match(str(value))
    if not m:
        raise ConfigError(f"無法解析數值 '{value}'")
    num, unit = m.groups()
    return float(num) * split_unit(unit or default_unit)[1]


def to_si_dict(d: dict) -> dict:
    """把 dict 內能解析成數量的字串轉成 float（非數量的字串保持原樣）。"""
    out = {}
    for k, v in d.items():
        if isinstance(v, str):
            try:
                out[k] = parse_quantity(v)
                continue
            except ConfigError:
                pass
        out[k] = v
    return out
