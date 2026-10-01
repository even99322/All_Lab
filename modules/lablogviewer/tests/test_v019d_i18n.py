"""v0.19D: the whole interface is translated to Traditional Chinese (static and dynamic text),
drop-downs keep their English values for the code, and Help shows pictures in both languages."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
# Technical values that are shown as they are (solver names, icon ids, log messages, symbols ...)
NOT_INTERFACE_TEXT = {
    "3D Surface — {}", "AND", "OR", "Magnitude |S|", "S_ideal =", "T_avg |S|", "Trace {} / {}{}", "Trace —", "__chi2",
    "abs", "analysis", "annotate", "arctan", "back", "cauchy", "clear", "copy", "debackground", "dogbox", "export",
    "f_ref", "filter", "huber", "laser", "max nfev", "maximize", "mean_pos", "nfev = {}   cost = {}   {} s", "open",
    "pen", "phase_unwrapped_deg", "phase_unwrapped_rad", "reload", "save", "show_trace", "soft_l1", "star",
    "star_filled", "tag", "trf", "view_all", "zoom", "{} px", "|φ − nP| <", "κm (Hz)", "φ_ref",
    "LabLogViewer 3D scene (raster image)", "LabLogViewer figure", "LabLogViewer scientific plot",
    "LabLogViewer v{}", "LabLogViewer v{} — {}",
}
CALLS = {"QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "QAction", "QToolButton", "addItem",
         "addItems", "addTab", "insertTab", "setTabText", "setText", "setTitle", "setToolTip", "setWindowTitle",
         "setPlaceholderText", "addMenu", "showMessage", "setHeaderLabels", "warning", "information", "critical",
         "question", "addAction", "addRow", "setInformativeText", "addButton", "make_icon_only"}


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _strings(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        yield "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            yield from _strings(element)
    elif isinstance(node, ast.IfExp):
        yield from _strings(node.body)
        yield from _strings(node.orelse)


def test_every_interface_string_in_the_code_has_chinese():
    """New features must add their Chinese (catalogs.py or ui_strings.py) -- this finds any that did not."""
    from app.localization.catalogs import CATALOGS
    from app.localization.manager import translate_text
    from app.localization.terminology import SCIENTIFIC_TERMS

    english = set(CATALOGS["en"].values())
    missing = set()
    for path in APP.rglob("*.py"):
        if "_guard" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name not in CALLS:
                continue
            for argument in list(node.args) + [k.value for k in node.keywords]:
                for text in _strings(argument):
                    text = text.strip()
                    if (not re.search(r"[A-Za-z]{2}", text) or text in english or text in SCIENTIFIC_TERMS
                            or text in NOT_INTERFACE_TEXT or "%s" in text or "%r" in text
                            or re.search(r"[一-鿿]", text) or text.startswith(("#", "color:", "font-", "QLabel"))
                            or re.fullmatch(r"[\w.\-/]+\.(png|svg|json|hdf5|h5|py)", text)):
                        continue
                    sample = text.replace("{}", "7")
                    if translate_text(sample, "zh_TW") == sample:
                        missing.add(f"{path.relative_to(APP)}: {text!r}")
    assert sorted(missing) == []


def test_patterns_keep_their_values():
    from app.localization.manager import translate_text
    from app.localization.ui_strings import PATTERNS

    for english, chinese in PATTERNS:
        count = english.count("{}")
        indexes = {int(i) for i in re.findall(r"\{(\d+)\}", chinese)}
        assert indexes <= set(range(count)), english
    assert translate_text("Rendering frame 3 / 90", "zh_TW") == "正在算圖第 3 / 90 幀"
    assert translate_text("LabLogViewer v0.19D — Database Browser", "zh_TW") == "LabLogViewer v0.19D — 資料庫瀏覽器"
    assert translate_text("VNA - S21", "zh_TW") == "VNA - S21"                       # data names stay


def test_dynamic_texts_follow_the_language_and_combos_keep_values(qapp):
    from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

    from app.localization import initialize_localization

    localizer = initialize_localization(qapp)
    window = QWidget()
    layout = QVBoxLayout(window)
    label = QLabel("Pending")
    combo = QComboBox()
    combo.addItems(["Surface", "Transparent Surface"])
    layout.addWidget(label)
    layout.addWidget(combo)
    window.show()
    localizer.set_language("zh_TW")
    assert label.text() == "等待中"
    label.setText("Select Target and Background Data.")                  # set while the window is open
    assert label.text() == "請選擇目標與背景資料。"
    assert combo.currentText() == "Surface"                              # the code still reads English
    assert localizer._translated(combo.itemText(1)) == "透明曲面"          # what the user sees
    localizer.set_language("en")
    assert label.text() == "Select Target and Background Data."
    window.close()

