"""1.0.31：量測結束 → 在 Hub 的「實驗記錄」自動新增一筆（量測方案、掃描範圍、儀器設定、存的檔、狀態）。

Experiment.export 存完檔後呼叫 post_run()；在背景執行緒送出，Hub 連不到只記 log，不影響量測。
同一個 run_id 只會有一筆（再送一次 = 更新那筆，例如之後補存的檔）。settings.yaml notebook.auto: false 可以關掉。
"""
from __future__ import annotations

import datetime as _dt
import getpass
import logging
import socket
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from ..settings import setting

log = logging.getLogger(__name__)


def _ts(iso: Any) -> Optional[float]:
    try:
        return _dt.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


def _val(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(_val(x) for x in v)[:80]
    return str(v)[:80]


def _node_name() -> str:
    try:
        from ..remote.hub import default_node_name

        return default_node_name()
    except Exception:  # noqa: BLE001
        return ""


def record(exp, dataset, paths: Sequence[Path], status: str = "") -> Dict[str, Any]:
    """量測的記錄內容（給 Hub /api/notebook/auto）。"""
    meta = dict(getattr(dataset, "metadata", {}) or {})
    cfg = exp.config or {}
    axes = []
    for a in getattr(dataset, "axes", []) or []:
        if not a.target and len(a.values) <= 1:
            continue
        sc = float(a.display_scale or 1.0)
        vals = list(a.values)
        try:
            start, stop = float(vals[0]) * sc, float(vals[-1]) * sc
        except (TypeError, ValueError, IndexError):
            start = stop = None
        axes.append({"name": a.name, "unit": a.display_unit or a.unit, "start": start, "stop": stop,
                     "n": len(vals), "target": a.target})
    setup = dict(cfg.get("setup") or {})
    measures: Dict[str, Dict[str, Any]] = {}
    for r in ((cfg.get("procedure") or {}).get("readouts") or []):
        ref = str(r.get("ref") or "")
        m = measures.setdefault(ref, {"instrument": ref, "traces": [], "settings": {}})
        if r.get("trace") and r["trace"] not in m["traces"]:
            m["traces"].append(str(r["trace"]))
        if isinstance(setup.get(ref), dict):
            m["settings"] = {str(k): _val(v) for k, v in setup[ref].items()}
    fixed = {str(k): _val(v) for k, v in setup.items() if not isinstance(v, dict)}
    n, planned = len(dataset), int(getattr(dataset, "planned_points", 0) or 0)
    if not status:
        status = "finished" if planned and n >= planned else "stopped"
    out_cfg = cfg.get("output") or {}
    comment = ""
    for e in out_cfg.get("export") or []:
        if isinstance(e, dict) and e.get("comment"):
            comment = str(e["comment"])
    user = ""
    for e in out_cfg.get("export") or []:
        if isinstance(e, dict) and e.get("user"):
            user = str(e["user"])
    try:
        user = user or getpass.getuser()
    except Exception:  # noqa: BLE001
        pass
    from .. import __version__

    return {"run_id": str(meta.get("run_id") or ""), "scheme": exp.name, "status": status,
            "host": socket.gethostname(), "node": _node_name(), "user": user,
            "started": _ts(meta.get("created")), "finished": _dt.datetime.now().timestamp(),
            "points": n, "planned": planned, "axes": axes, "measures": list(measures.values()), "setup": fixed,
            "files": [Path(p).name for p in paths], "comment": comment, "tags": exp.output_tags(),
            "project": exp.output_project(), "version": __version__}


def post_run(exp, dataset, paths: Sequence[Path], status: str = "", wait: bool = False) -> Optional[threading.Thread]:
    """背景送出（wait=True：等送完，測試用）。"""
    from ..remote.hub import hub_token

    if not setting("notebook.auto", True) or not hub_token():
        return None                                    # 關掉，或這台沒有設定 Hub token（送不進去）
    try:
        rec = record(exp, dataset, paths, status)
    except Exception:  # noqa: BLE001
        log.exception("實驗記錄內容整理失敗")
        return None
    if not rec["run_id"]:
        return None

    def go() -> None:
        try:
            from .platform import cached_hub

            r = cached_hub().post("/api/notebook/auto", rec)
            log.info("已寫入實驗記錄 %s", (r or {}).get("id", ""))
        except Exception as e:  # noqa: BLE001
            log.info("實驗記錄沒有送到 Hub：%s", e)

    t = threading.Thread(target=go, name="notebook-post", daemon=True)
    t.start()
    if wait:
        t.join(30)
    return t

