"""把讀檔程式（LabLogViewer）解析 HDF5 的核心程式複製到 labhub/llvcore/（1.0.4）。

NAS 上的數據索引（labhub/indexer.py）用同一份程式解析檔案，結果才會跟讀檔程式自己掃描的一樣。
讀檔程式改了解析方式時重新執行：

    python tools/sync_llvcore.py <LabLogViewer 資料夾>

只複製不需要 Qt 的模組（清單見 FILES）；llvcore/SOURCE.json 記錄讀檔程式版本與每個檔的 sha256。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "labhub" / "llvcore"
FILES = [
    "app/__init__.py",
    "app/core/__init__.py",
    "app/core/data_identity.py",
    "app/core/data_location.py",
    "app/core/data_model.py",
    "app/core/database_index_store.py",
    "app/core/database_scanner.py",
    "app/core/external_state.py",
    "app/core/file_lock.py",
    "app/core/formula.py",
    "app/core/hdf5_processing_writer.py",
    "app/core/hdf5_reader.py",
    "app/core/hub_index.py",
    "app/core/labber_parser.py",
    "app/core/lab_share.py",
    "app/core/labcontrol_nc.py",
    "app/core/net_cache.py",
    "app/core/quick_metadata.py",
    "app/core/quick_preview.py",
    "app/core/safe_expr.py",
    "app/core/win_paths.py",
    "app/plotting/__init__.py",
    "app/plotting/complex_transform.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_info(src: Path) -> dict:
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (src / "app" / "__init__.py").read_text(encoding="utf-8"))
    return {"llv_version": version.group(1) if version else "?", "files": {f: sha(src / f) for f in FILES}}


def sync(src: Path, dest: Path = DEST) -> dict:
    src = Path(src)
    missing = [f for f in FILES if not (src / f).is_file()]
    if missing:
        raise SystemExit(f"{src} 不是讀檔程式資料夾（缺 {', '.join(missing)}）")
    if dest.exists():
        shutil.rmtree(dest)
    for f in FILES:
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / f, dest / f)
    info = source_info(src)
    (dest / "SOURCE.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    (dest / "README.md").write_text(
        "# llvcore\n\n讀檔程式（LabLogViewer）解析 HDF5 的核心程式的複本，給 NAS 上的數據索引用（labhub/indexer.py）。\n"
        "不要直接改這裡；改讀檔程式後執行 `python tools/sync_llvcore.py <LabLogViewer 資料夾>`。\n",
        encoding="utf-8")
    return info


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    info = sync(Path(sys.argv[1]))
    print(f"llvcore ← LabLogViewer {info['llv_version']}（{len(info['files'])} 個檔）")
