"""Acceptance coverage for the consolidated v0.9F/v0.9G batch."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from app.core.database_scanner import DatabaseScanResult, LogEntry
from app.core.quick_preview import build_quick_preview
from app.core.star_store import StarStore
from app.core.transform_store import TransformStore
from tests.real_data import BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE

_QAPP_KEEPALIVE = []


pytestmark = pytest.mark.skipif(
    not all(path.exists() for path in (BIG_FILE, FLUX_FILE, S31_FILE, SMALL_FILE)),
    reason="Required workspace real-data fixtures are unavailable.",
)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    _QAPP_KEEPALIVE.append(app)
    yield app


def _entry(path: str, folder: tuple[str, ...]) -> LogEntry:
    relative = "/".join((*folder, Path(path).name))
    return LogEntry(path, relative, Path(path).name, Path(path).stem, "ok", None, 10, 1.0)


def _candidate_index(combo, name: str, channel: str) -> int:
    for index in range(combo.count()):
        candidate = combo.itemData(index)
        if candidate and candidate.name == name and candidate.base_channel == channel:
            return index
    return -1


def test_folder_selection_controls_data_list(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow

    result = DatabaseScanResult(
        str(tmp_path), str(tmp_path),
        [_entry("root.hdf5", ()), _entry("one.hdf5", ("2026", "08", "Data_0828")),
         _entry("two.hdf5", ("2026", "08", "Data_0831"))],
    )
    window = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    window.scan_result = result
    window._populate_folder_tree(result)
    assert window.data_list.topLevelItemCount() == 1
    assert window.data_list.topLevelItem(0).text(1) == "root"

    window.folder_tree.setCurrentItem(window._folder_items[("2026", "08", "Data_0831")])
    qapp.processEvents()
    assert window.data_list.topLevelItemCount() == 1
    assert window.data_list.topLevelItem(0).text(1) == "two"
    window.close()


@pytest.mark.parametrize(
    "path,kind,channel",
    [(SMALL_FILE, "1d", "VNA - S21"), (BIG_FILE, "2d", "VNA - S21"),
     (FLUX_FILE, "2d", "VNA - S21"), (S31_FILE, "2d", "VNA - S31")],
)
def test_real_quick_preview_is_generic(path, kind, channel):
    preview = build_quick_preview(str(path))
    assert preview.kind == kind
    assert preview.channel_name == channel
    assert preview.data.transform == "magnitude_db"


def test_toolbar_star_has_clear_states_and_persists(qapp, tmp_path):
    from app.gui.browser_window import BrowserWindow

    path = tmp_path / "stars.json"
    result = DatabaseScanResult(str(tmp_path), str(tmp_path), [_entry("run.hdf5", ())])
    window = BrowserWindow(star_store=StarStore(path))
    window.scan_result = result
    window._populate_folder_tree(result)
    window.data_list.setCurrentItem(window.data_list.topLevelItem(0))
    from app.gui.browser_window import STAR_STATE_ROLE

    assert window.data_list.currentItem().data(0, STAR_STATE_ROLE) is False
    assert "Star" in window.star_button.text()
    window.star_button.click()
    assert window.data_list.currentItem().data(0, STAR_STATE_ROLE) is True
    assert "Unstar" in window.star_button.text()
    assert StarStore(path).is_starred(str(tmp_path), "run.hdf5")
    window.close()


def test_data_selection_loads_background_quick_preview(qapp, tmp_path):
    from PySide6.QtCore import QEventLoop, QTimer
    from app.gui.browser_window import BrowserWindow

    entry = _entry(str(SMALL_FILE), ())
    result = DatabaseScanResult(str(tmp_path), str(tmp_path), [entry])
    window = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    window.scan_result = result
    window._populate_folder_tree(result)
    window.data_list.setCurrentItem(window.data_list.topLevelItem(0))

    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if window._preview_data is not None else None)
    poll.start(20)
    QTimer.singleShot(15000, loop.quit)
    loop.exec()
    poll.stop()
    assert window._preview_data is not None
    assert window._preview_data.kind == "1d"
    assert window.preview_stack.currentWidget() is window.preview_1d
    window.close()


def test_axis_preset_store_restart_roundtrip(tmp_path):
    path = tmp_path / "axis_presets.json"
    preset = AxisPreset(
        "IQ", AxisRef("Imaginary", "derived", "VNA - S21", "imag"),
        AxisRef("Real", "derived", "VNA - S21", "real"),
    )
    AxisPresetStore(path).save(preset)
    loaded = AxisPresetStore(path).get("IQ")
    assert loaded == preset


def test_existing_transform_json_remains_backward_compatible(tmp_path):
    path = tmp_path / "transforms.json"
    path.write_text(json.dumps([{"name": "Old Phase", "base": "phase_deg", "unwrap": True}]))
    spec = TransformStore(path).get("Old Phase")
    assert spec is not None and spec.unwrap and spec.base == "phase_deg"


def test_iq_save_restart_and_restore(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from app.gui.main_window import MainWindow

    preset_path = tmp_path / "axis_presets.json"
    transform_path = tmp_path / "transforms.json"
    first = MainWindow(
        transform_store=TransformStore(transform_path),
        axis_preset_store=AxisPresetStore(preset_path),
    )
    first.open_file(str(SMALL_FILE))
    first.x_combo.setCurrentIndex(_candidate_index(first.x_combo, "Imaginary", "VNA - S21"))
    first.y_combo.setCurrentIndex(_candidate_index(first.y_combo, "Real", "VNA - S21"))
    assert first.save_transform_button.isEnabled()
    assert first.save_transform_button.text() == "Save Axis Preset..."
    assert not first.transform_combo.isEnabled()
    assert not first.db_checkbox.isEnabled()
    assert not first.unwrap_checkbox.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("IQ", True)))
    first.save_transform_button.click()
    first.close()

    second = MainWindow(
        transform_store=TransformStore(transform_path),
        axis_preset_store=AxisPresetStore(preset_path),
    )
    second.open_file(str(SMALL_FILE))
    index = second.axis_preset_combo.findText("IQ")
    assert index >= 0
    second.axis_preset_combo.setCurrentIndex(index)
    assert second.x_combo.currentText() == "Imaginary"
    assert second.y_combo.currentText() == "Real"
    assert second.x_combo.currentData().base_channel == "VNA - S21"
    assert second.y_combo.currentData().base_channel == "VNA - S21"
    second.close()


def test_magnitude_phase_preset_modifier_semantics(qapp, tmp_path):
    from app.gui.main_window import MainWindow

    window = MainWindow(axis_preset_store=AxisPresetStore(tmp_path / "axis.json"))
    window.open_file(str(SMALL_FILE))
    window.x_combo.setCurrentIndex(_candidate_index(window.x_combo, "Magnitude", "VNA - S21"))
    window.y_combo.setCurrentIndex(_candidate_index(window.y_combo, "Phase", "VNA - S21"))
    assert window.save_transform_button.isEnabled()
    assert window.db_checkbox.isEnabled()
    assert window.unwrap_checkbox.isEnabled()
    window.db_checkbox.setChecked(True)
    window.unwrap_checkbox.setChecked(True)
    x_values, y_values = window.plot_widget._curve.getData()
    assert len(x_values) == len(y_values) > 0
    window.close()
