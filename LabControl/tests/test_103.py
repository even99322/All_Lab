"""1.0.3：Windows 工作列的程式 ID 固定（不帶版本號），更新後釘選在工作列的捷徑仍然有效。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_app_user_model_id_has_no_version():
    from labcontrol import __version__
    from labcontrol.app import APP_USER_MODEL_ID

    assert APP_USER_MODEL_ID == "CCUQEL.LabControl" and __version__ not in APP_USER_MODEL_ID
    src = (ROOT / "labcontrol" / "app.py").read_text(encoding="utf-8")
    assert "SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)" in src
    mon = (ROOT / "labmonitor" / "app.py").read_text(encoding="utf-8")
    assert "__version__}\")" not in mon and "CCUQEL.LabControlMonitor" in mon


def test_installer_shortcuts_carry_the_same_id():
    iss = (ROOT / "packaging" / "labcontrol.iss").read_text(encoding="utf-8")
    icons = iss.split("[Icons]", 1)[1].split("[Run]", 1)[0]
    program = [line for line in icons.splitlines() if "LabControl.exe" in line and not line.startswith(";")]
    assert len(program) == 2
    assert all(re.search(r'AppUserModelID: "CCUQEL\.LabControl"', line) for line in program)
