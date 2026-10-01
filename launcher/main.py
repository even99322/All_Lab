"""QEL Lab 大程式（桌面）啟動程式。

先看 ``<QEL_HOME>/modules/launcher/`` 有沒有比這份更新的大程式（大程式自我更新後放在那裡），
有就改用新版；再把通信模塊（labcomm）加到搜尋路徑。這個檔案本身很少需要改。
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _ver(v):
    import re
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def _home() -> Path:
    p = os.environ.get("QEL_HOME")
    return Path(p).expanduser() if p else Path.home() / "QELLab"


def _installed(mid: str):
    d = _home() / "modules" / mid
    try:
        v = json.loads((d / "current.json").read_text(encoding="utf-8"))["version"]
        return v, d / v
    except (OSError, ValueError, KeyError):
        return None, None


def _bundled_version() -> str:
    import re
    t = (HERE / "qellauncher" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', t).group(1)


def main() -> int:
    base = HERE
    v, d = _installed("launcher")
    if v and d and (d / "qellauncher").is_dir() and _ver(v) > _ver(_bundled_version()) \
            and os.environ.get("QEL_LAUNCHER_PINNED") != "1":
        base = d
    sys.path.insert(0, str(base))
    _, lc = _installed("labcomm")
    for p in (lc, HERE.parent / "comm", HERE / "vendor"):
        if p and (Path(p) / "labcomm").is_dir():
            sys.path.insert(1, str(p))
            break
    from qellauncher.app import main as run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
