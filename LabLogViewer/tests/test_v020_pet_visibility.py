"""v0.20 pet: stays visible when another program is in front, click brings LabLogViewer forward."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    from app.pet import store

    monkeypatch.setattr(store, "store_path", lambda: tmp_path / "pet.json")
    return store


def _window():
    from app.pet import registry
    from app.pet.character import Character
    from app.pet.pet_window import PetWindow

    info = next(p for p in registry.PETS if not p.hidden)
    return PetWindow(Character(info.pack, 1))


def test_pet_window_survives_switching_to_another_program(qapp):
    from PySide6.QtCore import Qt

    window = _window()
    assert window.testAttribute(Qt.WA_MacAlwaysShowToolWindow)
    assert window.windowFlags() & Qt.WindowStaysOnTopHint
    window.set_float_on_top(False)
    assert not window.windowFlags() & Qt.WindowStaysOnTopHint
    window.set_float_on_top(True)
    assert window.windowFlags() & Qt.WindowStaysOnTopHint
    window.close()


def test_float_setting_defaults_on_and_is_saved(isolated_store):
    assert isolated_store.load()["float_on_top"] is True
    isolated_store.set_float_on_top(False)
    assert isolated_store.load()["float_on_top"] is False
    isolated_store.set_enabled(False)                      # other saves must not reset it
    assert isolated_store.load()["float_on_top"] is False


def test_plain_click_asks_to_raise_the_app_but_drag_and_sleeping_click_do_not(qapp):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    window = _window()
    window.show()
    seen = []
    window.activate_requested.connect(lambda: seen.append(1))

    def click(move=0):
        pos = QPointF(10, 10)
        for kind, p in ((QEvent.MouseButtonPress, pos), (QEvent.MouseMove, pos + QPointF(move, 0)),
                        (QEvent.MouseButtonRelease, pos + QPointF(move, 0))):
            ev = QMouseEvent(kind, p, window.mapToGlobal(p.toPoint()) + QPoint(0, 0), Qt.LeftButton,
                             Qt.LeftButton if kind != QEvent.MouseButtonRelease else Qt.NoButton, Qt.NoModifier)
            getattr(window, {QEvent.MouseButtonPress: "mousePressEvent", QEvent.MouseMove: "mouseMoveEvent",
                             QEvent.MouseButtonRelease: "mouseReleaseEvent"}[kind])(ev)

    click()
    assert seen == [1]
    click(move=80)                                         # a drag only moves the pet
    assert seen == [1]
    window.set_awake(False)
    click()                                                # the first click only wakes it
    assert seen == [1] and window.awake
    window.close()


def test_manager_raises_viewer_then_browser(qapp):
    from app.pet.manager import PetManager

    calls = []

    class Win:
        def __init__(self, name, minimized=False):
            self.name, self.minimized = name, minimized

        def isMinimized(self):
            return self.minimized

        def showNormal(self):
            calls.append((self.name, "normal"))

        def show(self):
            calls.append((self.name, "show"))

        def raise_(self):
            calls.append((self.name, "raise"))

        def activateWindow(self):
            calls.append((self.name, "activate"))

    manager = PetManager()
    manager.tracker.viewer = lambda: Win("viewer", True)
    manager.tracker.browser = lambda: Win("browser")
    manager.raise_app()
    assert calls == [("viewer", "normal"), ("viewer", "show"), ("viewer", "raise"), ("viewer", "activate")]
    calls.clear()
    manager.tracker.viewer = lambda: None
    manager.raise_app()
    assert ("browser", "activate") in calls
