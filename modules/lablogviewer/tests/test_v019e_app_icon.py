"""v0.19E: the application icon can be chosen in Settings > Appearance (no licence)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_four_icons_ship_at_packaging_size():
    from PySide6.QtGui import QImage

    from app.gui import app_icon

    for number in range(1, app_icon.COUNT + 1):
        image = QImage(str(app_icon.icon_path(number)))
        assert not image.isNull() and image.width() == image.height() == 1024


def test_setting_is_saved_validated_and_applied(qapp, tmp_path):
    from app.gui.app_icon import app_icon, apply_app_icon
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.app_icon() == 4                        # the default (also the packaged app's icon)
    store.set_app_icon(3)
    assert json.loads((tmp_path / "settings.json").read_text())["general"]["app_icon"] == 3
    assert SettingsStore(tmp_path / "settings.json").app_icon() == 3
    for bad in (0, 5, True, "2"):
        with pytest.raises(ValueError):
            store.set_app_icon(bad)
    (tmp_path / "broken.json").write_text(json.dumps({"general": {"app_icon": 9}}))
    assert SettingsStore(tmp_path / "broken.json").app_icon() == 4
    assert apply_app_icon(store) == 3
    assert qapp.windowIcon().cacheKey() == app_icon(3).cacheKey()


def test_settings_page_changes_the_icon_without_a_licence(qapp, tmp_path):
    from app.gui.app_icon import app_icon
    from app.localization import initialize_localization
    from app.settings.dialog import SettingsDialog

    localizer = initialize_localization(qapp)
    dialog = SettingsDialog(localizer)
    buttons = dialog.app_icon_buttons
    assert len(buttons.buttons()) == 4 and dialog.app_icon_group.isEnabled()
    buttons.button(2).click()
    assert localizer.store.app_icon() == 2
    assert qapp.windowIcon().cacheKey() == app_icon(2).cacheKey()
    assert dialog.windowIcon().cacheKey() == app_icon(2).cacheKey()
    localizer.set_language("zh_TW")
    assert dialog.app_icon_group.title() == "App 圖示"
    localizer.set_language("en")
    dialog.close()
