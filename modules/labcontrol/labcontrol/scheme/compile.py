"""把量測方案（流程圖）編譯成 labcontrol 實驗設定，同時做檢查與估時。

    result = compile_scheme(scheme, build_catalog(station))
    result.issues          → 錯誤 / 警告（附方塊 id，UI 用來標紅）
    result.config          → 與 LAB/experiments/*.yaml 相同格式的 dict（有錯誤時為 None）
    result.experiment(st)  → labcontrol.measure.Experiment，可直接 create_runner()

對應規則：
    量測方塊所在的迴圈鏈（外 → 內）       → sweep 軸（外 → 內）
    固定值的 set 方塊                     → setup（開始前設定一次）
    量測方塊的儀器設定                     → setup[儀器]
    Data 方塊在第 d 層迴圈內               → output.split_by = 最外面 d 個軸（每個外圈值一個檔）
    第 2 個以後的 Data 方塊                → output.extra[]（各自的檔名、格式、分檔層數）
    set 方塊「電流異步」（交錯電磁鐵組）     → 軸步進 = step/2，setup 設定交錯網格（每台步進、原點）
    wait 方塊                             → 加到所在迴圈的 settle（最外層 → 開始前等待）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from ..core.errors import LabControlError
from ..core.units import parse_quantity, split_unit
from ..measure.sweep import Axis
from ..settings import setting
from .catalog import Catalog, Target
from .model import Block, Scheme


@dataclass
class Issue:
    level: str                 # error | warning | info
    message: str
    block_id: Optional[str] = None


@dataclass
class LoopInfo:
    block: Block
    target: Target
    axis_name: str
    values: np.ndarray          # SI
    display_unit: str
    display_scale: float
    settle: float

    @property
    def n(self) -> int:
        return int(len(self.values))


@dataclass
class CompileResult:
    config: Optional[Dict[str, Any]]
    issues: List[Issue] = field(default_factory=list)
    loops: List[LoopInfo] = field(default_factory=list)
    measures: List[Block] = field(default_factory=list)
    save: Optional[Block] = None
    saves: List[Block] = field(default_factory=list)
    extras: List[Dict[str, Any]] = field(default_factory=list)   # 第 2 個以後的 Data：{block, depth, split_by, file_name, formats, collision}
    split_depth: int = 0
    total_points: int = 0
    n_files: int = 0
    est_seconds: Optional[float] = None
    est_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def issues_for(self, block_id: str) -> List[Issue]:
        return [i for i in self.issues if i.block_id == block_id]

    def experiment(self, station):
        from ..measure.experiment import Experiment

        if self.config is None:
            raise LabControlError("方案有錯誤，無法執行：" + "；".join(i.message for i in self.issues if i.level == "error"))
        return Experiment(station, self.config)


# ---------------------------------------------------------------------------
def default_axis_name(t: Target) -> str:
    if t.group in ("magnet", "source"):
        suffix = setting("editor.axis_name_suffix", {}) or {}
        return f"{t.short}{suffix.get(split_unit(t.unit)[0], '')}"
    return t.short


def _seconds(v: Any) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(parse_quantity(v, "s"))
    except Exception:  # noqa: BLE001
        return 0.0


def _axis_dict(b: Block, t: Target) -> Dict[str, Any]:
    d: Dict[str, Any] = {"target": target_param_ref(t), "unit": b.unit, "start": b.start, "stop": b.stop}
    if b.points:
        d["num"] = int(b.points)
        if b.interp == "log":
            d["interp"] = "log"
    elif b.interleave and b.step:
        d["step"] = abs(b.step) / 2              # 電流異步：每點只有一台前進 step → 平均走 step/2
    else:
        d["step"] = b.step
    if b.alternate:
        d["alternate"] = True
    return d


def file_settings(scheme: Scheme, save: Optional[Block] = None) -> Dict[str, Any]:
    """檔案設定：方案的 output（右下「檔案設置」）> Data 節點（舊格式）> settings.yaml。"""
    o = scheme.output
    if "formats" in o:
        formats = list(o.get("formats") or [])
    elif save is not None:
        formats = list(save.formats)
    else:
        formats = list(setting("data.default_formats", ["labber"]) or [])
    tags = o.get("tags")
    if tags is None:
        tags = [*(setting("labber.tags", []) or []), scheme.name]
    return {
        "file_name": o.get("file_name") or (save.file_name if save and save.file_name else f"{scheme.name}.hdf5"),
        "formats": formats,
        "collision": o.get("collision") or (save.collision if save else None) or setting("data.collision",
                                                                                         "underscore"),
        "project": o.get("project") or setting("labber.project", "auto"),
        "user": o.get("user") if o.get("user") is not None else setting("labber.user", ""),
        "tags": list(tags),
        "comment": o.get("comment", ""),
    }


def target_param_ref(t: Target) -> str:
    return f"{t.ref}.level" if t.group in ("magnet", "source") else t.ref


def block_caption(b: Block, cat: Catalog) -> str:
    """流程圖 / 表格共用的一行說明。"""
    if b.kind == "set":
        t = cat.target(b.target)
        name = t.short if t else (b.target or "（未選目標）")
        if b.is_loop:
            step = f"{b.points} 點" if b.points else f"步進 {_fmt(b.step)}"
            if b.interleave:
                step = f"異步：每台步進 {_fmt(b.step)}（平均 {_fmt(abs(b.step or 0) / 2)}）"
            return f"{name}：{_fmt(b.start)} → {_fmt(b.stop)} {b.unit}，{step}"
        return f"{name} = {_fmt(b.value)} {b.unit}"
    if b.kind == "measure":
        m = cat.measurer(b.instrument)
        return f"{m.label if m else b.instrument or '（未選儀器）'}：{', '.join(b.traces) or '—'}"
    if b.kind == "save":
        return f"{b.file_name or '（自動檔名）'}  [{', '.join(b.formats)}]"
    if b.kind == "wait":
        return f"等待 {_fmt(b.seconds)} s"
    return ""


def _fmt(v: Any) -> str:
    if v is None:
        return "?"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _measure_time(b: Block, cat: Catalog) -> float:
    """每點量測時間估計；係數在 settings.yaml estimate.<MEASURE_KIND>。
    有 integration_time → 點數 × 平均 × 積分時間；有 if_bw → 點數 / IF BW × 平均。"""
    m = cat.measurer(b.instrument)
    est = setting("estimate", {}) or {}
    coef = est.get(m.kind if m else "", {}) or {}
    s = {**(m.defaults if m else {}), **(b.settings or {})}
    try:
        pts = float(s.get("points", 1))
        avg = max(1.0, float(s.get("averages", 1) or 1))
        if "integration_time" in s:
            base = pts * avg * parse_quantity(s["integration_time"], "s")
        elif "if_bw" in s:
            base = pts / parse_quantity(s["if_bw"], "Hz") * avg
        else:
            return float(coef.get("fixed_s", est.get("default_s", 0.1)))
        return base * float(coef.get("overhead", 1.0)) + float(coef.get("fixed_s", 0.0))
    except Exception:  # noqa: BLE001
        return float(est.get("default_s", 0.1))


# ---------------------------------------------------------------------------
def compile_scheme(scheme: Scheme, cat: Catalog) -> CompileResult:
    res = CompileResult(config=None)
    issues = res.issues
    err = lambda msg, b=None: issues.append(Issue("error", msg, b.id if b else None))  # noqa: E731
    warn = lambda msg, b=None: issues.append(Issue("warning", msg, b.id if b else None))  # noqa: E731
    info = lambda msg, b=None: issues.append(Issue("info", msg, b.id if b else None))  # noqa: E731

    flat = scheme.flat()
    for nid in scheme.loose_ids:
        warn("這個節點沒有接到流程上（從「開始」連不到），不會執行", scheme.graph.nodes[nid])
    if not flat:
        err("方案是空的：從左下的儀器參數列表把電源、量測拖進流程圖，並從「開始」連線")
        return res
    order = {b.id: i for i, b in enumerate(flat)}
    measures = [b for b in flat if b.kind == "measure"]
    saves = [b for b in flat if b.kind == "save"]
    res.measures = measures

    # ---- 量測方塊 ----
    if not measures:
        err("沒有量測方塊")
    kinds = set()
    for m in measures:
        mm = cat.measurer(m.instrument)
        if mm is None:
            err(f"找不到量測儀器 '{m.instrument}'", m)
            continue
        kinds.add(mm.kind)
        if not m.traces:
            err("請至少選一個量測通道（例如 S21）", m)
    for group in setting("rules.exclusive_measure_kinds", []) or []:
        used = [k for k in group if k in kinds]
        if len(used) > 1:
            err(f"{' 與 '.join(k.upper() for k in used)} 不同時量測：同一個方案請只用其中一種"
                "（settings.yaml rules.exclusive_measure_kinds）")

    chain: List[Block] = []
    if measures:
        chains = [[a.id for a in scheme.ancestors(m.id)] for m in measures]
        if any(c != chains[0] for c in chains):
            for m in measures[1:]:
                err("所有量測方塊必須在同一層迴圈內", m)
        chain = scheme.ancestors(measures[0].id)
    chain_ids = {b.id for b in chain}
    for b in flat:
        if b.is_loop and b.id not in chain_ids:
            err("這個迴圈裡沒有量測方塊（或與量測不在同一條迴圈鏈上）", b)

    # ---- 迴圈 → 掃描軸 ----
    swept_refs = set()
    names_seen: Dict[str, str] = {}
    for b in chain:
        t = cat.target(b.target)
        if t is None:
            err(f"找不到掃描目標 '{b.target}'", b)
            continue
        want = split_unit(t.unit)[0]
        if want and split_unit(b.unit)[0] != want:
            err(f"單位應為 {want} 系列（目前 '{b.unit}'）", b)
            continue
        if b.start is None or b.stop is None or (not b.step and not b.points):
            err("請填起點、終點，以及步進或點數", b)
            continue
        if b.interleave and not t.interleave:
            err(f"「電流異步」只適用於兩台交錯的電磁鐵組（virtual.interleaved_pair），{t.short} 不是", b)
            continue
        if b.interleave and b.points:
            err("「電流異步」請用步進（每台每次前進的電流）設定，不要用點數", b)
            continue
        d = _axis_dict(b, t)
        if b.interp == "log" and not b.points:
            err("對數間隔請用「點數」設定", b)
            continue
        try:
            ax = Axis.from_config(d)
        except LabControlError as e:
            err(str(e), b)
            continue
        if len(ax.values) < 2:
            warn("掃描只有 1 點", b)
        if len(ax.values) > int(setting("rules.max_points_per_axis", 100000)):
            err(f"點數過多（{len(ax.values)}）", b)
            continue
        if t.limits and (ax.values.min() < t.limits[0] - 1e-15 or ax.values.max() > t.limits[1] + 1e-15):
            err(f"範圍超出 {t.short} 的安全上下限 [{t.limits[0] * ax.display_scale:g}, "
                f"{t.limits[1] * ax.display_scale:g}] {ax.display_unit}", b)
        name = b.axis_name or default_axis_name(t)
        if name in names_seen:
            err(f"資料軸名稱重複：{name}", b)
        names_seen[name] = b.id
        settle = float(b.settle or 0) + sum(float(c.seconds or 0) for c in b.children if c.kind == "wait")
        swept_refs.add(t.ref)
        res.loops.append(LoopInfo(b, t, name, ax.values, ax.display_unit, ax.display_scale, settle))
    delay = _seconds(scheme.run.get("point_delay"))
    if delay and res.loops:   # Labber「Delay between step and measure」：最內圈每一點至少等這麼久
        res.loops[-1].settle = max(res.loops[-1].settle, delay)

    # ---- 固定設定 ----
    setup: Dict[str, Any] = {}
    for b in flat:
        if b.kind != "set" or b.is_loop:
            continue
        t = cat.target(b.target)
        if t is None:
            err(f"找不到設定目標 '{b.target}'", b)
            continue
        if b.value is None:
            err("請填設定值", b)
            continue
        if t.ref in swept_refs:
            err(f"{t.short} 同時被固定設定與掃描", b)
            continue
        try:
            v = parse_quantity(b.value, b.unit)
        except LabControlError as e:
            err(str(e), b)
            continue
        if t.limits and not (t.limits[0] <= v <= t.limits[1]):
            err(f"設定值超出 {t.short} 的安全上下限", b)
        if scheme.ancestors(b.id):
            info("迴圈內的固定設定只會在開始前設定一次", b)
        setup[target_param_ref(t)] = v
    # 兩台交錯的電磁鐵組：每次量測都明確設定交錯網格（異步 = 每台步進、原點 = 起點；其他 = 0 → resolution）
    for b in flat:
        if b.kind != "set":
            continue
        t = cat.target(b.target)
        if t is None or not t.interleave:
            continue
        scale = split_unit(b.unit)[1] or 1.0
        async_ = b.is_loop and b.interleave and b.step and b.start is not None
        setup[f"{t.ref}.interleave_step"] = abs(b.step) * scale if async_ else 0.0
        setup[f"{t.ref}.interleave_origin"] = b.start * scale if async_ else 0.0
    for m in measures:
        mm = cat.measurer(m.instrument)
        if mm is None:
            continue
        settings = {}
        for k, v in (m.settings or {}).items():
            if f"{m.instrument}.{k}" in swept_refs or f"{m.instrument}.{k}" in setup:
                info(f"{mm.field_label(k)}由設定 / 掃描方塊控制，量測方塊裡的值不使用", m)
                continue
            settings[k] = v
        if m.instrument in setup and setup[m.instrument] != settings:
            err(f"{m.instrument} 在兩個量測方塊中的設定不同", m)
        setup[m.instrument] = settings

    # ---- Data（可以有多個：每個 Data 各自決定分檔與檔名）----
    split_by: List[str] = []
    save = saves[0] if saves else None
    res.save = save
    res.saves = list(saves)
    fs = file_settings(scheme, save)
    extras: List[Dict[str, Any]] = []
    if save is None:
        if fs["formats"]:
            info("沒有 Data 節點：整個量測存成一個檔（要每個外圈值一個檔，把 Data 節點接在外圈迴圈裡）")
        else:
            warn("沒有選存檔格式：只會寫 _raw/ 原始檔")
    names_files = {}
    for k, sv in enumerate(saves):
        if measures and order[sv.id] < max(order[m.id] for m in measures):
            err("Data 必須放在量測方塊之後", sv)
        anc = scheme.ancestors(sv.id)
        if [a.id for a in anc] != [c.id for c in chain[:len(anc)]]:
            err("Data 必須在量測所在的迴圈鏈上", sv)
            continue
        depth = len(anc)
        by = [lp.axis_name for lp in res.loops[:depth]]
        if chain and depth == len(chain):
            warn("Data 在最內層迴圈裡：每一個點都會存成一個檔", sv)
        if k == 0:
            res.split_depth = depth
            split_by = by
            if not fs["formats"]:
                warn("沒有選存檔格式，只會寫 _raw/ 原始檔", sv)
        else:
            if not sv.formats:
                warn("這個 Data 沒有選存檔格式，不會存檔", sv)
            extras.append({"block": sv.id, "depth": depth, "split_by": by,
                           "file_name": sv.file_name or f"{scheme.name}_{k + 1}.hdf5",
                           "formats": list(sv.formats), "collision": sv.collision or fs["collision"]})
        key = ((fs["file_name"] if k == 0 else (sv.file_name or f"{scheme.name}_{k + 1}.hdf5")), depth)
        if key in names_files:
            warn(f"和另一個 Data 的檔名與分檔方式相同（{key[0]}），會存成 _2、_3…", sv)
        names_files[key] = sv.id
    res.extras = extras

    settle_first = sum(float(b.seconds or 0) for b in scheme.blocks if b.kind == "wait")
    if delay and not res.loops:
        settle_first += delay

    # ---- 估算 ----
    ns = [lp.n for lp in res.loops]
    res.total_points = int(np.prod(ns)) if ns else 1
    res.n_files = (int(np.prod(ns[:res.split_depth])) if res.split_depth else 1) if fs["formats"] else 0
    for e in extras:
        if e["formats"]:
            res.n_files += int(np.prod(ns[:e["depth"]])) if e["depth"] else 1
    t_meas = sum(_measure_time(m, cat) for m in measures) * res.total_points
    t_settle = 0.0
    t_reset = 0.0
    resets = []   # (迴圈, 每次秒數, 次數)
    for k, lp in enumerate(res.loops):
        outer = int(np.prod(ns[:k])) if k else 1
        t_settle += lp.settle * lp.n * outer
        if k and not (scheme.snake or lp.block.alternate) and outer > 1:
            span = float(abs(lp.values[-1] - lp.values[0]))
            rate, jump = lp.target.ramp_rate, lp.target.max_jump
            if rate and jump is not None and span > jump:
                t_reset += (outer - 1) * span / rate
                resets.append((lp, span / rate, outer - 1))
    res.est_breakdown = {"量測": t_meas, "等待": t_settle + settle_first, "內圈回到起點（斜坡）": t_reset}
    res.est_seconds = sum(res.est_breakdown.values())
    for lp, each, count in resets:
        if each * count > 0.2 * max(res.est_seconds, 1e-9):
            info(f"{lp.axis_name}每圈結束要斜坡回起點約 {each:.0f} s（共 {count} 次，約 {format_duration(each * count)}）；"
                 f"在這個掃描節點勾選「來回掃」可省下這段時間", lp.block)

    if not res.ok:
        return res

    # ---- 產生實驗設定 ----
    readouts = []
    for m in measures:
        mm = cat.measurer(m.instrument)
        for tr in m.traces:
            readouts.append({"ref": m.instrument, "trace": tr, "export_name": f"{mm.labber_name} - {tr}"})
    sweep = []
    for lp in res.loops:
        d = _axis_dict(lp.block, lp.target)
        d.update(name=lp.axis_name, settle=lp.settle)
        sweep.append(d)
    park = {}
    for lp in res.loops:     # Labber「After last step」：每一軸各自設定
        b = lp.block
        if b.after == "stay":
            park[lp.axis_name] = "none"
        elif b.after == "value" and b.after_value is not None:
            park[lp.axis_name] = f"{b.after_value} {b.unit}"
        else:
            park[lp.axis_name] = "start"
    if not sweep:   # 沒有迴圈：只量一次
        sweep = [{"target": "", "name": "Repeat", "values": [0]}]
    fs = file_settings(scheme, save)
    export = []
    for f in fs["formats"]:
        if f == "labber":
            export.append({"type": "labber", "project": fs["project"], "user": fs["user"], "tags": fs["tags"],
                           **({"comment": fs["comment"]} if fs["comment"] else {})})
        else:
            export.append({"type": f})
    output = {k: v for k, v in scheme.output.items()
              if k not in ("formats", "project", "user", "tags", "comment", "file_name", "collision")}
    output.update(raw=bool(setting("data.raw", True)), export=export, file_name=fs["file_name"],
                  collision=fs["collision"])
    if split_by:
        output["split_by"] = split_by

    def _export_list(formats: List[str]) -> List[Dict[str, Any]]:
        out = []
        for f in formats:
            if f == "labber":
                out.append({"type": "labber", "project": fs["project"], "user": fs["user"], "tags": fs["tags"],
                            **({"comment": fs["comment"]} if fs["comment"] else {})})
            else:
                out.append({"type": f})
        return out
    if extras:
        output["extra"] = [{"file_name": e["file_name"], "collision": e["collision"], "split_by": e["split_by"],
                            "export": _export_list(e["formats"])} for e in extras if e["formats"]]
    run = {k: v for k, v in scheme.run.items() if k != "point_delay"}
    if settle_first:
        run["settle_first"] = settle_first
    if any(v != "start" for v in park.values()):
        run["park"] = park
    res.config = {"name": scheme.name, "procedure": {"type": "trace_sweep", "readouts": readouts},
                  "setup": setup, "sweep": sweep, "snake": scheme.snake, "run": run,
                  "hooks": list(scheme.hooks), "output": output, "scheme": scheme.to_dict()}
    return res


def format_duration(sec: Optional[float]) -> str:
    if sec is None or not math.isfinite(sec):
        return "—"
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h} 小時 {m:02d} 分" if h else f"{m} 分 {s:02d} 秒"
