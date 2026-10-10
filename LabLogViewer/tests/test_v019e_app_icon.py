"""v0.19E: the application icon can be chosen in Settings > Appearance (no licence)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_seven_icons_and_two_3d_icons_ship_at_packaging_size():
    from PySide6.QtGui import QImage

    from app.gui import app_icon

    for number in range(1, app_icon.COUNT + 1):
        image = QImage(str(app_icon.icon_path(number)))
        assert not image.isNull() and image.width() == image.height() == 1024
    assert app_icon.COUNT == 7 and app_icon.DEFAULT == 5
    for number in range(1, app_icon.THREE_D_COUNT + 1):
        image = QImage(str(app_icon.three_d_icon_path(number)))
        assert not image.isNull() and image.width() == image.height() == 1024


def test_setting_is_saved_validated_and_applied(qapp, tmp_path):
    from app.gui.app_icon import app_icon, apply_app_icon
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert store.app_icon() == 5                        # the default (also the packaged app's icon)
    store.set_app_icon(3)
    assert json.loads((tmp_path / "settings.json").read_text())["general"]["app_icon"] == 3
    assert SettingsStore(tmp_path / "settings.json").app_icon() == 3
    for bad in (-1, 8, True, "2"):
        with pytest.raises(ValueError):
            store.set_app_icon(bad)
    (tmp_path / "broken.json").write_text(json.dumps({"general": {"app_icon": 9}}))
    assert SettingsStore(tmp_path / "broken.json").app_icon() == 5
    assert apply_app_icon(store) == 3
    assert qapp.windowIcon().cacheKey() == app_icon(3).cacheKey()


def test_settings_page_changes_the_icon_without_a_licence(qapp, tmp_path):
    from app.gui.app_icon import app_icon
    from app.localization import initialize_localization
    from app.settings.dialog import SettingsDialog

    localizer = initialize_localization(qapp)
    dialog = SettingsDialog(localizer)
    buttons = dialog.app_icon_buttons
    assert len(buttons.buttons()) == 8 and dialog.app_icon_group.isEnabled()
    buttons.button(2).click()
    assert localizer.store.app_icon() == 2
    assert qapp.windowIcon().cacheKey() == app_icon(2).cacheKey()
    assert dialog.windowIcon().cacheKey() == app_icon(2).cacheKey()
    localizer.set_language("zh_TW")
    assert dialog.app_icon_group.title() == "App 圖示"
    localizer.set_language("en")
    dialog.close()


def _picture(path, width, height):
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    return path


def test_custom_picture_is_imported_squared_and_selectable(qapp, tmp_path):
    from PySide6.QtGui import QImage

    from app.gui import app_icon
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    assert not app_icon.has_custom_icon(store)
    assert app_icon.app_icon(app_icon.CUSTOM, store).cacheKey() == app_icon.app_icon(5).cacheKey()   # no file: default
    target = app_icon.import_custom_icon(_picture(tmp_path / "wide.png", 400, 200), store)
    assert target == tmp_path / "custom_app_icon.png"
    image = QImage(str(target))
    assert image.width() == image.height() == 400                 # padded to a square, not stretched
    assert image.pixelColor(200, 2).alpha() == 0 and image.pixelColor(200, 200).red() == 255
    big = app_icon.import_custom_icon(_picture(tmp_path / "big.png", 3000, 1500), store)
    assert QImage(str(big)).width() == 1024
    store.set_app_icon(app_icon.CUSTOM)
    assert SettingsStore(tmp_path / "settings.json").app_icon() == 0
    assert app_icon.apply_app_icon(store) == 0
    assert qapp.windowIcon().cacheKey() == app_icon.app_icon(0, store).cacheKey()


def test_bad_pictures_are_refused_and_change_nothing(tmp_path):
    from app.gui import app_icon
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    (tmp_path / "not.png").write_text("hello")
    for bad in (tmp_path / "not.png", tmp_path / "missing.png", _picture(tmp_path / "tiny.png", 16, 16)):
        with pytest.raises(ValueError):
            app_icon.import_custom_icon(bad, store)
    assert not app_icon.has_custom_icon(store)


def test_settings_page_imports_a_picture_and_the_3d_icon_can_follow(qapp, tmp_path, monkeypatch):
    from app.gui import app_icon
    from app.localization import initialize_localization
    from app.settings.dialog import SettingsDialog
    from app.settings.store import SettingsStore

    localizer = initialize_localization(qapp)
    localizer.store = SettingsStore(tmp_path / "settings.json")
    dialog = SettingsDialog(localizer)
    store = localizer.store
    custom = dialog.app_icon_buttons.button(0)
    assert custom.text() == "+" and store.app_icon() == 5
    monkeypatch.setattr(dialog, "_import_app_icon", lambda: dialog._import_app_icon_from(str(_picture(tmp_path / "mine.png", 300, 300))))
    custom.click()                                              # nothing imported yet: opens the import
    assert store.app_icon() == 0 and app_icon.has_custom_icon(store) and custom.isChecked() and custom.text() == ""
    dialog.app_icon_buttons.button(3).click()
    custom.click()                                              # now just selects it again
    assert store.app_icon() == 0

    assert len(dialog.three_d_icon_buttons.buttons()) == 4 and store.three_d_icon() == 3
    custom3d = dialog.three_d_icon_buttons.button(0)
    assert custom3d.text() == "+" and not app_icon.has_custom_icon(store, True)
    monkeypatch.setattr(dialog, "_import_three_d_icon", lambda: dialog._import_three_d_icon_from(
        str(_picture(tmp_path / "mine3d.png", 300, 200))))
    custom3d.click()                                            # nothing imported yet: opens the import
    assert store.three_d_icon() == 0 and app_icon.has_custom_icon(store, True) and custom3d.text() == ""
    assert (tmp_path / "custom_3d_icon.png").is_file() and store.app_icon() == 0
    assert app_icon.current_three_d_icon(store).cacheKey() == app_icon.three_d_icon(0, store).cacheKey()
    assert app_icon.three_d_icon(0, store).cacheKey() != app_icon.app_icon(0, store).cacheKey()
    assert SettingsStore(tmp_path / "settings.json").three_d_icon() == 0
    dialog.three_d_icon_buttons.button(3).click()
    assert store.three_d_icon() == 3
    dialog.three_d_icon_buttons.button(2).click()
    assert store.three_d_icon() == 2 and not store.three_d_icon_sync()
    assert app_icon.current_three_d_icon(store).cacheKey() == app_icon.three_d_icon(2).cacheKey()
    dialog.three_d_icon_sync.setChecked(True)
    assert store.three_d_icon_sync() and not dialog.three_d_icon_buttons.button(1).isEnabled()
    assert app_icon.current_three_d_icon(store).cacheKey() == app_icon.app_icon(0, store).cacheKey()
    dialog.three_d_icon_sync.setChecked(False)
    assert app_icon.current_three_d_icon(store).cacheKey() == app_icon.three_d_icon(2).cacheKey()
    dialog.close()


def test_3d_process_uses_the_3d_icon_and_the_3d_window_class_too(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget

    from app.gui import app_icon
    from app.settings.store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    store.set_app_icon(2)
    monkeypatch.setattr(app_icon, "ROLE", "3d")
    app_icon.apply_app_icon(store)
    assert qapp.windowIcon().cacheKey() == app_icon.three_d_icon(3).cacheKey()
    monkeypatch.setattr(app_icon, "ROLE", "main")
    ThreeDAnalysisWindow = type("ThreeDAnalysisWindow", (QWidget,), {})
    other, three_d = QWidget(), ThreeDAnalysisWindow()
    app_icon.apply_app_icon(store)
    assert other.windowIcon().cacheKey() == app_icon.app_icon(2).cacheKey()
    assert three_d.windowIcon().cacheKey() == app_icon.three_d_icon(3).cacheKey()
    other.deleteLater(); three_d.deleteLater()
