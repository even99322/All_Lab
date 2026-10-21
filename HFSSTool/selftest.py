"""打包後的自我檢查：HFSSTool.exe --self-test（雲端打包時執行；不需要 AEDT）。

結果寫到 %TEMP%\\hfss_selftest.log，回傳碼 0 = 通過。
"""
import os
import sys
import tempfile
import traceback


def _probe(q):
    q.put("ok")


def run() -> int:
    os.environ["HFSS_NO_UPDATE_CHECK"] = "1"
    log = os.path.join(tempfile.gettempdir(), "hfss_selftest.log")
    import faulthandler
    crash = open(log + ".crash", "w", encoding="utf-8")
    faulthandler.enable(file=crash, all_threads=True)          # 原生程式當掉時留下 Python 的位置
    try:
        from PyQt6.QtCore import qInstallMessageHandler

        def qt_msg(mode, ctx, msg):
            crash.write(f"Qt {mode}: {msg}\n")
            crash.flush()
        qInstallMessageHandler(qt_msg)
    except Exception:  # noqa: BLE001
        pass
    lines = []
    keep = []
    ok = True

    def write():
        with open(log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def step(name, fn):
        nonlocal ok
        lines.append(f"...  {name}")                # 當掉的話，log 停在這一步
        write()
        try:
            r = fn()
            lines[-1] = f"OK   {name}{'' if r is None else ': ' + str(r)}"
        except BaseException:
            ok = False
            lines[-1] = f"FAIL {name}\n{traceback.format_exc()}"
        write()

    step("ansys.aedt.core", lambda: __import__("ansys.aedt.core", fromlist=["Hfss", "Desktop"]).__version__)
    step("Hfss / Desktop", lambda: [getattr(__import__("ansys.aedt.core", fromlist=["x"]), n) for n in ("Hfss", "Desktop")] and None)
    step("get_dxf_layers", lambda: __import__("ansys.aedt.core.generic.file_utils", fromlist=["get_dxf_layers"]).get_dxf_layers)
    def meta():
        from utils import pyaedt_version_tuple
        t, v = pyaedt_version_tuple()
        if t is None:
            raise RuntimeError("讀不到 pyaedt 的版本（打包時少了套件資訊）")
        return v
    step("pyaedt 版本", meta)
    step("pythoncom", lambda: __import__("pythoncom") and None)
    step("numpy / pandas / PIL / psutil", lambda: [__import__(m) for m in ("numpy", "pandas", "PIL.Image", "psutil")] and None)
    step("matplotlib qtagg", lambda: __import__("matplotlib.backends.backend_qtagg") and None)

    def gui():
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv[:1])
        keep.append(app)                       # 留著：不然 QApplication 被回收，下一步建視窗會讓程式直接結束
        from ui_main import MainWindow
        w = MainWindow()
        w.close()
        from mode_help import ModeHelpDialog  # noqa: F401
        return app.platformName()
    step("介面", gui)

    def need_app():
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            keep.append(QApplication(sys.argv[:1]))

    step("ezdxf", lambda: __import__("ezdxf").__version__)
    step("shapely", lambda: __import__("shapely").__version__)

    def buf():
        from shapely.geometry import LineString
        return round(LineString([(0, 0), (10, 0), (10, 5)]).buffer(0.5, cap_style="flat", join_style="mitre").area, 3)
    step("shapely 運算", buf)

    def dxf():
        from dxf_model import Design
        d = Design.template("CPW")
        f = os.path.join(tempfile.gettempdir(), "hfss_selftest.dxf")
        d.save(f)
        r = Design.load(f)
        os.remove(f)
        if not r.auto_ground or len(r.generated()["ub"]) != 2:
            raise RuntimeError("CPW 範本讀回來不對")
        return "CPW 範本存檔 / 讀回"
    step("DXF 檔案", dxf)

    def editor():
        need_app()
        from dxf_editor import DxfEditor
        w = DxfEditor(mode="MS")
        w.dirty = False
        w.close()
        return "ok"
    step("DXF 編輯器", editor)

    def upd():
        import updater
        s = updater.install_script("C:/a b/new.exe", "C:/a'b/HFSSTool.exe", [1, 2], updater.current_version())
        if "C:/a''b/HFSSTool.exe" not in s:
            raise RuntimeError("換檔腳本的路徑沒有跳脫")
        return f"{updater.current_version()} 可以自己換新：{updater.can_self_update()}"
    step("自動更新", upd)

    def spawn():
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_probe, args=(q,))
        p.start()
        r = q.get(timeout=120)
        p.join(60)
        return r
    step("獨立程序", spawn)

    write()
    print("\n".join(lines))
    return 0 if ok else 1
