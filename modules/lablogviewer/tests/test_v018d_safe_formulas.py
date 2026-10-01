"""v0.18D: one safe expression engine (Viewer + YIG) and trusted .py models."""

from __future__ import annotations

import ast
import json

import numpy as np
import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from app.core import data_location
    from app.analysis.yig_fitting.core import paths, trust

    user = tmp_path / "user"
    (user / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(data_location, "legacy_sources", lambda: [])
    monkeypatch.setattr(paths, "_CONFIG", None)
    data_location._reset_for_tests()
    trust.set_prompt(None)
    yield user
    trust.set_prompt(None)
    data_location._reset_for_tests()


# ---- engine ------------------------------------------------------------------
@pytest.mark.parametrize(("text", "expected"), [
    ("pi/4", np.pi / 4), ("np.deg2rad(30)", np.pi / 6), ("2.5e9", 2.5e9), ("-3.141592654", -3.141592654),
    (r"\frac{1}{2}", 0.5), ("2^3", 8.0), ("2**3", 8.0), ("|-3|", 3.0), (r"\sqrt{16}", 4.0),
    ("sqrt(2)", np.sqrt(2)), ("π/2", np.pi / 2), ("3 × 2", 6.0), ("np.pi", np.pi),
])
def test_numeric_fields_accept_every_spelling(text, expected):
    from app.analysis.yig_fitting.core.formula import parse_num

    assert parse_num(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [
    "__import__('os').system('echo hacked')", "np.load('x.npy')", "().__class__.__bases__",
    "open('f')", "[1][0]", "(lambda: 1)()", "np.__dict__", "'text'", "getattr(np, 'load')",
    "exec('1')", "np.save", "x if 1 else 2", "1 < 2",
])
def test_numeric_fields_never_run_code(text):
    from app.analysis.yig_fitting.core.formula import parse_num

    with pytest.raises(Exception):
        parse_num(text)


def test_viewer_formula_accepts_python_and_latex_styles():
    from app.core.formula import apply_formulas

    x = np.array([1.0, 2.0, 4.0])
    y = np.array([10.0, 100.0, 1000.0])
    for text in ("20*log10(|y|)", "20*np.log10(abs(y))", r"20 \cdot \log_{10}\left|y\right|"):
        assert np.allclose(apply_formulas(x, y, "", text)[1], 20 * np.log10(y))
    assert np.allclose(apply_formulas(x, y, "x**2", "")[0], x ** 2)
    assert np.allclose(apply_formulas(x, y, "", "arctan2(y, x)")[1], np.arctan2(y, x))


# ---- Formula Builder -----------------------------------------------------------
def test_builder_accepts_latex_and_blocks_code():
    from app.analysis.yig_fitting.core import codegen

    parsed = codegen.parse_model(r"S = 1 - \frac{\kappa_{e}}{i(w - \omega_{0}) + \kappa/2}", "w")
    assert parsed["params"] == ["kappa_e", "omega_0", "kappa"]
    assert codegen.parse_model(r"S = \ln(w) * a", "w")["final"] == "log(w) * a"
    for source in ("import os\nS = w", "S = w + open('f')", "S = w.__class__", "S = np.load('x') + w",
                   "S = (lambda: w)()", "S = __import__('os') + w", "S = [w][0]"):
        with pytest.raises(ValueError):
            codegen.parse_model(source, "w")


def test_builder_docstring_cannot_be_escaped():
    from app.analysis.yig_fitting.core import codegen

    spec = dict(name="S21_model", expr='S = 1 + a*w  # """; import os; os.system("x") #', freq_var="w",
                params=[])
    code = codegen.generate_code(spec)
    imports = [node for node in ast.parse(code).body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(alias.name.split(".")[0] == "numpy" for node in imports
               for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module)]))


# ---- trusted .py models --------------------------------------------------------
def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


MODEL = "import numpy as np\n\n\ndef S_line(w, a, b):\n    return a * w + b\n"


