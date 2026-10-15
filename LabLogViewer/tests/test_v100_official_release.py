"""1.0.0 正式版：版本號重新開始。舊版號（2.x）用過的資料夾算升級；沒有 "line" 的舊 feed 不會把程式「更新」回 2.x。"""

from __future__ import annotations

import json


def test_version_is_1_0_0_and_notes_exist():
    from app import __version__
    from app.core.app_update import whats_new_text

    assert __version__ == "1.0.7"
    assert "正式發佈版" in whats_new_text("1.0.0", "zh")
    assert "2.1.6" not in whats_new_text("1.0.0", "en", since=None).split("\n")[0]


def test_folder_used_by_2x_counts_as_an_upgrade(tmp_path):
    from app.core.app_update import check

    (tmp_path / "lablogviewer_data.json").write_text(json.dumps({"last_app_version": "2.1.6", "data_format": 1}))
    result = check(tmp_path, "1.0.0", had_data=True)
    assert result.show_whats_new and result.from_legacy and result.previous_version == "2.1.6"
    assert json.loads((tmp_path / "lablogviewer_data.json").read_text())["version_line"] == 1
    again = check(tmp_path, "1.0.0", had_data=True)
    assert not again.show_whats_new
    later = check(tmp_path, "1.0.1", had_data=True)
    assert later.show_whats_new and not later.from_legacy
    assert not check(tmp_path, "1.0.0", had_data=True).show_whats_new      # 新版號裡往回：不算新


def test_old_feeds_do_not_offer_2x(tmp_path):
    from app.core import hub_update
    from app.core.update_feed import UpdateInfo, _parse_feed, newer_info

    old = _parse_feed({"version": "2.1.6", "items": []})
    new = _parse_feed({"version": "1.0.1", "items": [], "line": 1})
    assert old.line == 0 and new.line == 1
    assert newer_info(old, "1.0.0") is None and not hub_update.newer(old, "1.0.0")
    assert newer_info(new, "1.0.0") is new and hub_update.newer(new, "1.0.0")
    assert newer_info(UpdateInfo("2.0.1"), "2.0.0") is not None              # 舊版號的程式之間照舊
