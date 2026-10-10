"""範本方案：LAB/templates/*.scheme.yaml（第一次啟動時從 labcontrol/defaults/templates 複製）。

程式碼裡沒有任何範本內容；要新增範本，把方案另存到 LAB/templates/ 即可。
TEMPLATES 是即時讀取的對照表：{檔名（不含 .scheme.yaml）: (顯示名稱, 產生 Scheme 的函式)}。
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from ..paths import DEFAULTS_DIR, lab_path
from .model import Scheme

SUFFIX = ".scheme.yaml"


def template_dirs() -> List[Path]:
    """LAB/templates 優先；同名時 LAB 的版本蓋過內建預設。"""
    return [lab_path("templates"), DEFAULTS_DIR / "templates"]


def template_files() -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for d in reversed(template_dirs()):
        if d.is_dir():
            for f in sorted(d.glob(f"*{SUFFIX}")):
                out[f.name[:-len(SUFFIX)]] = f
    return dict(sorted(out.items()))


def load_template(key: str) -> Scheme:
    files = template_files()
    if key not in files:
        raise KeyError(f"找不到範本 '{key}'（{', '.join(files) or '無'}）")
    return Scheme.load(files[key])


def default_template() -> Optional[Scheme]:
    from ..settings import setting

    files = template_files()
    key = setting("app.default_template", "")
    if key in files:
        return load_template(key)
    return load_template(next(iter(files))) if files else None


class _Templates(Mapping):
    def _items(self) -> Dict[str, Tuple[str, Callable[[], Scheme]]]:
        out = {}
        for key, path in template_files().items():
            try:
                label = Scheme.load(path).name or key
            except Exception:  # noqa: BLE001 — 壞掉的範本不影響其他範本
                continue
            out[key] = (label, (lambda p=path: Scheme.load(p)))
        return out

    def __getitem__(self, key: str) -> Tuple[str, Callable[[], Scheme]]:
        return self._items()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items())

    def __len__(self) -> int:
        return len(self._items())


TEMPLATES = _Templates()
