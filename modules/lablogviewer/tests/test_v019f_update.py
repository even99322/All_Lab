"""v0.19F: first start after an update (records upgraded with a backup, What's New once)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _folder(tmp_path, records=True, marker=None) -> Path:
    root = tmp_path / "LabLogViewerData"
    (root / "state").mkdir(parents=True)
    if records:
        (root / "state" / "stars.json").write_text('{"schema_version": 1, "by_database": {}}')
    (root / "lablogviewer_data.json").write_text(json.dumps(marker or {"format": 1}))
    return root


def test_versions_compare_as_people_expect():
    from app.core.app_update import version_key

    assert version_key("0.19E") < version_key("0.19F") < version_key("0.20A") < version_key("1.0")
    assert version_key("0.9G") < version_key("0.19A")


def test_update_from_0_19e_shows_whats_new_once(tmp_path):
    from app.core.app_update import check

    root = _folder(tmp_path)                                   # 0.19E did not record its version
    first = check(root, "0.19F")
    assert first.show_whats_new and first.previous_version is None and not first.fresh
    marker = json.loads((root / "lablogviewer_data.json").read_text())
    assert marker["last_app_version"] == "0.19F" and marker["data_format"] == 1
    assert check(root, "0.19F").show_whats_new is False          # only once
    later = check(root, "0.20A")
    assert later.show_whats_new and later.previous_version == "0.19F"
    assert [v["version"] for v in json.loads((root / "lablogviewer_data.json").read_text())["versions_used"]] \
        == ["0.19F", "0.20A"]
    assert check(root, "0.19F").show_whats_new is False          # going back never shows "new"


def test_a_brand_new_installation_does_not_show_it(tmp_path):
    from app.core.app_update import check

    result = check(_folder(tmp_path, records=False), "0.19F")
    assert result.fresh and not result.show_whats_new


def test_upgrade_steps_run_in_order_with_a_backup(tmp_path):
    from app.core.app_update import check

    root = _folder(tmp_path, marker={"format": 1, "last_app_version": "0.19F", "data_format": 1})
    order = []

    def step_1(folder):
        order.append(1)
        (folder / "state" / "stars.json").write_text('{"schema_version": 2}')

    def step_2(folder):
        order.append(2)

    result = check(root, "0.20A", upgrades=[(2, 3, "second", step_2), (1, 2, "first", step_1)], formats=3)
    assert order == [1, 2] and len(result.upgraded) == 2
    assert json.loads((root / "lablogviewer_data.json").read_text())["data_format"] == 3
    assert json.loads((result.backup / "state" / "stars.json").read_text())["schema_version"] == 1
    assert check(root, "0.20A", upgrades=[(1, 2, "first", step_1)], formats=3).upgraded == []   # done once


def test_a_failed_upgrade_puts_everything_back(tmp_path):
    from app.core.app_update import check

    root = _folder(tmp_path, marker={"format": 1, "last_app_version": "0.19F", "data_format": 1})

    def breaks(folder):
        (folder / "state" / "stars.json").write_text("half written")
        raise RuntimeError("disk full")

    result = check(root, "0.20A", upgrades=[(1, 2, "breaks", breaks)], formats=2)
    assert result.error and "disk full" in result.error
    assert json.loads((root / "state" / "stars.json").read_text())["schema_version"] == 1
    assert json.loads((root / "lablogviewer_data.json").read_text()).get("data_format", 1) == 1


def test_older_program_on_newer_data_changes_nothing(tmp_path):
    from app.core.app_update import check

    marker = {"format": 1, "last_app_version": "0.21A", "data_format": 5}
    root = _folder(tmp_path, marker=marker)
    result = check(root, "0.19F")
    assert result.newer_data and not result.show_whats_new
    assert json.loads((root / "lablogviewer_data.json").read_text()) == marker


def test_whats_new_window_in_both_languages(tmp_path):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app import __version__
    from app.core.app_update import check
    from app.gui.whats_new import after_start
    from app.localization import initialize_localization

    localizer = initialize_localization(app)
    for language, needle in (("en", "refreshes itself"), ("zh_TW", "自動刷新")):
        localizer.set_language(language)
        dialog = after_start(check(_folder(tmp_path / language), __version__), localizer)
        assert dialog is not None and needle in dialog.body.toPlainText()
        dialog.close()
    localizer.set_language("en")
    root = _folder(tmp_path / "again")
    check(root, __version__)
    assert after_start(check(root, __version__), localizer) is None
