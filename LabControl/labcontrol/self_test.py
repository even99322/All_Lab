"""打包版的自我檢查：``LabControl.exe --self-test``（打包腳本做完會自動跑一次）。

檢查打包是否完整：套件、預設檔、驅動註冊、模擬量測存成 .nc 再讀回、主視窗與儀器伺服器能開。
不連任何儀器、不碰使用者的 LAB 資料夾（用暫存資料夾）。結果印出來，也寫到環境變數
LABCONTROL_SELF_TEST_REPORT 指定的檔案（Windows 視窗程式沒有主控台）。回傳 0 = 全部通過。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, List, Tuple


def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="labcontrol_selftest_"))
    os.environ["LAB_CONTROL_HOME"] = str(tmp / "LAB")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    results: List[Tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> None:
        t0 = time.perf_counter()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.perf_counter() - t0:.1f} s)"))
        except Exception:  # noqa: BLE001
            results.append((name, False, traceback.format_exc(limit=6)))

    def libraries() -> str:
        import h5netcdf  # noqa: F401
        import h5py
        import numpy
        import pyqtgraph  # noqa: F401
        import pyvisa  # noqa: F401
        import ruamel.yaml  # noqa: F401
        import scipy  # noqa: F401
        import xarray
        import yaml  # noqa: F401
        from PyQt6 import QtCore

        return f"numpy {numpy.__version__}, h5py {h5py.__version__}, xarray {xarray.__version__}, Qt {QtCore.QT_VERSION_STR}"

    def lab_folder() -> str:
        from .paths import ensure_lab_home, lab_home

        created = ensure_lab_home()
        need = ["settings.yaml", "instruments.yaml"]
        missing = [n for n in need if not (lab_home() / n).exists()]
        if missing:
            raise RuntimeError(f"預設檔沒有複製出來：{missing}")
        return f"{len(created)} 個預設檔"

    def drivers() -> str:
        from .core.registry import DRIVERS, WRITERS, ensure_builtins_loaded

        ensure_builtins_loaded()
        names = DRIVERS.names()
        for need in ("yokogawa.gs200", "yokogawa.gs820", "rs.zna", "virtual.interleaved_pair", "sim.vna"):
            if need not in names:
                raise RuntimeError(f"缺少驅動 {need}")
        wr = WRITERS.names()
        if "netcdf" not in wr:
            raise RuntimeError("缺少 NetCDF 存檔")
        return f"{len(names)} 個驅動"

    def measure() -> str:
        from .core.station import Station
        from .scheme import Block, Scheme
        from .scheme.compile import compile_scheme
        from .scheme.catalog import build_catalog

        st = Station.from_lab(simulate=True)
        st.sim_time_scale = 0.0
        mag = next(n for n, i in st.instruments.items() if i.options.get("sources"))
        vna = next(n for n, i in st.instruments.items() if getattr(i, "MEASURE_KIND", "") == "vna")
        sch = Scheme("self-test", [Block("set", target=mag, mode="sweep", start=150.0, stop=150.002, step=0.001,
                                         unit="mA", children=[Block("measure", instrument=vna, traces=["S21"],
                                                                    settings={"points": 51})])],
                     output={"formats": ["netcdf"], "file_name": "selftest.nc", "root": str(tmp / "data")})
        res = compile_scheme(sch, build_catalog(st))
        if not res.ok:
            raise RuntimeError("; ".join(i.message for i in res.issues if i.level == "error"))
        exp = res.experiment(st)
        out = exp.plan_output(root=str(tmp / "data"))
        runner = exp.create_runner(out)
        runner.start()
        if not runner.wait(180):
            runner.stop()
            raise RuntimeError("模擬量測超過 3 分鐘沒有結束")
        if runner.dataset is None or not len(runner.dataset):
            raise RuntimeError("模擬量測沒有資料")
        exp.export(runner.dataset, out)
        files = list((tmp / "data").rglob("*.nc"))
        if not files:
            raise RuntimeError("量測完沒有 .nc 檔")
        import xarray as xr

        with xr.open_dataset(files[0], engine="h5netcdf") as ds:
            n = int(ds.sizes.get("Frequency", 0) or max(ds.sizes.values()))
        st.close()
        return f"{files[0].name}：{n} 點"

    def windows() -> str:
        from PyQt6 import QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
        from .apps.qt import theme
        from .apps.qt.server import InstrumentServerWindow
        from .apps.qt.workbench import LabControlWindow
        from .core.station import Station

        theme.apply(app, "light")
        st = Station.from_lab(simulate=True)
        w = LabControlWindow(st, None)
        w.show()
        app.processEvents()
        s = InstrumentServerWindow(st)
        s.show()
        app.processEvents()
        w.popout_live()
        app.processEvents()
        s.close()
        w.doc.dirty = False
        w.close()
        app.processEvents()
        st.close()
        return "主視窗、儀器伺服器、即時數據"

    for name, fn in (("套件", libraries), ("LAB 設定資料夾", lab_folder), ("驅動", drivers),
                     ("模擬量測 → .nc", measure), ("視窗", windows)):
        check(name, fn)

    from . import APP_NAME, __version__

    lines = [f"{APP_NAME} {__version__} 自我檢查（{sys.platform}，{'打包版' if getattr(sys, 'frozen', False) else 'Python'}）"]
    for name, ok, detail in results:
        lines.append(f"{'✔' if ok else '✖'} {name}：{detail}")
    ok = all(r[1] for r in results)
    if os.environ.get("LABCONTROL_CODE_ROOT"):
        lines.insert(1, f"（功能更新包 {os.environ.get('LABCONTROL_CODE_VERSION')}，程式本體 "
                        f"{os.environ.get('LABCONTROL_BASE_VERSION')}）")
    lines.append("全部通過" if ok else "有項目失敗")
    if ok:
        from .selfupdate import mark_good

        mark_good()                                       # 功能更新包通過自我檢查 = 可以用
    text = "\n".join(lines)
    report = os.environ.get("LABCONTROL_SELF_TEST_REPORT")
    if report:
        Path(report).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        try:
            print(text, flush=True)
        except Exception:  # noqa: BLE001
            pass
    return 0 if ok else 1
