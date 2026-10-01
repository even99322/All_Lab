"""Preview restore and v0.13D Browser Comment regression coverage."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.comment_store import CommentStore
from app.core.quick_preview import build_quick_preview
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from tests.real_data import BIG_FILE, FLUX_FILE, SMALL_FILE


LEGACY_PARTIAL_FILE = (
    Path(__file__).resolve().parent.parent.parent / "Data" / "2025 0827 LR CPAEP"
    / "2025" / "08" / "Data_0826" / "0825 LR @4.812GHz.hdf5"
)

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _state_2d(channel: str, x: str, y: str, *, colormap="viridis", auto=False):
    return {
        "mode": "2d",
        "two_d": {
            "z": channel, "x": x, "y": y, "transform": "magnitude_db",
            "colormap": colormap, "auto_color": auto, "minimum": -42.0, "maximum": -12.0,
        },
    }


@pytest.mark.skipif(not SMALL_FILE.exists(), reason="Real 1D fixture unavailable")
def test_quick_preview_restores_valid_per_data_1d_state_and_falls_back():
    state = {"mode": "1d", "one_d": {
        "channel": "VNA - S21", "x_axis": "Frequency", "transform": "phase_deg",
        "trace_index": 0, "db": False, "unwrap": False,
    }}
    restored = build_quick_preview(str(SMALL_FILE), state)
    assert restored.kind == "1d" and restored.restored
    assert restored.data.transform == "phase_deg"

    invalid = {"mode": "1d", "one_d": {"channel": "Missing", "transform": "raw", "trace_index": 0}}
    fallback = build_quick_preview(str(SMALL_FILE), invalid)
    assert fallback.kind == "1d" and not fallback.restored


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real 2D fixture unavailable")
def test_quick_preview_restores_2d_transform_colormap_and_manual_range():
    state = _state_2d("VNA - S21", "Frequency", "Average Current")
    preview = build_quick_preview(str(BIG_FILE), state)
    assert preview.kind == "2d" and preview.restored
    assert preview.data.z_values.shape == (855, 501)
    assert preview.data.transform == "magnitude_db"
    assert (preview.colormap, preview.z_min, preview.z_max) == ("viridis", -42.0, -12.0)


@pytest.mark.skipif(not FLUX_FILE.exists(), reason="Real Flux fixture unavailable")
def test_quick_preview_restores_flux_orientation_without_transpose():
    state = _state_2d("VNA - S21", "Frequency", "DC supply - 1 - Current", colormap="plasma", auto=True)
    preview = build_quick_preview(str(FLUX_FILE), state)
    assert preview.kind == "2d" and preview.restored
    assert preview.data.z_values.shape == (681, 10001)
    assert preview.data.x_name == "Frequency"
    assert preview.data.y_name == "DC supply - 1 - Current"
    assert preview.z_min is None and preview.z_max is None


@pytest.mark.skipif(not LEGACY_PARTIAL_FILE.exists(), reason="Real partial fixture unavailable")
def test_quick_preview_restore_keeps_only_real_partial_measurements():
    state = _state_2d("VNA - S31", "Frequency", "DC supply - 1 - Current")
    preview = build_quick_preview(str(LEGACY_PARTIAL_FILE), state)
    assert preview.restored and preview.acquisition is not None and preview.acquisition.is_partial
    assert preview.data.z_values.shape == (270, 1001)


def test_viewer_display_state_is_stable_and_corrupt_entries_are_isolated(tmp_path):
    path = tmp_path / "viewer_display_states.json"
    source_a, source_b = tmp_path / "a.hdf5", tmp_path / "b.hdf5"
    store = ViewerDisplayStateStore(path)
    store.set(source_a, {"mode": "1d", "one_d": {"channel": "A"}})
    store.set(source_b, {"mode": "2d", "two_d": {"z": "B"}})
    restarted = ViewerDisplayStateStore(path)
    assert restarted.get(source_a)["mode"] == "1d"
    assert restarted.get(source_b)["mode"] == "2d"
    path.write_text('{"schema_version": 1, "by_data": {"bad": 3}}')
    assert ViewerDisplayStateStore(path).get(source_a) is None


@pytest.mark.skipif(not PYSIDE_AVAILABLE, reason="PySide6 unavailable")
def test_browser_comment_is_data_owned_compact_and_uses_native_editor(qapp, tmp_path):
    from app.core.database_scanner import DatabaseScanResult, LogEntry
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    database = str(tmp_path.resolve())
    store = CommentStore(tmp_path / "comments.json")
    source_a, source_b = tmp_path / "Data_A" / "same.hdf5", tmp_path / "Data_B" / "same.hdf5"
    store.set(database, "Data_A/same.hdf5", "v0.13B note")
    browser = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"), comment_store=store)
    browser.scan_result = DatabaseScanResult(database, database, [])
    first = LogEntry(str(source_a), "Data_A/same.hdf5", "same.hdf5", "same", "ok", None, 0, 0)
    second = LogEntry(str(source_b), "Data_B/same.hdf5", "same.hdf5", "same", "ok", None, 0, 0)
    browser._load_browser_comment(first)
    assert browser.comment_editor.toPlainText() == "v0.13B note"
    assert browser.comment_editor.isEnabled()
    browser.comment_editor.setPlainText("第一行\nΔS21 ★")
    browser.comment_editor.selectAll()
    browser.comment_editor.copy()
    assert qapp.clipboard().text() == "第一行\nΔS21 ★"
    browser.comment_editor.cut()
    assert browser.comment_editor.toPlainText() == ""
    browser.comment_editor.paste()
    assert browser.comment_editor.toPlainText() == "第一行\nΔS21 ★"
    browser.comment_editor.undo()
    assert browser.comment_editor.toPlainText() == ""
    browser.comment_editor.redo()
    assert browser.comment_editor.toPlainText() == "第一行\nΔS21 ★"
    actions = [action.text().split("\t", 1)[0].replace("&", "")
               for action in browser.comment_editor.createStandardContextMenu().actions()]
    assert {"Cut", "Copy", "Paste", "Select All"} <= set(actions)
    browser._flush_browser_comment()
    browser._load_browser_comment(second)
    assert browser.comment_editor.toPlainText() == ""
    assert browser._comment_ratio <= 0.15
    browser._toggle_comment_panel()
    assert not browser._comment_expanded
    browser.close()
    restarted = CommentStore(store.storage_path)
    assert restarted.get_for_source(source_a) == "第一行\nΔS21 ★"
    assert restarted.get(database, "Data_A/same.hdf5") == "第一行\nΔS21 ★"


@pytest.mark.skipif(not PYSIDE_AVAILABLE or not BIG_FILE.exists(), reason="Qt or real 2D fixture unavailable")
def test_cut_titles_identify_data_and_legacy_panels_are_not_constructed(qapp):
    from app.core.viewer_display_state_store import ViewerDisplayStateStore
    from app.gui.main_window import MainWindow
    from app.gui.linecut_widget import LineCutWidget

    window = MainWindow(viewer_display_state_store=ViewerDisplayStateStore(Path("/private/tmp/v013b-state.json")))
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    window._refresh_cut_windows()
    assert not window.findChildren(LineCutWidget)
    assert window._cut_windows["x"].windowTitle() == "X Cut — 0828 RSMEP_1"
    assert window._cut_windows["y"].windowTitle() == "Y Cut — 0828 RSMEP_1"
    window.close()


@pytest.mark.skipif(not PYSIDE_AVAILABLE or not BIG_FILE.exists(), reason="Qt or real 2D fixture unavailable")
def test_viewer_captures_display_state_per_data_without_marks_or_geometry(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    store = ViewerDisplayStateStore(tmp_path / "viewer_display_states.json")
    window = MainWindow(viewer_display_state_store=store)
    window.open_file(str(BIG_FILE))
    window.mode_combo.setCurrentIndex(1)
    window.colormap_combo.setCurrentText("inferno")
    window.auto_range_checkbox.setChecked(False)
    window.zmin_spin.setValue(-45.0)
    window.zmax_spin.setValue(-15.0)
    window._flush_display_state()
    state = store.get(BIG_FILE)
    assert state["mode"] == "2d"
    assert state["two_d"]["colormap"] == "inferno"
    assert state["two_d"]["auto_color"] is False
    assert (state["two_d"]["minimum"], state["two_d"]["maximum"]) == (-45.0, -15.0)
    assert "marks" not in state and "geometry" not in state and "panes" not in state
    window.close()
