"""v2.1.5: lab data folder on the NAS, smooth 2D heatmap, pet pictures as an optional download."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


# ---- smooth heatmap ----------------------------------------------------------------------------------
def test_smooth_display_upsamples_few_sweeps_and_keeps_values():
    from app.gui.plot_2d_widget import SMOOTH_TARGET, smooth_display

    x = np.linspace(0, 1, 2001)
    y = np.linspace(-5, 5, 77)
    z = np.add.outer(y, x)                               # z[y, x], linear: interpolation is exact
    out = smooth_display(x, y, z)
    assert out.shape == (SMOOTH_TARGET, 2001)
    yy = np.linspace(-5, 5, SMOOTH_TARGET)
    assert np.allclose(out, np.add.outer(yy, x))
    assert out.min() >= z.min() - 1e-9 and out.max() <= z.max() + 1e-9


def test_smooth_display_caps_pixels_and_keeps_big_images():
    from app.gui.plot_2d_widget import SMOOTH_MAX_PIXELS, smooth_display

    x = np.arange(10001.0)
    y = np.arange(77.0)
    out = smooth_display(x, y, np.zeros((77, 10001)))
    assert out.shape[1] == 10001 and 77 < out.shape[0] and out.size <= SMOOTH_MAX_PIXELS
    big = np.ones((1500, 1500))
    assert smooth_display(np.arange(1500.0), np.arange(1500.0), big) is big


def test_smooth_display_places_uneven_axis_correctly():
    from app.gui.plot_2d_widget import smooth_display

    x = np.array([0.0, 1.0, 10.0])                        # uneven: column 2 is far away
    y = np.arange(1500.0)
    z = np.tile(x, (1500, 1))
    out = smooth_display(x, y, z)
    assert np.allclose(out[0], np.linspace(0, 10, out.shape[1]))


def test_smooth_switch_replots(qapp):
    from app.gui.plot_2d_widget import Plot2DWidget

    old = Plot2DWidget.smooth
    try:
        Plot2DWidget.set_smooth_all(False)
        assert Plot2DWidget.smooth is False
        Plot2DWidget.set_smooth_all(True)
        assert Plot2DWidget.smooth is True
    finally:
        Plot2DWidget.smooth = old


# ---- lab data folder ------------------------------------------------------------------------------------
def test_lab_share_override_and_unreachable(tmp_path, monkeypatch):
    from app.core import lab_share

    missing = tmp_path / "nowhere"
    there = tmp_path / "數據集散地"
    there.mkdir()
    monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", f"{missing};{there}")
    assert lab_share.candidates() == [str(missing), str(there)]
    assert lab_share.find(timeout=2) == str(there)
    monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", str(missing))
    assert lab_share.find(timeout=2) is None


def test_lab_share_default_places(monkeypatch):
    from app.core import lab_share

    monkeypatch.delenv("LABLOGVIEWER_LAB_DATA", raising=False)
    monkeypatch.setattr(lab_share.sys, "platform", "win32")
    assert lab_share.candidates() == [r"\\192.168.50.2\ccuqel\數據集散地", r"\\100.114.33.20\ccuqel\數據集散地"]


def test_browser_open_lab_data(qapp, tmp_path, monkeypatch):
    from app.gui.browser_window import BrowserWindow

    there = tmp_path / "數據集散地"
    there.mkdir()
    monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", str(there))
    window = BrowserWindow()
    opened = []
    monkeypatch.setattr(window, "open_database", opened.append)
    try:
        assert window.open_lab_data_action.objectName() == "browserOpenLabData"
        assert window.open_lab_data(ask=False) is True
        assert opened == [str(there)]
        monkeypatch.setenv("LABLOGVIEWER_LAB_DATA", str(tmp_path / "missing"))
        assert window.open_lab_data(ask=False) is False
    finally:
        window.close()
        window.deleteLater()


# ---- pet pictures -----------------------------------------------------------------------------------------
def _fake_assets(root: Path) -> Path:
    from app.pet.pack import BUNDLED

    src = BUNDLED / "capybara" / "lv01"
    if not (src / "meta.json").is_file():
        pytest.skip("source pet pictures are not in this checkout")
    dst = root / "capybara" / "lv01"
    dst.mkdir(parents=True)
    (dst / "meta.json").write_bytes((src / "meta.json").read_bytes())
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    ext = meta.get("ext", "png")
    for clip, info in meta["clips"].items():
        (dst / clip).mkdir()
        for i in range(info["frames"]):
            name = f"frame_{i + 1:04d}.{ext}"
            (dst / clip / name).write_bytes((src / clip / name).read_bytes())
    return root


def test_pets_dir_and_override(tmp_path, monkeypatch):
    from app.pet import pack

    monkeypatch.setenv("LLV_PETS_DIR", str(tmp_path / "p"))
    assert pack.pets_dir() == tmp_path / "p"
    monkeypatch.delenv("LLV_PETS_DIR")
    monkeypatch.setattr(pack.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert pack.pets_dir() == tmp_path / "Local" / "LabLogViewer" / "pets"


def test_pack_zip_install_and_resolve(tmp_path, monkeypatch):
    from app.pet import pack, registry
    from app.pet.character import Character

    assets = _fake_assets(tmp_path / "assets")
    zip_path = pack.build_zip(assets, tmp_path / "LabLogViewerPets_v1.0.0.zip", "1.0.0")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert pack.MANIFEST in names and "capybara/lv01/meta.json" in names

    target = tmp_path / "user" / "pets"
    monkeypatch.setenv("LLV_PETS_DIR", str(target))
    monkeypatch.setattr(pack, "BUNDLED", tmp_path / "no-bundle")
    assert not pack.available()
    pack.install_zip(zip_path)
    assert pack.available() and pack.assets_root() == target
    assert pack.installed_info()["pets"] == ["capybara"]
    assert pack.resolve(Path("x"), "../assets/capybara/lv01") == target / "capybara" / "lv01"
    char = Character(registry.get("capybara").pack, 1)
    assert char.root == (target / "capybara" / "lv01").resolve()
    assert registry.get("capybara").preview == target / "capybara" / "lv01" / "idle" / "frame_0001.webp"
    pack.install_zip(zip_path)                            # again: replaces the folder
    assert pack.available() and not target.with_name("pets.old").exists()


def test_pack_rejects_unsafe_and_empty_zip(tmp_path, monkeypatch):
    from app.pet import pack

    monkeypatch.setenv("LLV_PETS_DIR", str(tmp_path / "pets"))
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError):
        pack.install_zip(bad)
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(ValueError):
        pack.install_zip(empty)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "pets").exists()


def test_install_from_hub(tmp_path, monkeypatch):
    from app.pet import pack

    zip_path = pack.build_zip(_fake_assets(tmp_path / "assets"), tmp_path / "LabLogViewerPets_v1.0.0.zip", "1.0.0")
    body = zip_path.read_bytes()
    sha = hashlib.sha256(body).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/get/setup.txt"):
                data = (f"lablogviewer|2.1.5|LabLogViewer_v2.1.5_windows-setup.exe|10|{'0' * 64}|/get/x\n"
                        f"pets|1.0.0|{zip_path.name}|{len(body)}|{sha}|/get/llvpets/1.0.0/{zip_path.name}\n").encode()
            elif self.path == f"/get/llvpets/1.0.0/{zip_path.name}":
                data = body
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        monkeypatch.setenv("QEL_HUB", base)
        monkeypatch.setenv("LLV_PETS_DIR", str(tmp_path / "pets"))
        info = pack.find_on_hub(timeout=3)
        assert info["version"] == "1.0.0" and info["size"] == len(body) and info["url"].startswith(base)
        seen = []
        pack.install_from_hub(lambda d, t: seen.append((d, t)), info=info)
        assert seen and seen[-1][0] == len(body)
        assert pack.installed_info()["version"] == "1.0.0"
    finally:
        server.shutdown()


def test_pet_page_shows_download_when_missing(qapp, tmp_path, monkeypatch):
    from app.gui.pet_page import PetPage
    from app.localization import initialize_localization
    from app.pet import pack

    monkeypatch.setenv("LLV_PETS_DIR", str(tmp_path / "pets"))
    monkeypatch.setattr(pack, "BUNDLED", tmp_path / "no-bundle")
    localizer = initialize_localization(qapp)
    localizer.set_language("en")
    page = PetPage(localizer)
    try:
        assert page.pics_button.isVisibleTo(page)
        assert "optional download" in page.pics_status.text()
        assert all(not card.button.isEnabled() for card in page.cards)
    finally:
        page.deleteLater()
