"""Lab Control 進入點（Lab APP 的 .entry；打包版 PyInstaller 的入口）。

打包版（.exe / .app）的「功能更新包」（0.0.27）：
  .exe 裡的 Python、numpy、PyQt6… 是「執行環境」，Lab Control 自己的程式碼可以單獨更新。
  程式內更新下載 Hub 上的 Python zip，解壓到 ~/.qel/labcontrol/code/<版本>/；下次開啟時，這裡把它放在
  打包內容的前面（labcontrol、labhub、labmonitor、qelcomm 從那裡載入），所以只要幾秒、約 1 MB 就能更新功能。
  * 只用「執行環境相同」的版本（requirements 指紋與這個 .exe 一樣；不同時要換整個 .exe，程式會自己處理）；
  * 新版開不起來（沒有回報「已正常開啟」）→ 下次開啟自動退回上一個可用的版本，並記下這版不再用；
  * 環境變數 LABCONTROL_NO_OVERLAY=1 → 只用 .exe 內附的版本（救援用）。
這段只用標準函式庫，而且一定是 .exe 內附的這一份（不會被更新掉）。
"""
import sys


def _overlay() -> None:
    import os

    if not getattr(sys, "frozen", False) or os.environ.get("LABCONTROL_NO_OVERLAY"):
        return
    try:
        import importlib.machinery
        import json
        import re
        from pathlib import Path

        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        runtime = json.loads((bundle / "runtime.json").read_text(encoding="utf-8"))
        base = Path(os.environ.get("LABCONTROL_CODE_DIR") or (Path.home() / ".qel" / "labcontrol" / "code"))
        state_file = base / "state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}

        def key(v):
            return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])

        bad = set(state.get("bad") or [])
        trial = state.get("trial")
        import time

        recent = abs(time.time() - float(state.get("trial_at") or 0)) < 30
        if trial and trial != state.get("good") and not recent:   # 上次試的新版沒有正常開啟 → 不再用它
            bad.add(trial)                                # （30 秒內又開一次：可能是同時開兩個，先不判定）
            state["trial"] = None
        cands = []
        for d in (sorted(base.iterdir()) if base.is_dir() else []):
            if not d.is_dir() or not (d / ".complete").exists() or d.name in bad:
                continue
            try:
                meta = json.loads((d / ".complete").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("requirements") != runtime.get("requirements"):
                continue                                  # 執行環境不同：要換整個 .exe
            if key(d.name) > key(runtime.get("version")):
                cands.append(d)
        cands.sort(key=lambda d: key(d.name), reverse=True)
        state["bad"] = sorted(bad)
        if not cands:
            os.environ["LABCONTROL_BASE_VERSION"] = str(runtime.get("version"))
            _write(state_file, state)
            return
        root = cands[0]
        if root.name != state.get("good") and state.get("trial") != root.name:
            state["trial"] = root.name                    # 開啟成功後程式會改成 good（selfupdate.mark_good）
            state["trial_at"] = time.time()
        _write(state_file, state)
        os.environ["LABCONTROL_BASE_VERSION"] = str(runtime.get("version"))
        os.environ["LABCONTROL_CODE_VERSION"] = root.name
        os.environ["LABCONTROL_CODE_ROOT"] = str(root)
        pkgs = {"labcontrol", "labhub", "labmonitor", "qelcomm"}

        class _Overlay:
            """把功能更新包的程式碼放在打包內容前面（PyInstaller 的載入器排在 sys.path 之前，所以用 meta_path）。"""

            @staticmethod
            def find_spec(name, path=None, target=None):
                if name.split(".", 1)[0] not in pkgs:
                    return None
                return importlib.machinery.PathFinder.find_spec(name, path if "." in name else [str(root)])

            @staticmethod
            def invalidate_caches():
                pass

        sys.meta_path.insert(0, _Overlay())
        sys.path.insert(0, str(root))
    except Exception:  # noqa: BLE001 - 任何問題都用 .exe 內附的版本
        return


def _write(path, data) -> None:
    import json

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _without_overlay(err: BaseException) -> None:
    """功能更新包一開始就載入失敗（例如需要 .exe 沒有的模組）：記下這版不再用，改用 .exe 內附的版本。"""
    import os

    ver = os.environ.pop("LABCONTROL_CODE_VERSION", "")
    root = os.environ.pop("LABCONTROL_CODE_ROOT", "")
    sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "_Overlay"]
    if root in sys.path:
        sys.path.remove(root)
    for name in [n for n in sys.modules if n.split(".", 1)[0] in ("labcontrol", "labhub", "labmonitor", "qelcomm")]:
        del sys.modules[name]
    try:
        import json
        from pathlib import Path

        state_file = Path(root).parent / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
        state["bad"] = sorted(set(state.get("bad") or []) | {ver})
        state["trial"] = None
        state["last_error"] = f"{ver}: {type(err).__name__}: {err}"
        _write(state_file, state)
    except Exception:  # noqa: BLE001
        pass


_overlay()
try:
    from labcontrol.app import main  # noqa: E402
except Exception as _e:  # noqa: BLE001
    import os as _os

    if not _os.environ.get("LABCONTROL_CODE_ROOT"):
        raise
    _without_overlay(_e)
    from labcontrol.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
