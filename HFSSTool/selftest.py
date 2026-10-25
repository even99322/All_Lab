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
    def hook(t, v, tb):
        crash.write("Python 例外：" + "".join(traceback.format_exception(t, v, tb)) + "\n")
        crash.flush()
    sys.excepthook = hook
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
        w.show()
        app.processEvents()
        w.close()
        w.deleteLater()                        # 真的刪掉視窗：關閉時的問題在這一步就會出現
        from PyQt6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        app.processEvents()
        from mode_help import ModeHelpDialog  # noqa: F401
        return app.platformName()
    step("介面", gui)

    def need_app():
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            keep.append(QApplication(sys.argv[:1]))

    step("ezdxf", lambda: f'{__import__("ezdxf").__version__}（C 加速：{__import__("ezdxf").options.use_c_ext}）')
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
        w.show()
        w.dirty = False
        w.close()
        w.deleteLater()
        return "ok"
    step("DXF 編輯器", editor)

    def curves():
        import dxf_geom as G
        v = G.path_outline([(0, 0), (10, 0), (10, 10)], 1.0, fillets=[0, 3, 0])
        if not any(b for _x, _y, b in v):
            raise RuntimeError("圓角沒有變成圓弧")
        return f"{len(v)} 個頂點"
    step("DXF 曲線", curves)

    def view3d():
        need_app()
        from dxf_model import Design
        from yig_view import make_view, scene_from
        sc = scene_from(Design.template("CPW"), 0.8, 0.035, {"yig_diameter": 0.4, "yig_gap": 0.1, "yig_pos_mode": "auto"})
        v = make_view()
        v.set_scene(sc)
        v.canvas.draw()
        return f"{len(sc['verts'])} 個頂點"
    step("YIG 3D 預覽", view3d)

    def yhelp():
        need_app()
        from yig_help import YigHelpDialog
        d = YigHelpDialog(None)
        r = d.recalc()
        d.deleteLater()
        return f"Kittel {r['f_k']:.4f} GHz、線寬 {r['lw']:.3g} MHz"
    step("YIG 參數說明", yhelp)

    def cad39():
        need_app()
        import tline
        from dxf_editor import ConvertDialog, DxfEditor
        ed = DxfEditor(mode="MS", substrate=lambda: {"h": 0.813, "material": "Rogers RO4003 (tm)"})
        ed.run_command("L")
        for t in ("0,5", "@10,0", ""):
            ed.run_command(t)
        n = ed.convert_design("CPW", 1.0, tline.gcpw_gap(50, 1.0, 0.813, 3.55), vias=(1.0, 0.3, 0.2))
        dlg = ConvertDialog(ed)
        dlg.deleteLater()
        ed.dirty = False
        ed.deleteLater()
        return f"指令列畫線、MS→CPW（過孔 {n} 個）、MS 50 Ω 線寬 {tline.ms_width(50, 0.813, 3.55):.3f} mm"
    step("DXF 編輯器 v3.9（指令列、標註、MS⇄CPW）", cad39)

    def map39():
        need_app()
        import numpy as np
        import yigmap
        from map_view import MapViewer
        vals = yigmap.bias_points(170, 190, 3)
        fl = yigmap.fields_for(vals, "external_mT", 0.18, 2.005)
        f = np.linspace(4.7, 5.4, 21)
        m = yigmap.MapData(vals, "external_mT", fl, f)
        for i in range(3):
            m.set_row("S21", i, np.ones(21, complex))
        base = os.path.join(tempfile.gettempdir(), "hfss_selftest_yigmap")
        w = MapViewer(m.save(base))
        w.deleteLater()
        m.save_png(base)
        from progress_panel import StepPanel
        StepPanel().deleteLater()
        return "2D 熱圖存檔 / 檢視、步驟清單"
    step("YIG 2D 熱圖", map39)

    def usage():
        import usage as u
        return f"{u.identity()['host']}，AEDT 開著：{u.aedt_status()['running']}"
    step("授權回報", usage)

    def lmutil():
        import usage as u
        return u.find_lmutil() or "這台沒有 lmutil.exe（只是不能幫忙查授權伺服器）"
    step("授權伺服器查詢", lmutil)

    def upd():
        import updater
        s = updater.install_script("C:/a b/new.exe", "C:/a'b/HFSSTool.exe", [1, 2], updater.current_version())
        if "C:/a''b/HFSSTool.exe" not in s:
            raise RuntimeError("換檔腳本的路徑沒有跳脫")
        s2 = updater.setup_script("C:/x/HFSSTool_v9_windows-setup.exe", "C:/a'b/HFSSTool.exe", [1], "1")
        if "/SILENT" not in s2 or "C:/a''b/HFSSTool.exe" not in s2:
            raise RuntimeError("安裝程式更新腳本不對")
        where = updater.installed_dir() or "（沒有安裝，單一 exe / 原始碼）"
        return f"{updater.current_version()} 可以自己換新：{updater.can_self_update()}；安裝在：{where}"
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