def test_bundled_and_builder_models_load_without_asking(home, tmp_path):
    from app.analysis.yig_fitting.core import codegen, trust
    from app.analysis.yig_fitting.core.formula import load_formula_module

    for bundled in trust.BUNDLED_DIR.glob("*.py"):
        assert load_formula_module(str(bundled))[1]
    spec = dict(name="S21_model", expr="S = 1 - k/(1j*(w - w0) + k)", freq_var="w", params=[])
    built = _write(tmp_path / "built.py", codegen.generate_code(spec))
    assert "S21_model" in load_formula_module(str(built))[1]
    # A hand-edited builder file is no longer the builder's output.
    _write(built, built.read_text(encoding="utf-8") + "\nX = 1\n")
    with pytest.raises(trust.UntrustedFormulaError):
        load_formula_module(str(built))


def test_unknown_model_asks_once_and_again_after_edit(home, tmp_path):
    from app.analysis.yig_fitting.core import trust
    from app.analysis.yig_fitting.core.formula import load_formula_module

    model = _write(tmp_path / "shared_model.py", MODEL)
    with pytest.raises(trust.UntrustedFormulaError):
        load_formula_module(str(model))                        # no GUI prompt -> refused
    asked = []
    trust.set_prompt(lambda path, report: asked.append(path) or True)
    assert "S_line" in load_formula_module(str(model))[1]
    assert "S_line" in load_formula_module(str(model))[1]
    assert len(asked) == 1                                     # remembered
    _write(model, MODEL + "\n# changed\n")
    load_formula_module(str(model))
    assert len(asked) == 2                                     # edited -> asked again
    stored = json.loads(trust.store_path().read_text(encoding="utf-8"))
    assert stored["files"][trust.file_hash(model)]["reason"] == "user"


def test_dangerous_model_is_never_loaded_even_if_user_would_trust(home, tmp_path):
    from app.analysis.yig_fitting.core import trust
    from app.analysis.yig_fitting.core.formula import load_formula_module

    marker = tmp_path / "ran.txt"
    evil = _write(tmp_path / "evil.py", MODEL + f"\nimport os\nos.system('touch {marker}')\n")
    trust.set_prompt(lambda path, report: True)
    with pytest.raises(trust.UntrustedFormulaError) as caught:
        load_formula_module(str(evil))
    assert "os" in str(caught.value) and not marker.exists()


def test_models_in_use_before_upgrade_are_trusted_once(home, tmp_path):
    from app.analysis.yig_fitting.core import paths, trust
    from app.analysis.yig_fitting.core.formula import load_formula_module

    elsewhere = _write(tmp_path / "old_project_model.py", MODEL)
    sessions = tmp_path / "user" / "Documents" / "LabLogViewerData" / "fitting" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "abc.json").write_text(json.dumps({"formula": {"path": str(elsewhere), "func": "S_line"}}),
                                       encoding="utf-8")
    assert paths.config_dir().endswith("fitting")
    assert "S_line" in load_formula_module(str(elsewhere))[1]   # no prompt needed
    assert json.loads(trust.store_path().read_text(encoding="utf-8"))["seeded"]


def test_hand_written_products_are_multiplication():
    from app.core.formula import apply_formulas
    from app.core.safe_expr import normalize

    x = np.array([1.0, 2.0]); y = np.array([10.0, 100.0])
    assert np.allclose(apply_formulas(x, y, "", r"20\log_{10}(|y|)")[1], 20 * np.log10(y))
    assert np.allclose(apply_formulas(x, y, "", r"2\pi x")[1], 2 * np.pi * x)
    assert np.allclose(apply_formulas(x, y, "", "3(x+1)")[1], 3 * (x + 1))
    for unchanged in ("1e9", "2.5E-3", "2j", "sqrt (x)", "np.log10(y)", "x2"):
        assert normalize(unchanged) == unchanged


def test_language_switch_after_settings_window_closed():
    import os
    from PySide6.QtWidgets import QApplication
    from app.localization import LocalizationManager
    from app.settings.dialog import SettingsDialog
    from app.settings.store import SettingsStore
    import tempfile

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    localizer = LocalizationManager(SettingsStore(os.path.join(tempfile.mkdtemp(), "settings.json")))
    dialog = SettingsDialog(localizer)
    dialog.show()
    dialog.close()
    dialog.deleteLater()
    app.sendPostedEvents(None, 0)
    from PySide6.QtCore import QEvent
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    localizer.set_language("zh_TW")                      # must not touch the deleted dialog
    localizer.set_language("en")
