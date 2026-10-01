"""
tests/test_gui_smoke.py — Phase 5A

Smoke tests for the PySide6 GUI, run via Qt's "offscreen" platform
plugin so they work in a headless CI/dev environment with no display.
Verifies both real sample files load through the GUI end-to-end and
that the GUI never touches h5py directly (only ChannelManager /
CachedExperiment / Experiment from app.core).

These are intentionally light "does it wire up and not crash" checks,
not exhaustive UI tests — full interaction testing is better done
manually / in later phases once the GUI has more surface area.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.real_data import BIG_FILE, SMALL_FILE

try:
    from PySide6.QtWidgets import QApplication
    PYSIDE_AVAILABLE = True
except ImportError:
    PYSIDE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PYSIDE_AVAILABLE, reason="PySide6 not installed in this environment."
)

samples_pytestmark = pytest.mark.skipif(
    not (SMALL_FILE.exists() and BIG_FILE.exists()),
    reason="Real Labber sample files not present in this environment.",
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_imports_do_not_touch_h5py():
    """Static check: app/gui modules must never import h5py directly -
    only app.core modules are allowed to."""
    import ast

    gui_dir = Path(__file__).resolve().parent.parent / "app" / "gui"
    for py_file in gui_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "h5py", f"{py_file} imports h5py directly!"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "h5py", f"{py_file} imports from h5py directly!"


def test_main_window_launches_empty(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    assert win.experiment is None
    assert win.y_combo.count() == 0
    win.close()


@samples_pytestmark
def test_main_window_opens_small_file(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(SMALL_FILE))

    assert win.experiment is not None
    assert win.experiment.format_variant == "vector_only"
    # v0.9B: y_combo (Y Axis) now offers every dynamically-discovered
    # axis candidate for this file - for the single-point sample that's
    # "Frequency" (the trace's own axis) and "VNA - S21" (the complex
    # channel itself), not just the one log channel as in earlier
    # versions.
    y_items = [win.y_combo.itemText(i) for i in range(win.y_combo.count())]
    assert "VNA - S21" in y_items
    assert "Frequency" in y_items
    x_items = [win.x_combo.itemText(i) for i in range(win.x_combo.count())]
    assert "Frequency" in x_items
    assert win.transform_combo.isEnabled() is True  # Y defaults to the complex channel

    curve = win.plot_widget._curve
    assert curve is not None
    xdata, ydata = curve.getData()
    assert len(xdata) == 501
    assert np.isfinite(ydata).all()

    win.close()


@samples_pytestmark
def test_main_window_opens_big_file_and_navigates_sweep(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.show()
    win.open_file(str(BIG_FILE))

    assert win.experiment is not None
    assert win.experiment.format_variant == "trace_log_channel"
    assert win.entry_spin.maximum() == 854  # 855 entries, 0-indexed
    assert win.entry_spin.isVisible() is True

    # move to entry 500 and confirm the plot + label update accordingly
    win.entry_spin.setValue(500)
    assert "Average Current" in win.entry_label.text()

    curve = win.plot_widget._curve
    xdata, ydata = curve.getData()
    assert len(xdata) == 501
    assert np.isfinite(ydata).all()

    win.close()


@samples_pytestmark
def test_transform_switch_changes_plotted_data(qapp):
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(BIG_FILE))

    _, y_db = win.plot_widget._curve.getData()

    # v0.9B: transform_combo's userData is now a TransformSpec object
    # (default base transforms: Real/Imaginary/Magnitude/Phase), not a
    # raw VALID_TRANSFORMS string key - select "Phase" by item text.
    idx = win.transform_combo.findText("Phase")
    win.transform_combo.setCurrentIndex(idx)
    _, y_phase = win.plot_widget._curve.getData()

    assert not np.allclose(y_db, y_phase)
    assert y_phase.min() >= -180.001 and y_phase.max() <= 180.001

    win.close()


@samples_pytestmark
def test_switching_files_closes_previous_experiment(qapp):
    """Opening a second file must not leak the first Experiment's open
    HDF5Reader - covers the closeEvent/replace-on-open logic."""
    from app.gui.main_window import MainWindow

    win = MainWindow()
    win.open_file(str(SMALL_FILE))
    first_experiment = win.experiment
    assert first_experiment._reader.is_open

    win.open_file(str(BIG_FILE))
    assert first_experiment._reader.is_open is False  # closed by open_file()
    assert win.experiment is not first_experiment
    assert win.experiment._reader.is_open

    win.close()


def test_open_invalid_file_shows_error_not_crash(qapp, tmp_path, monkeypatch):
    from app.gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox

    fake = tmp_path / "not_hdf5.txt"
    fake.write_text("this is not an hdf5 file")

    # avoid a blocking modal dialog during the test
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    win.open_file(str(fake))  # must not raise
    assert win.experiment is None

    win.close()
