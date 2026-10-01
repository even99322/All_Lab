"""v0.19B Settings > Personal: user colours and SVG icons."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app.core import data_location, personal

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QApplication.instance() or QApplication([])
    user = tmp_path / "user"
    (user / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr(data_location, "legacy_sources", lambda: [])
    data_location._reset_for_tests()
    personal.reset_cache()
    # v0.19C locks Personal colours until 10 data operations; these tests cover the colours themselves.
    from app._guard import gate

    monkeypatch.setattr(gate, "personal_colors_unlocked", lambda: True)
    yield user
    personal.reset_cache()
    data_location._reset_for_tests()


GOOD = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M4 4h16v16H4z" fill="#ff0000" stroke="#00aa00"/></svg>'


def test_user_colours_override_the_palette_and_reset(home):
    from app.core import personal
    from app.palette import APP_DARK

    assert personal.effective("app_dark", APP_DARK) is APP_DARK
    personal.set_color("app_dark", "accent", "#ff8800")
    assert personal.effective("app_dark", APP_DARK).accent == "#FF8800"
    with pytest.raises(ValueError):
        personal.set_color("app_dark", "accent", "orange")
    personal.reset_colors("app_dark", "accent")
    assert personal.effective("app_dark", APP_DARK).accent == APP_DARK.accent


def test_theme_manager_uses_personal_colours(home, tmp_path):
    from PySide6.QtWidgets import QApplication
    from app.core import personal
    from app.settings.store import SettingsStore
    from app.theme import ThemeManager

    app = QApplication.instance()
    manager = ThemeManager(app, SettingsStore(tmp_path / "s.json"), app)
    manager.set_mode("light")
    personal.set_color("app_light", "window", "#123456")
    manager.refresh_personal()
    assert manager.colors.window == "#123456"
    assert app.palette().color(app.palette().ColorRole.Window).name().upper() == "#123456"
    personal.set_color("plot_white", "background", "#FAFAF0")
    assert manager.plot_colors.background == "#FAFAF0"
    manager.detach()


def test_contrast_ratio():
    from app.core.personal import contrast_ratio

    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#808080") < 1.2


@pytest.mark.parametrize("bad", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><path d="M0 0h1"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink"><use x:href="http://evil/x.svg#a"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AAAA"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
    b'<html><body/></html>',
    b'not xml',
])
def test_unsafe_or_invalid_svg_is_rejected(bad):
    from app.core.personal import SvgRejected, validate_svg

    with pytest.raises(SvgRejected):
        validate_svg(bad)


def test_uploaded_svg_replaces_icon_and_can_follow_theme(home, tmp_path):
    from app.core import personal

    source = tmp_path / "my_open.svg"
    source.write_bytes(GOOD)
    with pytest.raises(personal.SvgRejected):
        png = tmp_path / "x.png"
        png.write_bytes(b"\x89PNG")
        personal.set_icon("open", png)
    personal.set_icon("open", source, follow_theme=True)
    data = personal.icon_override("open")
    assert b"currentColor" in data and b"#ff0000" not in data
    personal.set_icon("open", source, follow_theme=False)
    assert b"#ff0000" in personal.icon_override("open")
    (personal.icon_folder() / "open.svg").write_bytes(b"<svg><script/></svg>")   # edited on disk
    assert personal.icon_override("open") is None                                  # falls back to built-in
    personal.reset_icon("open")
    assert personal.icon_override("open") is None


def test_personal_page_in_settings(home, tmp_path):
    from app.settings.dialog import SettingsDialog

    dialog = SettingsDialog()
    try:
        dialog.show_page("personal")
        page = dialog.personal_page
        assert page.tree.topLevelItemCount() == 5
        first = page.tree.topLevelItem(1).child(0)          # dark theme > window
        page.tree.setCurrentItem(first)
        page.hex_edit.setText("#202020")
        page._hex_entered()
        page._commit()
        from app.core import personal
        assert personal.color("app_dark", "window") == "#202020"
        source = tmp_path / "icon.svg"
        source.write_bytes(GOOD)
        assert page.replace_icon_from("reload", str(source)) is None
        assert page.replace_icon_from("reload", str(tmp_path / "missing.svg")) is not None
    finally:
        dialog.close()
