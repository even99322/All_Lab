"""Virtual acceptance: scripted Host (loopback only, sandbox HOME)."""
import json
import os
import sys
import time

ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)
os.chdir(ROOT)
OUT = os.environ["NET_OUT"]


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LabLogViewer")
    from app.core.data_location import data_root
    data_root()
    from app.localization import initialize_localization
    from app.theme import initialize_theme
    loc = initialize_localization(app)
    initialize_theme(app, loc.store)
    loc.store.set_network_user_name("實驗室主機 A")
    from app.core.warmup import warm_now

    warm_now()              # as in the real program: Matplotlib is ready before a session starts
    from app.network.workspace import workspace
    from app.gui.main_window import MainWindow
    from tests.real_data import BIG_FILE

    space = workspace()
    viewer = MainWindow()
    viewer.resize(1200, 800)
    viewer.show()
    viewer.open_file(os.environ.get("NET_FILE", str(BIG_FILE)))
    space.start_hosting("週二組會")
    with open(os.path.join(OUT, "host.json"), "w") as stream:
        json.dump({"port": space.host.port, "code": space.host.join_code}, stream)
    log = []

    def step(seconds, action, label):
        QTimer.singleShot(int(seconds * 1000), lambda: (action(), log.append((time.time(), label))))

    def to_2d():
        viewer.mode_combo.setCurrentIndex(1)

    def zoom():
        view = viewer.plot_2d_widget.plot_widget.getViewBox() if hasattr(viewer.plot_2d_widget, "plot_widget") else None
        if view is not None:
            (x0, x1), (y0, y1) = view.viewRange()
            view.setRange(xRange=(x0 + (x1 - x0) * 0.25, x1 - (x1 - x0) * 0.25), padding=0)

    def to_1d():
        viewer.mode_combo.setCurrentIndex(0)
        viewer.log_entries.set_current_row(40) if hasattr(viewer.log_entries, "set_current_row") else None

    def yig():
        viewer._open_yig_fitting_window()

    def marks():
        manager = viewer._mark_managers[0]
        x = float(manager._x[len(manager._x) // 3])
        manager.add_nearest(x, 0.0)
        manager.add_vertical_line(float(manager._x[2 * len(manager._x) // 3]))
        viewer._persist_mark_manager(manager)
        viewer._refresh_mark_ui()
        with open(os.path.join(OUT, "host_marks.json"), "w") as stream:
            json.dump({"marks": [m.x for m in manager.marks()], "annotations": len(manager.annotations())}, stream)

    def clear_marks():
        viewer._mark_managers[0].clear()
        viewer._persist_mark_manager(viewer._mark_managers[0])
        viewer._refresh_mark_ui()

    def draw():
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QColor
        viewer.activateWindow()
        viewer.annotation.set_active(True)
        canvas = viewer.annotation.canvases[0]
        canvas.begin_stroke(QPointF(100, 100), QColor("#E53935"), 4)
        for i in range(30):
            canvas.extend_stroke(QPointF(100 + 10 * i, 100 + 4 * i))
        canvas.end_stroke()

    def status():
        with open(os.path.join(OUT, "host_status.json"), "w") as stream:
            json.dump({"clients": space.host_clients, "log": log,
                       "sharing": getattr(space.publisher.viewer, "windowTitle", lambda: "")()}, stream,
                      ensure_ascii=False, default=str)
        viewer.grab().save(os.path.join(OUT, "host_viewer.png"))

    step(6, to_2d, "2d")
    step(10, zoom, "zoom")
    step(14, to_1d, "1d")
    step(15, marks, "marks")
    step(16, draw, "annotation")
    step(22, clear_marks, "clear marks")
    step(19, yig, "yig")
    step(27, status, "status")
    step(float(os.environ.get("NET_HOST_SECONDS", "34")), lambda: (space.stop_hosting(), app.quit()), "stop")
    app.exec()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
    os._exit(0)
