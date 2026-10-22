"""v3.5：「建立 / 更新 YIG 球」跟「建立 3D 模型」一起開關（3.4 跑完任務後一直是灰的）。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["HFSS_NO_UPDATE_CHECK"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_yig_button_follows_build_button():
    from ui_main import MainWindow
    w = MainWindow()
    try:
        assert not w.btn_yig_build.isEnabled()                 # 還沒開專案
        w.task_start_time = 0
        w.on_task_finished(True, 'OPEN', {'has_model': True})
        assert w.btn_yig_build.isEnabled()
        w.set_buttons_state(False)                             # 跑任務中
        assert not w.btn_yig_build.isEnabled()
        w.on_task_finished(True, 'BUILD', {'has_model': True})
        assert w.btn_yig_build.isEnabled()                     # 3.4 這裡一直是灰的
        w.on_task_finished(True, 'CLOSE', {})
        assert not w.btn_yig_build.isEnabled()
    finally:
        w.usage.close() if hasattr(w, "usage") else None
        w.close()
