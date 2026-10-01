"""v0.19C licences, machine code, data operation experience and the Personal colour lock.

Keys are generated for each test run; the developer's real key is never used.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

DEVTOOLS = Path(__file__).resolve().parents[2] / "LabLogViewer_Plugins" / "DevTools"
sys.path.insert(0, str(DEVTOOLS))
dev = pytest.importorskip("llv_devtools")


@pytest.fixture
def dev_key(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app._guard import trusted_key

    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", dev.public_hex(key))
    return key


def _files(folder: Path, count: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = folder / f"m{index}.hdf5"
        path.write_bytes(os.urandom(64) + bytes([index]))
        paths.append(path)
    return paths


def _machine():
    from app._guard.machine import machine_code

    return machine_code()


# -- machine code -------------------------------------------------------------------------
def test_machine_code_is_stable_and_well_formed():
    from app._guard.machine import format_code, is_machine_code, machine_code

    assert machine_code() == machine_code()
    assert is_machine_code(machine_code())
    assert is_machine_code(format_code(b"\x00" * 32)) and format_code(b"\x01" * 32) != format_code(b"\x02" * 32)
    assert not is_machine_code("LLV-0000-1111-OOOO-IIII")


# -- licences -----------------------------------------------------------------------------
def test_issued_licence_installs_and_grants_features(tmp_path, dev_key):
    from app._guard import gate, license as licenses

    path = tmp_path / "a.llvkey"
    path.write_bytes(dev.issue(dev_key, _machine(), "Student A", ["personal_colors"]))
    found = licenses.install(path)
    assert found.holder == "Student A" and found.features == ("personal_colors",)
    assert found.expires - found.issued == timedelta(days=60)
    assert (licenses.licenses_dir() / f"{found.id}.llvkey").is_file()
    assert gate.personal_colors_status()[0] and gate.personal_colors_status()[2].id == found.id
    assert gate.relink_allowed() is None
    licenses.remove(found.path)
    assert licenses.installed() == []


@pytest.mark.parametrize("case, reason", [
    ("other_machine", "machine"), ("tampered", "signature"), ("wrong_key", "signature"),
    ("garbage", "format"), ("expired", "expired"), ("future", "clock"),
])
def test_invalid_licences_are_refused(tmp_path, dev_key, case, reason):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app._guard import license as licenses

    machine, today = _machine(), date.today()
    if case == "other_machine":
        other = "LLV-AAAA-AAAA-AAAA-AAAA" if machine != "LLV-AAAA-AAAA-AAAA-AAAA" else "LLV-CCCC-CCCC-CCCC-CCCC"
        raw = dev.issue(dev_key, other, "x")
    elif case == "tampered":
        document = json.loads(dev.issue(dev_key, machine, "x", days=1))
        payload = json.loads(base64.b64decode(document["payload"]))
        payload["expires"] = "2099-01-01"
        document["payload"] = base64.b64encode(dev.canonical(payload)).decode()
        raw = json.dumps(document).encode()
    elif case == "wrong_key":
        raw = dev.issue(Ed25519PrivateKey.generate(), machine, "x")
    elif case == "garbage":
        raw = b"{not json"
    elif case == "expired":
        raw = dev.issue(dev_key, machine, "x", days=60, today=today - timedelta(days=61))
    else:
        raw = dev.issue(dev_key, machine, "x", today=today + timedelta(days=3))
    with pytest.raises(licenses.LicenseError) as error:
        licenses.parse(raw)
    assert error.value.reason == reason


def test_no_public_key_means_no_licence(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app._guard import license as licenses, trusted_key

    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", "")
    with pytest.raises(licenses.LicenseError) as error:
        licenses.parse(dev.issue(Ed25519PrivateKey.generate(), _machine(), "x"))
    assert error.value.reason == "no_key"


def test_turning_the_clock_back_does_not_revive_an_expired_licence(dev_key):
    from app._guard import experience, license as licenses

    raw = dev.issue(dev_key, _machine(), "x", days=10)
    real_now = time.time()
    # The app has already seen a date after the expiry ...
    experience.trusted_now(real_now + 40 * 86400)
    # ... so setting the clock back to "today" does not help.
    with pytest.raises(licenses.LicenseError) as error:
        licenses.parse(raw, now=real_now)
    assert error.value.reason == "expired"


# -- experience ---------------------------------------------------------------------------
def test_experience_counts_distinct_files_once(tmp_path):
    from app._guard import experience, gate

    files = _files(tmp_path / "data", 3)
    assert experience.count() == 0 and experience.remaining() == 10
    assert gate.note_operation(files[0]) is True
    assert gate.note_operation(files[0]) is False            # same file again
    assert gate.note_operation(files[1]) is True
    assert gate.note_operation("lablogviewer-remote://host/x.hdf5") is False
    assert gate.note_operation(tmp_path / "missing.hdf5") is False
    assert experience.count() == 2
    stored = experience.store_path().read_text(encoding="utf-8")
    assert "m0.hdf5" not in stored and str(tmp_path) not in stored   # only hashes are kept
    experience.reset_cache()
    assert experience.count() == 2


def test_edited_experience_file_is_not_trusted(tmp_path):
    from app._guard import experience

    for path in _files(tmp_path / "data", 3):
        experience.record(str(path))
    data = json.loads(experience.store_path().read_text(encoding="utf-8"))
    data["files"] += [f"{i:032x}" for i in range(10)]
    experience.store_path().write_text(json.dumps(data), encoding="utf-8")
    experience.reset_cache()
    assert experience.count() == 0 and experience.was_tampered()


def test_ten_experiences_unlock_personal_colours(tmp_path):
    from app._guard import gate
    from app.core import personal
    from app.palette import APP_DARK

    unlocked = []
    gate.add_unlock_listener(lambda: unlocked.append(True))
    try:
        with pytest.raises(personal.ColorsLocked):
            personal.set_color("app_dark", "accent", "#FF8800")
        # A hand-written override is not applied while locked.
        personal._data()["colors"]["app_dark"] = {"accent": "#FF8800"}
        assert personal.effective("app_dark", APP_DARK) is APP_DARK
        files = _files(tmp_path / "data", 10)
        for path in files[:9]:
            gate.note_operation(path)
        gate.invalidate()
        assert gate.personal_colors_status()[:2] == (False, 1)
        gate.note_operation(files[9])
        assert unlocked == [True]
        assert personal.effective("app_dark", APP_DARK).accent == "#FF8800"
        personal.set_color("app_dark", "accent", "#123456")
    finally:
        gate._listeners.clear()


# -- DevTools -----------------------------------------------------------------------------
def test_devtools_key_file_is_portable_and_password_protected(tmp_path):
    key_file = tmp_path / "developer_key.llvdev"
    public = dev.create_key(key_file, "correct horse")
    with pytest.raises(dev.DevToolsError):
        dev.create_key(key_file, "correct horse")            # never overwrite by accident
    with pytest.raises(dev.DevToolsError):
        dev.load_key(key_file, "wrong password")
    moved = tmp_path / "elsewhere" / "k.llvdev"
    moved.parent.mkdir()
    moved.write_bytes(key_file.read_bytes())                  # e.g. handed to a successor
    dev.change_password(moved, "correct horse", "battery staple")
    assert dev.public_hex(dev.load_key(moved, "battery staple")) == public
    with pytest.raises(dev.DevToolsError):
        dev.load_key(moved, "correct horse")
    with pytest.raises(dev.DevToolsError):
        dev.issue(dev.load_key(moved, "battery staple"), "not a code", "x")


def test_devtools_installs_public_key_into_a_copy(tmp_path):
    app_copy = tmp_path / "LabLogViewer_copy" / "app" / "_guard"
    app_copy.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "app" / "_guard" / "trusted_key.py"
    (app_copy / "trusted_key.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    key_file = tmp_path / "k.llvdev"
    public = dev.create_key(key_file, "password1")
    dev.install_public_key(public, tmp_path / "LabLogViewer_copy")
    text = (app_copy / "trusted_key.py").read_text(encoding="utf-8")
    assert f'PUBLIC_KEY_HEX = "{public}"' in text
    assert dev.main(["show", "--license", str(_write(tmp_path, dev.issue(dev.load_key(key_file, "password1"),
                                                                           _machine(), "B")))]) == 0


def _write(folder: Path, raw: bytes) -> Path:
    path = folder / "x.llvkey"
    path.write_bytes(raw)
    return path


def test_guard_package_is_the_only_place_that_decides():
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if "_guard" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "PUBLIC_KEY_HEX" in text or "_APP_SECRET" in text or "experience.record(" in text:
            offenders.append(str(path))
    assert offenders == []


def test_build_refuses_plugins_and_developer_keys(tmp_path):
    """Packaging must never ship the plugins folder, the sandbox or a developer key file."""
    import build_guard

    build = tmp_path / "build"
    (build / "app" / "_guard").mkdir(parents=True)
    assert build_guard.forbidden_content(build) == []
    (build / "LabLogViewer_Plugins" / "DevTools").mkdir(parents=True)
    (build / "notes" ).mkdir()
    (build / "notes" / "developer_key.llvdev").write_bytes(b"x")
    (build / "someone_ABCD_20260928.llvkey").write_bytes(b"x")
    names = {p.name for p in build_guard.forbidden_content(build)}
    assert {"LabLogViewer_Plugins", "developer_key.llvdev", "someone_ABCD_20260928.llvkey"} <= names
    with pytest.raises(SystemExit):
        build_guard.compile_guard(build)



def test_key_installed_while_running_is_picked_up(tmp_path, monkeypatch):
    """v0.19E: a public key installed by DevTools while LabLogViewer is open works without a
    restart (the module was read once and kept the empty key)."""
    import importlib

    from app._guard import license as licenses, trusted_key

    copy = tmp_path / "trusted_key.py"
    copy.write_text('PUBLIC_KEY_HEX = ""\n', encoding="utf-8")
    monkeypatch.setattr(trusted_key, "__file__", str(copy))
    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", "")
    monkeypatch.setattr(licenses, "_key_file_stamp", None)
    assert licenses._installed_key_hex() == ""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public = dev.public_hex(Ed25519PrivateKey.generate())
    time.sleep(0.02)
    copy.write_text(f'PUBLIC_KEY_HEX = "{public}"\n', encoding="utf-8")
    os.utime(copy, (time.time() + 5, time.time() + 5))
    # stand-in for re-importing the real module, which would undo other tests' patches
    monkeypatch.setattr(importlib, "reload", lambda module: type(
        "Reloaded", (), {"PUBLIC_KEY_HEX": copy.read_text().split('"')[1], "__file__": str(copy)}))
    assert licenses._installed_key_hex() == public


def test_licence_window_names_the_program_folder_and_key_status(monkeypatch):
    """v0.19E: "no developer key" could not be traced; the window now says which LabLogViewer
    folder is running and whether its public key is installed."""
    from PySide6.QtWidgets import QApplication

    from app._guard import trusted_key
    from app.gui.license_dialog import LicenseDialog
    from app.localization import initialize_localization

    qapp = QApplication.instance() or QApplication([])
    localizer = initialize_localization(qapp)
    localizer.set_language("en")
    root = str(Path(__file__).resolve().parents[1])
    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", "")
    dialog = LicenseDialog(localizer)
    assert root in dialog.key_line.text() and "not installed" in dialog.key_line.text()
    licence = dev.issue(_any_key(), _machine(), "A")
    message = dialog.install_from(str(_write_tmp(licence)))
    assert "no developer key" in message and root in message
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    monkeypatch.setattr(trusted_key, "PUBLIC_KEY_HEX", dev.public_hex(Ed25519PrivateKey.generate()))
    dialog = LicenseDialog(localizer)
    assert "installed (" in dialog.key_line.text()
    dialog.close()



def _any_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


def _write_tmp(raw: bytes) -> Path:
    import tempfile

    handle, name = tempfile.mkstemp(suffix=".llvkey")
    os.close(handle)
    Path(name).write_bytes(raw)
    return Path(name)


def test_packaging_embeds_every_trusted_issuer_key():
    """v0.19F: several licence issuers; build_guard embeds all of them, in order."""
    import build_guard

    first, second = "a" * 64, "b" * 64
    text = f'PUBLIC_KEY_HEX = "{first}"\nADDITIONAL_KEYS: dict[str, str] = {{\n    "Successor": "{second}",\n}}\n'
    assert build_guard.trusted_keys_in(text) == [first, second]
    assert build_guard.trusted_keys_in(f'PUBLIC_KEY_HEX = "{first}"\n') == [first]      # the v0.19E format
    assert build_guard.trusted_keys_in('PUBLIC_KEY_HEX = ""\n') == []


def test_embedded_keys_accept_any_listed_issuer(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app._guard import license as licenses

    one, two = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    monkeypatch.setattr(licenses, "_EMBEDDED_KEY", f"{dev.public_hex(one)},{dev.public_hex(two)}")
    for key in (one, two):
        raw = dev.issue(key, _machine(), "Someone")
        assert licenses.parse(raw).holder == "Someone"
    installed, fingerprints = licenses.key_status()
    assert installed and fingerprints.count("…") == 2
    with pytest.raises(licenses.LicenseError) as error:
        licenses.parse(dev.issue(Ed25519PrivateKey.generate(), _machine(), "Stranger"))
    assert error.value.reason == "signature"
