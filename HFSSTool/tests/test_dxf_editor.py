"""DXF 編輯器介面：畫微波線（點節點）、拖節點、改板材、復原、存檔讀回。"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([])


@pytest.fixture
def ed(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    from dxf_editor import DxfEditor
    w = DxfEditor(mode="MS")
    w.resize(1200, 800)
    w.show()
    QTest.qWait(50)
    w.canvas.fit()
    yield w
    w.dirty = False
    w.close()


def _vp(ed, x, y) -> QPoint:
    return ed.canvas.mapFromScene(QPointF(x, y))


def _click(ed, x, y, button=Qt.MouseButton.LeftButton, double=False):
    p = _vp(ed, x, y)
    vp = ed.canvas.viewport()
    QTest.mouseMove(vp, p)
    if double:
        QTest.mouseDClick(vp, button, Qt.KeyboardModifier.NoModifier, p)
    else:
        QTest.mouseClick(vp, button, Qt.KeyboardModifier.NoModifier, p)


def test_draw_path_with_nodes_and_save(ed, tmp_path):
    d = ed.design
    assert d.mode == "MS" and len(d.shapes) == 2
    ed.set_tool("path")
    ed.new_width.setValue(0.5)
    for x, y in ((0, 5), (10, 5), (10, 15)):
        _click(ed, x, y)
    QTest.keyClick(ed.canvas, Qt.Key.Key_Return)
    paths = [s for s in d.shapes if s.kind == "path"]
    assert len(paths) == 2
    new = paths[-1]
    assert new.pts == [(0, 5), (10, 5), (10, 15)] and new.width == pytest.approx(0.5)
    # 拖最後一個節點到上板邊（吸附板邊 → 變成 Wave Port）
    ed.set_tool("select")
    ed.canvas.select(d.shapes.index(new))
    vp = ed.canvas.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(ed, 10, 15))
    QTest.mouseMove(vp, _vp(ed, 10, 19.95))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _vp(ed, 10, 19.95))
    assert new.pts[-1] == (10, 20)
    assert ed.design.ports()[-1][1] == "上"
    # 復原 → 回到拖之前
    ed.undo()
    assert ed.design.shapes[-1].pts[-1] == (10, 15)
    ed.redo()
    assert ed.design.shapes[-1].pts[-1] == (10, 20)
    # 表格改節點
    ed.canvas.select(len(ed.design.shapes) - 1)
    ed.table.item(1, 0).setText("12")
    assert ed.design.shapes[-1].pts[1] == (12, 5)
    # 改板材：端點跟著板邊
    ed.sp_w.setValue(40)
    ed.sp_h.setValue(25)
    ed.apply_board()
    assert ed.design.board_bounds() == pytest.approx((0, 0, 40, 25))
    assert ed.design.shapes[-1].pts[-1] == (10, 25)
    f = str(tmp_path / "edited.dxf")
    got = []
    ed.saved.connect(got.append)
    assert ed._save_to(f) and got == [f]
    from dxf_model import Design
    r = Design.load(f)
    assert sorted(s.kind for s in r.shapes) == ["path", "path", "poly"]


def test_add_and_delete_nodes_rect_circle(ed):
    d = ed.design
    path = next(s for s in d.shapes if s.kind == "path")
    ed.set_tool("select")
    ed.canvas.select(d.shapes.index(path))
    _click(ed, 15, 10, double=True)                     # 雙擊線段 → 加節點
    assert path.pts == [(0, 10), (15, 10), (30, 10)]
    ed.delete_node(1)
    assert path.pts == [(0, 10), (30, 10)]
    ed.delete_node(0)                                   # 只剩 2 個：不能刪
    assert len(path.pts) == 2
    ed.combo_layer.setCurrentIndex(ed.combo_layer.findData("h"))
    ed.set_tool("circle")
    _click(ed, 3, 3)
    _click(ed, 4, 3)
    hole = d.shapes[-1]
    assert hole.kind == "circle" and hole.layer == "h" and hole.r == pytest.approx(1.0)
    ed.combo_layer.setCurrentIndex(ed.combo_layer.findData("g"))
    ed.set_tool("rect")
    _click(ed, 5, 12)
    _click(ed, 8, 14)
    assert d.shapes[-1].kind == "poly" and d.shapes[-1].layer == "g" and len(d.shapes[-1].pts) == 4
    ed.delete_selected()
    assert d.shapes[-1] is hole


def test_cpw_mode_switch(ed):
    ed.combo_mode.setCurrentText("CPW")
    assert ed.design.mode == "CPW" and ed.design.auto_ground
    assert any(s.layer == "db" for s in ed.design.shapes)
    assert len(ed.design.generated()["ub"]) == 2
    ed.undo()
    assert ed.design.mode == "MS"
