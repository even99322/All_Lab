"""1.0.33：儀器改名時，把所有用到舊名稱的地方一起改成新名稱。

instedit.rename_instrument 改完 instruments.yaml / 量測電腦對應後呼叫 propagate()：
  * 量測方案：LAB/schemes/**（含自動儲存）、LAB/templates/、專案範本快取 LAB/project_templates/**（改了會送回 Hub，
    別台電腦也拿到新名稱）——設定方塊的 target（magnet_A、VNA1.power、DC1.ch1.level）、量測方塊的 instrument、
    「背景＋去背」的電源、hooks。
  * 即時數據：電源群組與 M1–M3 記憶（live_groups.json）、＋／－步進（live_steps.json）、曲線排法（live_ui.json）、
    VNA 接線的 port（vna_ports.json）。
  * 目前開著的方案：工作台收到 station.changed（action=rename）自己改（apps/qt/workbench/window.py）。
跨電腦：別台收到儀器同步的改名（remote/instsync.py）也走 instedit.rename_instrument → 也會跑這裡。
以前的數據檔、實驗記錄不改（那是當時的紀錄）。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)

JSON_STORES = ("live_groups.json", "live_steps.json", "live_ui.json", "vna_ports.json")


def ref(s: Any, old: str, new: str) -> Any:
    """"old" → "new"；"old.ch1.level" → "new.ch1.level"；其他不動。"""
    if not isinstance(s, str):
        return s
    if s == old:
        return new
    if s.startswith(old + "."):
        return new + s[len(old):]
    return s


def deep(obj: Any, old: str, new: str) -> Tuple[Any, bool]:
    """JSON 結構裡的字串值、dict 的 key（含 "a|b" 這種組合 key）都改。回傳 (新物件, 有沒有改)。"""
    if isinstance(obj, str):
        r = ref(obj, old, new)
        return r, r != obj
    if isinstance(obj, list):
        out, ch = [], False
        for x in obj:
            y, c = deep(x, old, new)
            out.append(y)
            ch = ch or c
        return out, ch
    if isinstance(obj, dict):
        out_d: Dict[Any, Any] = {}
        ch = False
        for k, v in obj.items():
            k2 = k
            if isinstance(k, str):
                k2 = "|".join(ref(p, old, new) for p in k.split("|")) if "|" in k else ref(k, old, new)
            v2, c = deep(v, old, new)
            if k2 != k or c:
                ch = True
            out_d[k2] = v2
        return out_d, ch
    return obj, False


def rename_scheme(scheme, old: str, new: str) -> bool:
    """直接改 Scheme 物件；回傳有沒有改。"""
    changed = False
    blocks = list(scheme.graph.nodes.values()) if scheme.graph is not None else list(scheme.flat())
    for b in blocks:
        if b.kind == "set" and b.target:
            t = ref(b.target, old, new)
            if t != b.target:
                b.target, changed = t, True
        elif b.kind == "measure" and b.instrument == old:
            b.instrument, changed = new, True
    if scheme.background:
        for s in scheme.background.get("sets") or []:
            t = ref(str(s.get("target", "")), old, new)
            if t != s.get("target"):
                s["target"], changed = t, True
    if scheme.hooks:
        hooks, c = deep(scheme.hooks, old, new)
        if c:
            scheme.hooks, changed = hooks, True
    if changed and scheme.graph is not None:
        scheme.sync_from_graph()
    return changed


def rename_scheme_dict(d: Dict[str, Any], old: str, new: str) -> Tuple[Dict[str, Any], bool]:
    from ..scheme.model import Scheme

    sch = Scheme.from_dict(d)
    if not rename_scheme(sch, old, new):
        return d, False
    return sch.to_dict(), True


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.rename.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _scheme_files(root: Path) -> List[Path]:
    out: List[Path] = []
    for d in (root / "schemes", root / "templates"):
        if d.is_dir():
            out += [p for p in d.rglob("*.yaml") if p.is_file()] + [p for p in d.rglob("*.yml") if p.is_file()]
    return out


def propagate(old: str, new: str, root: Path | None = None, push_projects: bool = True) -> List[str]:
    """改這台電腦上存的所有引用。回傳改到的檔案（相對 LAB 資料夾）。"""
    if not old or not new or old == new:
        return []
    if root is None:
        from ..paths import lab_path

        root = lab_path()
    root = Path(root)
    touched: List[str] = []

    from ..scheme.model import Scheme

    for p in _scheme_files(root):
        try:
            sch = Scheme.load(p)
        except Exception:  # noqa: BLE001 - 不是方案檔或壞掉：跳過
            continue
        if rename_scheme(sch, old, new):
            try:
                sch.save(p)
                touched.append(str(p.relative_to(root)))
            except OSError as e:
                log.warning("改方案 %s 失敗：%s", p, e)

    for name in JSON_STORES:
        p = root / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data2, ch = deep(data, old, new)                     # 群組名稱 = 儀器名稱的也會一起改
        if ch:
            try:
                _write_json(p, data2)
                touched.append(name)
            except OSError as e:
                log.warning("改 %s 失敗：%s", name, e)

    pt = root / "project_templates"
    if pt.is_dir():
        for f in pt.glob("*/*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get("scheme"), dict):
                continue
            d2, ch = rename_scheme_dict(rec["scheme"], old, new)
            if not ch:
                continue
            touched.append(str(f.relative_to(root)))
            try:
                if push_projects:
                    from ..integrations import project_templates

                    project_templates.save(f.parent.name, f.stem, d2, push=False)     # 背景送回 Hub
                else:
                    _write_json(f, dict(rec, scheme=d2))
            except Exception as e:  # noqa: BLE001
                log.warning("改專案範本 %s 失敗：%s", f, e)
    if touched:
        log.info("儀器改名 %s → %s：一併改了 %s", old, new, "、".join(touched))
    return touched
