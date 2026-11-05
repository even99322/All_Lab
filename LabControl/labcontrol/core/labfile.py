"""編輯 LAB/instruments.yaml（儀器伺服器「新增 / 移除 / 停用」用）。

用 ruamel.yaml 做「來回編輯」：保留註解、順序、YAML anchor（<<: *yoko）。
每次寫入前先把原檔備份到 LAB/logs/backup/instruments.yaml.<時間>。
沒有安裝 ruamel.yaml 時退回 PyYAML（功能相同，但註解會消失，會先警告）。
"""
from __future__ import annotations

import datetime as _dt
import io
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import ConfigError

try:  # pragma: no cover - 依安裝環境
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    HAS_RUAMEL = True
except ImportError:  # pragma: no cover
    HAS_RUAMEL = False


#: 同一個程序裡的寫入互斥（儀器伺服器、量測節點的指令、跨電腦同步可能同時改 instruments.yaml）
LOCK = threading.RLock()


def _yaml():
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load(path: Path) -> Any:
    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else "instruments: {}\n"
    if HAS_RUAMEL:
        data = _yaml().load(text) or CommentedMap()
    else:
        import yaml
        data = yaml.safe_load(text) or {}
    if data.get("instruments") is None:
        data["instruments"] = CommentedMap() if HAS_RUAMEL else {}
    return data


def backup(path: Path) -> Optional[Path]:
    path = Path(path)
    if not path.exists():
        return None
    d = path.parent / "logs" / "backup"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{path.name}.{_dt.datetime.now():%Y%m%d-%H%M%S-%f}"
    shutil.copy2(path, dst)
    return dst


def save(path: Path, data: Any, make_backup: bool = True) -> None:
    if make_backup:
        backup(path)
    if HAS_RUAMEL:
        buf = io.StringIO()
        _yaml().dump(data, buf)
        text = buf.getvalue()
    else:
        import yaml
        text = yaml.safe_dump(_plain(data), allow_unicode=True, sort_keys=False)
    # 寫入前先確認新內容讀得回來（避免寫壞設定檔）
    import yaml as _pyyaml
    _pyyaml.safe_load(text)
    Path(path).write_text(text, encoding="utf-8")


def _plain(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def configured(path: Path) -> List[Tuple[str, Dict[str, Any]]]:
    """instruments.yaml 裡所有儀器（含 enabled: false 的），anchor 已展開。"""
    import yaml

    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    data = yaml.safe_load(text) or {}
    return [(str(k), dict(v or {})) for k, v in (data.get("instruments") or {}).items()]


def add_instrument(path: Path, name: str, options: Dict[str, Any]) -> None:
    with LOCK:
        data = load(path)
        inst = data["instruments"]
        if name in inst:
            raise ConfigError(f"instruments.yaml 已經有 {name}")
        inst[name] = _to_node(options)
        save(path, data)


def remove_instrument(path: Path, name: str) -> None:
    with LOCK:
        data = load(path)
        if name in data["instruments"]:
            del data["instruments"][name]
            save(path, data)


def update_instrument(path: Path, name: str, updates: Dict[str, Any], remove: Tuple[str, ...] = ()) -> None:
    """合併更新一台儀器的欄位（例如 {"enabled": False}、{"address": ...}）。"""
    with LOCK:
        data = load(path)
        inst = data["instruments"]
        if name not in inst:
            raise ConfigError(f"instruments.yaml 沒有 {name}")
        node = inst[name]
        for k, v in updates.items():
            node[k] = _to_node(v)
        for k in remove:
            if k in node:
                del node[k]
        save(path, data)


def _rename_ref(ref: Any, old: str, new: str) -> Any:
    s = str(ref)
    if s == old:
        return new
    if s.startswith(old + "."):
        return new + s[len(old):]
    return ref


def rename_instrument(path: Path, old: str, new: str) -> List[str]:
    """把 old 改名成 new（保留順序、註解）；其他儀器 sources 裡的 old / old.ch1 也一起改。回傳被改到的其他儀器。"""
    with LOCK:
        data = load(path)
        inst = data["instruments"]
        if old not in inst:
            raise ConfigError(f"instruments.yaml 沒有 {old}")
        if new in inst:
            raise ConfigError(f"instruments.yaml 已經有 {new}")
        keys = list(inst.keys())
        pos = keys.index(old)
        node = inst.pop(old)
        if HAS_RUAMEL and hasattr(inst, "insert"):
            inst.insert(pos, new, node)
        else:
            items = list(inst.items())
            items.insert(pos, (new, node))
            inst.clear()
            inst.update(items)
        touched = []
        for n in list(inst.keys()):
            o = inst[n]
            srcs = o.get("sources") if hasattr(o, "get") else None
            if not srcs:
                continue
            renamed = [_rename_ref(x, old, new) for x in srcs]
            if [str(x) for x in renamed] != [str(x) for x in srcs]:
                o["sources"] = _to_node(renamed)
                touched.append(str(n))
        save(path, data)
        return touched


def apply_changes(path: Path, add: Optional[Dict[str, Dict[str, Any]]] = None,
                  replace: Optional[Dict[str, Dict[str, Any]]] = None, remove: Optional[List[str]] = None) -> bool:
    """一次寫入多筆變更（跨電腦同步用；只備份一次）。replace：整筆換掉（保留位置）。回傳是否有寫入。"""
    add, replace, remove = dict(add or {}), dict(replace or {}), list(remove or [])
    if not (add or replace or remove):
        return False
    with LOCK:
        data = load(path)
        inst = data["instruments"]
        for n in remove:
            if n in inst:
                del inst[n]
        for n, o in replace.items():
            if n in inst:
                inst[n] = _to_node(o)
            else:
                add[n] = o
        for n, o in add.items():
            if n not in inst:
                inst[n] = _to_node(o)
        save(path, data)
        return True


def _to_node(v: Any) -> Any:
    if not HAS_RUAMEL:
        return v
    if isinstance(v, dict):
        m = CommentedMap()
        for k, x in v.items():
            m[k] = _to_node(x)
        return m
    if isinstance(v, (list, tuple)):
        from ruamel.yaml.comments import CommentedSeq
        s = CommentedSeq([_to_node(x) for x in v])
        if all(not isinstance(x, (dict, list, tuple)) for x in v):
            s.fa.set_flow_style()
        return s
    return v


def set_value(path: Path, key: str, value: Any) -> None:
    """在任一 YAML 設定檔（例如 LAB/settings.yaml）設定一個值（key 用點分隔，如 "app.theme"），保留其他內容與註解。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if HAS_RUAMEL:
        data = _yaml().load(text) or CommentedMap()
    else:
        import yaml
        data = yaml.safe_load(text) or {}
    node = data
    parts = key.split(".")
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = CommentedMap() if HAS_RUAMEL else {}
        node = node[p]
    node[parts[-1]] = value
    save(path, data, make_backup=False)
