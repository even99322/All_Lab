"""v3.9：求解中的進度。

AEDT 求解改成不阻塞（analyze_setup(blocking=False)），工作程序每幾秒問一次：
    odesktop.AreThereSimulationsRunning()      還在算嗎
    odesktop.GetMonitorData("profile 0 0 convergence 0 0 …")   AEDT「Simulation Monitor」的資料
GetMonitorData 回的是 AEDT 內部的文字格式（$begin 'MonConvData' … $end），各版本不太一樣，
這裡只用寬鬆的規則找：Adaptive 第幾個 pass、ΔS、掃頻 / 整體百分比、第幾個變數組合。
找不到也沒關係，介面照樣顯示「AEDT 求解中」與經過時間。原始文字存一份在 %TEMP%\\hfss_monitor_sample.txt 方便之後改規則。
"""
from __future__ import annotations

import os
import re
import tempfile
from typing import Dict, Optional

REQUEST = "profile 0 0 convergence 0 0 sweptvar 0 0 progress 0 0 variations 0 0 displaytype 0 0"
SAMPLE = os.path.join(tempfile.gettempdir(), "hfss_monitor_sample.txt")

_NUM = r"([0-9]+(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)"
_PASS = re.compile(r"(?:Adaptive\s*)?Pass(?:\s*(?:Number|Num|#|No\.?))?['\"]?\s*[=:]?\s*['\"]?(\d+)", re.I)
_DELTA = re.compile(r"(?:Max\s*)?(?:Mag\s*)?Delta\s*[-_ ]?S['\"]?\s*[=:]?\s*['\"]?" + _NUM, re.I)
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_PROG = re.compile(r"(?:Progress|Percent(?:Complete)?|PercentDone)['\"]?\s*[=:]\s*['\"]?" + _NUM, re.I)
_VAR = re.compile(r"Variation\w*\D{0,12}(\d+)\s*(?:of|/)\s*(\d+)", re.I)
_FREQ = re.compile(r"(?:Freq(?:uency)?|Solving\s+at)['\"]?\s*[=:]?\s*['\"]?" + _NUM + r"\s*(GHz|MHz|Hz)", re.I)
_TASK = re.compile(r"(Adaptive\s+(?:Pass|Meshing)|Frequency\s+Sweep|Interpolating|Discrete|Fast\s+Sweep|"
                   r"Solving\s+Matrix|Mesh(?:ing)?\s+(?:Generation|Refinement)|Initial\s+Mesh|Port\s+Adapt\w*|"
                   r"Field\s+Recovery|Assembling|Matrix\s+Solve)", re.I)


def parse(text: str) -> Dict:
    """回傳能找到的：pass、delta_s、percent、variation (k, n)、freq_ghz、task。"""
    out: Dict = {}
    if not text:
        return out
    passes = [int(m) for m in _PASS.findall(text)]
    if passes:
        out["pass"] = max(passes)
    deltas = _DELTA.findall(text)
    if deltas:
        try:
            out["delta_s"] = float(deltas[-1])
        except ValueError:
            pass
    prog = _PROG.findall(text)
    pct = [float(x) for x in prog] if prog else [float(x) for x in _PCT.findall(text)]
    pct = [x for x in pct if 0 <= x <= 100]
    if pct:
        out["percent"] = pct[-1]
    var = _VAR.findall(text)
    if var:
        k, n = var[-1]
        out["variation"] = (int(k), int(n))
    fr = _FREQ.findall(text)
    if fr:
        v, unit = fr[-1]
        try:
            out["freq_ghz"] = float(v) * {"ghz": 1, "mhz": 1e-3, "hz": 1e-9}[unit.lower()]
        except (ValueError, KeyError):
            pass
    tasks = _TASK.findall(text)
    if tasks:
        out["task"] = re.sub(r"\s+", " ", tasks[-1]).strip()
    return out


def describe(info: Dict) -> str:
    parts = []
    if info.get("task"):
        parts.append(info["task"])
    if info.get("variation"):
        k, n = info["variation"]
        parts.append(f"變數組合 {k}/{n}")
    if info.get("pass"):
        parts.append(f"Adaptive 第 {info['pass']} 個 pass")
    if info.get("delta_s") is not None:
        parts.append(f"ΔS {info['delta_s']:.4g}")
    if info.get("freq_ghz"):
        parts.append(f"{info['freq_ghz']:.4f} GHz")
    if info.get("percent") is not None:
        parts.append(f"{info['percent']:.0f}%")
    return "　".join(parts)


def save_sample(text: str) -> None:
    try:
        with open(SAMPLE, "w", encoding="utf-8", errors="replace") as f:
            f.write(text[:65536])
    except OSError:
        pass


def read(desktop) -> Optional[str]:
    """desktop = hfss.odesktop；拿不到 → None。"""
    try:
        return str(desktop.GetMonitorData(REQUEST) or "")
    except Exception:  # noqa: BLE001
        return None


def running(desktop) -> Optional[bool]:
    try:
        return bool(desktop.AreThereSimulationsRunning())
    except Exception:  # noqa: BLE001
        return None
