from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.data_identity import stable_data_identity
from app.core.formula import apply_formulas
from app.core.mark_store import MarkStore
from app.core.named_view_store import NamedViewStore
from app.core.quick_preview import build_quick_preview
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from tests.real_data import FLUX_FILE, SMALL_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_view_presets_are_data_bound_and_survive_store_reload(tmp_path):
    path_a = tmp_path / "database-a" / "same-name.hdf5"
    path_b = tmp_path / "database-b" / "same-name.hdf5"
    identity_a = stable_data_identity(path_a)
    identity_b = stable_data_identity(path_b)
    assert identity_a != identity_b

    path = tmp_path / "named_views.json"
    store = NamedViewStore(path)
    store.save(identity_a, "Magnitude view", {"data_identity": identity_a, "display": {"mode": "1d"}})
    store.save(identity_b, "Phase view", {"data_identity": identity_b, "display": {"mode": "2d"}})
    store.save(identity_a, "Magnitude view", {"data_identity": identity_a, "formula": "y^2"}, overwrite=True)

    restarted = NamedViewStore(path)
    assert restarted.list_names(identity_a) == ["Magnitude view"]
    assert restarted.list_names(identity_b) == ["Phase view"]
    assert restarted.get(identity_a, "Magnitude view")["formula"] == "y^2"
    assert restarted.get(identity_b, "Magnitude view") is None
    assert restarted.rename(identity_a, "Magnitude view", "Squared")
    assert restarted.delete(identity_b, "Phase view")


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real Labber sample file is unavailable.")
def test_viewer_formula_preset_round_trip_restores_rendered_data(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    store = NamedViewStore(tmp_path / "views.json")
    window = MainWindow(
        named_view_store=store,
        mark_store=MarkStore(tmp_path / "marks.json"),
        viewer_display_state_store=ViewerDisplayStateStore(tmp_path / "display.json"),
    )
    window.open_file(str(SMALL_FILE))
    assert window.mode_combo.currentIndex() == 0

    complex_index = next((index for index in range(window.y_combo.count())
                          if (candidate := window.y_combo.itemData(index)) is not None
                          and candidate.domain == "points" and candidate.is_complex), -1)
    assert complex_index >= 0
    window.y_combo.setCurrentIndex(complex_index)

    index = window.transform_combo.findText("Magnitude")
    if index >= 0:
        window.transform_combo.setCurrentIndex(index)
    window.x_formula_edit.setText("")
    window.y_formula_edit.setText("y^2")
    window._refresh_formula_previews()
    assert "²" in window.y_formula_preview.text()

    x_candidate, y_candidate = window.x_combo.currentData(), window.y_combo.currentData()
    base_x, base_y, _, _ = window._plot_arrays_for_entry(
        x_candidate, y_candidate, window.log_entries.current_row(),
        window._current_transform_spec(), apply_formula=False,
    )
    expected_x, expected_y = apply_formulas(base_x, base_y, "", "y^2")
    assert window._apply_formula_inputs()
    actual_x, actual_y = window.plot_widget._curve.getData()
    assert np.allclose(actual_x, expected_x, equal_nan=True)
    assert np.allclose(actual_y, expected_y, equal_nan=True)

    preview = build_quick_preview(str(SMALL_FILE), window._current_viewer_display_state())
    assert preview.kind == "1d" and preview.restored
    assert np.allclose(preview.data.x_values, expected_x, equal_nan=True)
    assert np.allclose(preview.data.y_values, expected_y, equal_nan=True)

    window.mode_combo.setCurrentIndex(1)
    assert not window.formula_controls.isEnabled()
    window.mode_combo.setCurrentIndex(0)
    assert window.formula_controls.isEnabled()
    assert window.y_formula_edit.text() == "y^2"

    pane_state = window._pane_states[window._active_pane_id]
    pane_state.formula_enabled = True
    pane_state.y_formula = "y^2"
    pane_x, pane_y, _ = window._pane_plot_arrays(
        x_candidate, y_candidate, window.log_entries.current_row(), pane_state,
    )
    assert np.allclose(pane_x, expected_x, equal_nan=True)
    assert np.allclose(pane_y, expected_y, equal_nan=True)

    manager = window._current_mark_manager()
    assert manager.add_at_trace_position(42) is not None
    window._refresh_mark_ui()
    window._persist_mark_manager(manager)
    mark_snapshot = manager.persistence_state()

    identity = window.experiment.data_identity
    payload = window._named_view_payload()
    assert payload["data_identity"] == identity
    assert payload["pane"]["states"][str(window._active_pane_id)]["y_formula"] == "y^2"
    store.save(identity, "S21 magnitude squared", payload)

    window._reset_formula_inputs()
    assert not window._pane_states[window._active_pane_id].formula_enabled
    assert window._load_named_view("S21 magnitude squared")
    restored = window._pane_states[window._active_pane_id]
    assert restored.formula_enabled and restored.y_formula == "y^2"
    assert window._current_mark_manager().persistence_state() == mark_snapshot
    actual_x, actual_y = window.plot_widget._curve.getData()
    assert np.allclose(actual_x, expected_x, equal_nan=True)
    assert np.allclose(actual_y, expected_y, equal_nan=True)
    window.close()


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real Labber sample file is unavailable.")
def test_sidebar_dropdown_formula_and_splitter_interactions(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox, QInputDialog
    from app.gui.main_window import MainWindow

    window = MainWindow(named_view_store=NamedViewStore(tmp_path / "views.json"))
    window.resize(1180, 760)
    window.open_file(str(SMALL_FILE))
    window.show()
    qapp.processEvents()

    def choose_second(combo):
        assert combo.isEnabled() and combo.count() > 1
        before = combo.currentIndex()
        combo.showPopup()
        qapp.processEvents()
        popup = combo.view()
        popup.setFocus()
        QTest.keyClick(popup, Qt.Key_Home)
        QTest.keyClick(popup, Qt.Key_Down)
        QTest.keyClick(popup, Qt.Key_Return)
        qapp.processEvents()
        combo.hidePopup()
        assert combo.currentIndex() != before

    for combo in (window.x_combo, window.y_combo):
        choose_second(combo)
    complex_index = next((index for index in range(window.y_combo.count())
                          if (candidate := window.y_combo.itemData(index)) is not None
                          and candidate.domain == "points" and candidate.is_complex), -1)
    if complex_index >= 0:
        window.y_combo.setCurrentIndex(complex_index)
    choose_second(window.transform_combo)

    manager = window._current_mark_manager()
    manager.add_at_trace_position(20)
    window._refresh_mark_ui()
    choose_second(window.mark_tool_combo)
    window.mark_tool_combo.setCurrentIndex(0)
    choose_second(window.analysis_region_combo)
    choose_second(window.analysis_operation_combo)
    if window.analysis_start_mark_combo.count() > 1:
        choose_second(window.analysis_start_mark_combo)

    long_expression = r"\sqrt{\frac{|y|^2}{x^2 + 1}} + \log_{10}(|y| + 1) + \sin(x) + \cos(y) + \exp(-x^2/100)"
    window.x_formula_edit.setFocus()
    QTest.keyClicks(window.x_formula_edit, "x+y")
    window.y_formula_edit.setFocus()
    QTest.keyClicks(window.y_formula_edit, long_expression)
    QTest.qWait(250)
    assert "√" in window.y_formula_preview.text()
    assert window.y_formula_preview.wordWrap()
    assert window.y_formula_preview.maximumHeight() > 50
    window.apply_formula_button.click()
    assert window._pane_states[window._active_pane_id].formula_enabled
    window.reset_formula_button.click()
    assert not window._pane_states[window._active_pane_id].formula_enabled

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("GUI preset", True)))
    window.save_view_preset_button.click()
    assert "GUI preset" in window.named_view_store.list_names(window.experiment.data_identity)
    window.view_preset_combo.setCurrentIndex(0)
    choose_second(window.view_preset_combo)
    assert window.view_preset_combo.currentData() == "GUI preset"
    window.update_view_preset_button.click()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.Yes))
    window.delete_view_preset_button.click()
    assert "GUI preset" not in window.named_view_store.list_names(window.experiment.data_identity)

    scroll = window.controls_scroll_area.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    qapp.processEvents()
    assert scroll.value() == scroll.maximum()
    handle = window.main_splitter.handle(1)
    start_sizes = window.main_splitter.sizes()
    center = handle.rect().center()
    QTest.mousePress(handle, Qt.LeftButton, pos=center)
    QTest.mouseMove(handle, center + QPoint(30, 0), delay=50)
    QTest.mouseRelease(handle, Qt.LeftButton, pos=center + QPoint(30, 0))
    qapp.processEvents()
    assert window.main_splitter.sizes() != start_sizes
    window.close()


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux Labber sample file is unavailable.")
def test_view_preset_restores_cut_window_state_for_its_data(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    store = NamedViewStore(tmp_path / "views.json")
    window = MainWindow(named_view_store=store)
    window.open_file(str(FLUX_FILE))
    window.mode_combo.setCurrentIndex(1)
    qapp.processEvents()
    window._show_cut_window("x")
    cut = window._cut_windows["x"]
    assert cut.isVisible()
    cut.follow_main_plot_checkbox.setChecked(False)
    x_position, y_position = cut._position
    cut.set_position(x_position + 0.0001, y_position)

    identity = window.experiment.data_identity
    store.save(identity, "Flux with X Cut", window._named_view_payload())
    cut.close()
    assert not cut.isVisible()
    assert window._load_named_view("Flux with X Cut")
    assert cut.isVisible()
    assert not cut.follow_main_plot_checkbox.isChecked()
    assert cut._position[0] == pytest.approx(x_position + 0.0001)
    window.close()
