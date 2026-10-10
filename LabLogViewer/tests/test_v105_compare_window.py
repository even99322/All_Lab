import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(__file__).resolve().parents[2] / "Data" / "tset"
FILES = [DATA / "0828 X1 Flux-dep.hdf5", DATA / "0828 4~6GHz BG.hdf5"]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(not all(f.exists() for f in FILES), reason="project sample data missing")
def test_two_files_overlay_difference_and_stacked(qapp):
    from app.gui.compare_window import CompareWindow
    w = CompareWindow()
    assert w.open_path(str(FILES[0])) and w.open_path(str(FILES[1]))
    assert len(w.files) == 2
    w.rows[1].file_combo.setCurrentIndex(1)
    for view in ("overlay", "stacked", "difference", "ratio"):
        w.view_combo.setCurrentIndex(w.view_combo.findData(view))
        w._redraw()
    for xm in ("native", "overlap", "index", "fixed_step"):
        w.x_mode_combo.setCurrentIndex(w.x_mode_combo.findData(xm))
        w._redraw()
    w.close()


@pytest.mark.skipif(not FILES[0].exists(), reason="project sample data missing")
def test_one_file_many_quantities_with_two_y_axes(qapp):
    from app.gui.compare_window import CompareWindow
    w = CompareWindow()
    w.open_path(str(FILES[0]))
    while len(w.rows) < 3:
        w.add_row()
    names = [w.rows[0].channel_combo.itemText(i) for i in range(w.rows[0].channel_combo.count())]
    vec = [n for n in names if w.rows[0].experiment.channels[n].is_vector]
    if not vec:
        pytest.skip("no vector channel")
    for i, r in enumerate(w.rows):
        r.channel_combo.setCurrentText(vec[0])
    for r, q in zip(w.rows, ("magnitude", "phase_deg", "real")):
        r.transform_combo.setCurrentText(q)
    w._redraw()
    assert w.graph.ci.items  # a plot was created
    w.y_mode_combo.setCurrentIndex(w.y_mode_combo.findData("minmax"))
    w._redraw()
    w.close()


def test_main_window_has_compare_entry(qapp):
    from app.gui.main_window import MainWindow
    m = MainWindow()
    assert m.compare_action.isEnabled()
    m._open_compare_window()
    assert m._compare_window is not None
    m._compare_window.close()
    m.close()
    m.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_compare_plot_follows_scientific_plot_appearance(qapp):
    from PySide6.QtGui import QColor
    from app.gui.compare_window import CompareWindow
    from app.theme import DARK_PLOT, WHITE_PLOT
    w = CompareWindow()
    w.show()
    qapp.processEvents()
    for colors in (DARK_PLOT, WHITE_PLOT):
        w.graph.apply_scientific_plot_appearance(colors)
        assert w.graph.backgroundBrush().color() == QColor(colors.plot_background)
    w.close()


def test_glass_recaptures_after_theme_change(qapp):
    from PySide6.QtWidgets import QMainWindow
    from app.gui.glass import GlassToolBar
    from app.theme import current_theme_colors
    win = QMainWindow()
    bar = GlassToolBar("t", win)
    win.addToolBar(bar)
    win.show()
    qapp.processEvents()
    bar.apply_lablog_theme(current_theme_colors())
    assert bar._glass_followup.isActive()
    win.close()


def test_legend_text_is_visible_on_dark_plot(qapp):
    import numpy as np
    from app.gui.compare_window import _ComparePlots
    from app.theme import DARK_PLOT
    graph = _ComparePlots()
    plot = graph.addPlot()
    legend = plot.addLegend()
    plot.plot(np.arange(3), np.arange(3), name="abc")
    graph.apply_scientific_plot_appearance(DARK_PLOT)
    html = legend.items[0][1].item.toHtml().lower()
    assert "color:" + str(DARK_PLOT.text).lower() in html
