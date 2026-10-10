"""v0.19A follow-ups: annotation overlay vs splitters, full YIG mirror, manual address."""

from __future__ import annotations

import collections
import os

import pytest

from tests.real_data import BIG_FILE


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_annotation_overlay_never_becomes_a_splitter_pane(qapp):
    """The 3D plot was squeezed to a third of its width when the pen was turned on."""
    from PySide6.QtWidgets import QMainWindow, QSplitter, QToolButton, QWidget
    from app.gui.annotation import AnnotationSession

    window = QMainWindow()
    splitter = QSplitter()
    controls, plot = QWidget(), QWidget()
    splitter.addWidget(controls)
    splitter.addWidget(plot)
    window.setCentralWidget(splitter)
    window.resize(1000, 600)
    window.show()
    splitter.setSizes([300, 700])
    qapp.processEvents()
    before = plot.width()
    session = AnnotationSession(window, QToolButton(), regions=lambda: [([plot], False)])
    session.set_active(True)
    qapp.processEvents()
    assert splitter.count() == 2 and abs(plot.width() - before) <= 2
    canvas = session.canvases[0]
    assert not isinstance(canvas.parentWidget(), QSplitter)
    assert canvas.geometry().size() == plot.size()
    session.set_active(False)
    window.close()


@pytest.mark.skipif(not BIG_FILE.exists(), reason="Real measurement fixture unavailable")
def test_yig_mirror_rebuilds_the_whole_window(qapp):
    from app.gui.main_window import MainWindow
    from app.network import protocol as P
    from app.network.yig_mirror import YigMirrorWindow
    from app.network.yig_snapshot import snapshot

    viewer = MainWindow()
    viewer.show()
    viewer.open_file(str(BIG_FILE))
    viewer._open_yig_fitting_window()
    qapp.processEvents()
    window = list(viewer._yig_fitting_windows.values())[0]
    window.resize(1500, 950)
    qapp.processEvents()
    try:
        layout, content = snapshot(window, None)
        kinds = collections.Counter(item["kind"] for item in layout["items"])
        # left settings panel + tabs + figures + parameter table
        assert kinds["combo"] >= 3 and kinds["spin"] >= 3 and kinds["label"] >= 10
        assert kinds["figure"] >= 1 and kinds["table"] >= 1 and kinds["tabbar"] >= 1
        combo_texts = {value.get("text") for key, value in content.items() if isinstance(value, dict) and "text" in value}
        assert window.w.cmb_s.currentText() in combo_texts if hasattr(window, "w") else True
        value = P.unpack(P.FrameReader().feed(P.pack({"type": "yig"}, {"layout": layout, "content": content}))[0])

        class Space:
            client = None

            @staticmethod
            def text(key):
                return "{host} {title}" if key == "net.yig_mirror_title" else key

            changed = progress = type("S", (), {"connect": staticmethod(lambda *a: None)})()
            client_quality = __import__("app.network.quality", fromlist=["Quality"]).Quality(None, None, "unknown")
            data_source = ""

            @staticmethod
            def disconnect():
                pass

        mirror = YigMirrorWindow(Space())
        mirror.apply(value, full=True)
        assert len(mirror.items) == len(layout["items"])
        assert mirror.board.size().toTuple() == tuple(layout["size"])
        mirror.close()
    finally:
        window.close()
        viewer.close()


def test_manual_address_with_and_without_port(qapp):
    from app.gui.network_panel import NetworkPanel
    from app.network.host import DEFAULT_PORT

    panel = NetworkPanel()
    panel.address_edit.setText("192.168.1.20")
    assert panel._target() == ("192.168.1.20", DEFAULT_PORT)
    panel.address_edit.setText("192.168.1.20:5000")
    assert panel._target() == ("192.168.1.20", 5000)
    panel.address_edit.setText("host:notaport")
    assert panel._target() is None
    panel.close()
