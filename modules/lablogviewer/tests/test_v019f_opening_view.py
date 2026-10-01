"""v0.19F: data opened for the first time starts in 2D (when it has a sweep); data worked on
before opens the way it was left."""

from __future__ import annotations

import pytest

from tests.real_data import BIG_FILE, SMALL_FILE


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.skipif(not (SMALL_FILE.exists() and BIG_FILE.exists()), reason="Real measurement fixtures unavailable")
def test_first_open_is_2d_then_the_last_view_returns(qapp, tmp_path, monkeypatch):
    from app.core.viewer_display_state_store import ViewerDisplayStateStore
    from app.gui.main_window import MainWindow

    monkeypatch.setenv("LABLOGVIEWER_FIRST_OPEN_2D", "1")                # the real behaviour
    store = ViewerDisplayStateStore(tmp_path / "views.json")

    def viewer():
        window = MainWindow(viewer_display_state_store=store)
        window.show()
        return window

    first = viewer()
    first.open_file(str(BIG_FILE))
    qapp.processEvents()
    assert first.mode_combo.currentIndex() == 1                         # never opened before: 2D
    first.mode_combo.setCurrentIndex(0)                                 # the user goes to 1D ...
    qapp.processEvents()
    first.close()
    qapp.processEvents()
    again = viewer()
    again.open_file(str(BIG_FILE))
    qapp.processEvents()
    assert again.mode_combo.currentIndex() == 0                         # ... and finds it that way
    again.close()
    single = viewer()
    single.open_file(str(SMALL_FILE))
    qapp.processEvents()
    assert single.mode_combo.currentIndex() == 0                        # one trace: 1D, no warning
    single.close()
