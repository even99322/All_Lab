"""獨立的量測監控視窗：直接執行一份實驗 YAML（不經過流程圖）。

    python -m labcontrol.apps.qt.monitor LAB/experiments/dc_vna_sweep.yaml --sim [--web 5000]

畫面與主視窗右上的「即時監控」相同（workbench/monitor_panel.py）。
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from PyQt6 import QtWidgets

from ...core.station import Station
from ...measure.experiment import Experiment
from .workbench.monitor_panel import MonitorPanel


class MonitorWindow(QtWidgets.QWidget):
    def __init__(self, station: Station, experiment: Experiment, root: Optional[str] = None) -> None:
        super().__init__()
        self.station, self.exp, self.root = station, experiment, root
        self.setWindowTitle(f"Lab Control — {experiment.name}")
        self.resize(1100, 800)
        self.panel = MonitorPanel(station)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.panel)
        self.panel.chk_manual.setChecked(experiment.options.manual)
        self.panel.start_requested.connect(self.start)

    @property
    def runner(self):
        return self.panel.runner

    def start(self) -> None:
        if not self.panel.busy:
            self.panel.run(self.exp, self.exp.plan_output(root=self.root))

    def closeEvent(self, ev) -> None:
        if self.panel.busy:
            r = QtWidgets.QMessageBox.question(self, "確認關閉", "量測進行中，確定要中斷並關閉？")
            if r != QtWidgets.QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.panel.runner.stop()
            self.panel.runner.wait()
        self.panel.close_bridge()
        ev.accept()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment")
    ap.add_argument("--lab", default=None, help="儀器清單（預設 LAB/instruments.yaml）")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--root", help="覆寫資料根目錄")
    ap.add_argument("--web", type=int, metavar="PORT", help="同時啟動網頁面板（共用同一個 Station）")
    a = ap.parse_args(argv)
    app = QtWidgets.QApplication(sys.argv[:1])
    st = Station.from_lab(a.lab, simulate=True if a.sim else None)
    if a.web:
        from ..web.server import serve_in_thread

        serve_in_thread(st, port=a.web)
    w = MonitorWindow(st, Experiment.from_file(a.experiment, st), root=a.root)
    w.show()
    rc = app.exec()
    st.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
