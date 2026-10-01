import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 測試用獨立的 LAB 資料夾：不讀寫使用者真正的 C:\Users\even9\LAB
import tempfile  # noqa: E402

import atexit  # noqa: E402
import shutil  # noqa: E402

_LAB = tempfile.mkdtemp(prefix="labcontrol_test_lab_")
atexit.register(shutil.rmtree, _LAB, ignore_errors=True)
os.environ["LAB_CONTROL_HOME"] = _LAB
with open(os.path.join(_LAB, "settings.yaml"), "w", encoding="utf-8") as _f:
    _f.write("labber: {python38: ''}\ndata: {root: '" + os.path.join(_LAB, "data").replace("\\", "/") + "'}\n")

from labcontrol import Station  # noqa: E402

SIM_LAB = {
    "simulate": True,
    "instruments": {
        "DC3": {"driver": "yokogawa.gs200", "address": "X", "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-3,
                "max_jump": 5e-5, "resolution": 1e-6}, "sim": {"initial_level": 0.1586}},
        "DC4": {"driver": "yokogawa.gs200", "address": "X", "source": {"limits": [-0.2, 0.2], "ramp_rate": 5e-3,
                "max_jump": 5e-5, "resolution": 1e-6}, "sim": {"initial_level": 0.1586}},
        "DC5": {"driver": "yokogawa.gs820", "address": "X"},
        "VNA1": {"driver": "rs.zna", "address": "X", "labber_name": "VNA",
                 "sim": {"coupled_to": ["pair"], "sweep_time": 0, "split_region": [0.15870, 0.15875],
                         "I0": 0.1587, "f0": 5.0248e9}},
        "pair": {"driver": "virtual.interleaved_pair", "sources": ["DC3", "DC4"],
                 "source": {"ramp_rate": 5e-3, "max_jump": 5e-5}},
    },
}


def experiment_cfg(start=158.60, stop=158.62, step=0.0005, **run):
    return {
        "name": "test",
        "procedure": {"type": "trace_sweep", "readouts": [{"ref": "VNA1", "trace": "S21", "export_name": "VNA - S21"}]},
        "setup": {"VNA1": {"start_freq": "5.0198 GHz", "stop_freq": "5.0298 GHz", "points": 101, "power": -10,
                           "if_bw": "10 kHz", "averages": 1}},
        "sweep": [{"target": "pair.level", "name": "Average Current", "unit": "mA",
                   "start": start, "stop": stop, "step": step, "settle": 0}],
        "run": {"approach_rate": "5 mA/s", "park": "start", "park_rate": "5 mA/s", **run},
        "hooks": [{"type": "dip_shape_pause", "channel": "S21", "enabled": False}],
        "output": {"raw": True, "file_name": "0929 test.hdf5", "export": [{"type": "hdf5"}]},
    }


@pytest.fixture
def station():
    st = Station(SIM_LAB)
    yield st
    from labcontrol.measure import Runner

    Runner.stop_all(wait=10)   # 測試失敗時也不會留下卡住的量測執行緒
    st.close()


_APP = []


@pytest.fixture(autouse=True, scope="session")
def _qapp():
    """整個測試期間只有一個 QApplication，並一直保留參考。

    各測試用 `QApplication.instance() or QApplication([])` 時，如果 QApplication 只存在區域變數裡，
    測試結束就被回收（銷毀 QApplication），之後的 Qt 物件會碰到已釋放的記憶體而偶發 segfault
    （0.0.12 查出的根本原因；常駐的 Qt 物件也會被一起刪掉）。"""
    if os.environ.get("QT_QPA_PLATFORM") or os.name == "nt" or os.environ.get("DISPLAY"):
        try:
            from PyQt6 import QtWidgets

            _APP.append(QtWidgets.QApplication.instance() or QtWidgets.QApplication([]))
        except Exception:  # noqa: BLE001
            pass
    yield


@pytest.fixture(autouse=True)
def _qt_cleanup():
    """每個測試後刪掉所有 Qt 視窗並立即回收，讓每個測試的 Qt 狀態互不影響。"""
    yield
    import gc

    if "PyQt6.QtWidgets" not in sys.modules:
        gc.collect()
        return

    from PyQt6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        # pyqtgraph 的右鍵選單等（QMenu / 內部 QWidget）歸 Python 物件擁有：刪掉會重複釋放而當機
        if isinstance(w, (QtWidgets.QMainWindow, QtWidgets.QDialog)) or \
                type(w).__module__.startswith(("labcontrol", "labmonitor", "tests")):
            w.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete.value)
    app.processEvents()
    gc.collect()


#: 測試中跳出的提示框（不會真的跳出、擋住測試）：[(種類, 標題, 內容)]
DIALOGS = []


@pytest.fixture(autouse=True)
def _no_modal_boxes(monkeypatch):
    """QMessageBox.warning / critical / information 在測試中不跳出（避免擋住、看起來像卡住），改成記錄下來。"""
    if "PyQt6.QtWidgets" not in sys.modules:
        yield
        return
    from PyQt6 import QtWidgets

    DIALOGS.clear()
    for kind in ("warning", "critical", "information"):
        def fake(parent, title, text, *a, _k=kind, **k):
            DIALOGS.append((_k, title, text))
            return QtWidgets.QMessageBox.StandardButton.Ok
        monkeypatch.setattr(QtWidgets.QMessageBox, kind, staticmethod(fake))
    yield
