"""在真的 Windows／macOS 上檢查打包版的第一次安裝流程：下載可攜版 Python → 建模塊環境 → pip 安裝套件。

GitHub Actions「build-desktop」在打包前執行；同時開 3 個執行緒模擬好幾個模塊一起安裝。
"""
import json
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "comm"), str(ROOT / "launcher")]
sys.frozen = True                                         # 走打包版的路線（可攜版 Python）

from qellauncher import core  # noqa: E402

home = Path(tempfile.mkdtemp(prefix="qelsmoke-"))
store = core.ModuleStore(home)
errors = []


def install(mid):
    try:
        z = home / f"{mid}.zip"
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr(f"{mid}/module.json", json.dumps({"id": mid, "version": "1.0.0", "kind": "desktop",
                                                         "entry": "main.py"}))
            zz.writestr(f"{mid}/main.py", "import yaml\nprint('ok')\n")
            zz.writestr(f"{mid}/requirements.txt", "# 中文註解 — 和 LabLogViewer 一樣（cp950 會解不開）\npyyaml\n")
        store.install_zip(mid, "1.0.0", z)
        py = store.prepare_env(mid, "1.0.0")
        import subprocess
        r = subprocess.run([str(py), str(store.mod_dir(mid) / "1.0.0" / "main.py")], capture_output=True, text=True)
        assert r.stdout.strip() == "ok", r.stderr
        print(f"{mid}: OK ({py})", flush=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{mid}: {type(e).__name__}: {e}")


ts = [threading.Thread(target=install, args=(m,)) for m in ("labcontrol", "lablogviewer", "monitor")]
for t in ts:
    t.start()
for t in ts:
    t.join()
print("python:", core.portable_python_exe(home))
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("smoke OK")
