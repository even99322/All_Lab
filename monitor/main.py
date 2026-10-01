"""QEL Lab 監控程式：python main.py（需要 PySide6；通信模塊 labcomm 在 ../comm 或已安裝）。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "comm", HERE / "vendor"):
    if (p / "labcomm").exists() or (p / "qelmonitor").exists():
        sys.path.insert(0, str(p))

from qelmonitor.app import main  # noqa: E402

raise SystemExit(main())
