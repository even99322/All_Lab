"""v0.20: notification bell, update feed (local folders only), pet notice bubble, no hidden-pet hint."""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QLabel

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def localizer(qapp):
    from app.localization import initialize_localization

    manager = initialize_localization(qapp)
    manager.set_language("en")
    return manager


ITEMS = [
    {"category": "new", "en": "Colour range spin boxes", "zh": "色階範圍可手動輸入"},
    {"category": "new", "en": "3D window icon", "zh": "3D 視窗圖示"},
    {"category": "improved", "en": "Larger icons", "zh": "圖示加大"},
    {"category": "improved", "en": "Halved 3D threads", "zh": "3D 執行緒減半"},
    {"category": "fixed", "en": "Settings height", "zh": "設定視窗高度"},
]


def _feed(folder, version="2.0.1", items=ITEMS, package=None, sha=None):
    folder.mkdir(parents=True, exist_ok=True)
    data = {"version": version, "date": "2026-10-20", "items": items}
    if package:
        data["package"] = package
    if sha:
        data["sha256"] = sha
    (folder / "latest.json").write_text(json.dumps(data), encoding="utf-8")


# ---- the notice store ---------------------------------------------------------------------------
def test_notices_are_kept_newest_first_and_capped():
    from app.core import notifications as n

    for i in range(60):
        n.add("measurement", {"name": f"m{i}", "points": 5})
    items = n.items()
    assert len(items) == n.MAX_ITEMS and items[0]["params"]["name"] == "m59"
    assert n.unread_count() == 50
    n.mark_read(items[0]["id"])
    assert n.unread_count() == 49
    n.mark_all_read()
    assert n.unread_count() == 0
    n.remove(items[1]["id"])
    assert len(n.items()) == 49
    n.clear()
    assert n.items() == []


def test_kinds_can_be_switched_off_and_everything_muted():
    from app.core import notifications as n

    n.set_kind_enabled("measurement", False)
    assert n.add("measurement", {"name": "x"}) is None
    assert n.add("streak", {"days": 7}) is not None
    n.set_muted(True)
    assert n.add("streak", {"days": 14}) is None and len(n.items()) == 1
    n.set_muted(False)
    n.set_kind_enabled("measurement", True)
    assert n.measurement_finished("x", 10) is not None


def test_same_key_is_announced_once_and_a_broken_file_is_ignored():
    from app.core import notifications as n

    assert n.add("update", {"version": "2.0.1"}, key="update:2.0.1")
    assert n.add("update", {"version": "2.0.1"}, key="update:2.0.1") is None
    n.store_path().write_text("{not json", encoding="utf-8")
    assert n.items() == [] and n.unread_count() == 0
    assert n.add("streak", {"days": 7}) is not None


def test_streak_only_at_milestones():
    from app.core import notifications as n

    assert n.streak_reached(2) is None and n.streak_reached(8) is None
    assert n.streak_reached(7)["params"]["days"] == 7
    assert n.streak_reached(401) is None and n.streak_reached(500)


def test_text_follows_the_language_and_updates_show_three_lines_then_more(localizer):
    from app.core import notifications as n
    from app.core.update_feed import UpdateInfo

    record = n.add("update", {"version": "2.0.1", "n": 5}, details=UpdateInfo("2.0.1", "d", items=ITEMS).details())
    shown = n.describe(record, localizer.text, "en")
    assert shown["title"] == "New version available"
    assert shown["body"] == "LabLogViewer 2.0.1 is available — 5 changes"
    assert shown["lines"] == ["Colour range spin boxes", "3D window icon", "Larger icons"] and shown["more"]
    from app.localization.catalogs import ZH_TW

    zh = n.describe(record, lambda k: ZH_TW[k], "zh")
    assert zh["title"] == "有新版本" and zh["lines"][0] == "色階範圍可手動輸入"
    few = n.add("update", {"version": "2.0.2"}, details=UpdateInfo("2.0.2", "", items=ITEMS[:2]).details())
    assert not n.describe(few, localizer.text, "en")["more"]
    plain = n.add("update", {"version": "2.0.3"})
    assert n.describe(plain, localizer.text, "en")["body"] == "LabLogViewer 2.0.3 is available"
    m = n.measurement_finished("Flux 03", 681)
    assert n.describe(m, localizer.text, "en")["body"] == "“Flux 03” finished — 681 points"


# ---- the update feed (local folders only) -----------------------------------------------------------
def test_feed_reports_only_newer_unskipped_versions(tmp_path):
    from app.core.update_feed import newer_update

    folder = tmp_path / "feed"
    assert newer_update(folder, "2.0.0") is None                       # no feed
    _feed(folder, "2.0.1")
    info = newer_update(folder, "2.0.0")
    assert info.version == "2.0.1" and len(info.items) == 5
    assert newer_update(folder, "2.0.1") is None and newer_update(folder, "2.0.2") is None
    assert newer_update(folder, "2.0.0", skipped=["2.0.1"]) is None
    _feed(folder, "2.0.1", items=[])
    assert newer_update(folder, "2.0.0").items == []                   # version only


def test_damaged_feeds_are_ignored(tmp_path):
    from app.core.update_feed import read_feed

    folder = tmp_path / "feed"
    folder.mkdir()
    for text in ("{broken", "[]", json.dumps({"version": 5}), json.dumps({"version": "../x"})):
        (folder / "latest.json").write_text(text)
        assert read_feed(folder) is None
    (folder / "latest.json").write_text(json.dumps({"version": "2.0.1", "items": [1, {"en": ""}, {"en": "ok"}]}))
    assert [i["en"] for i in read_feed(folder).items] == ["ok"]


def test_web_addresses_and_network_shares_are_refused(tmp_path):
    from app.core.update_feed import FeedError, check_configured, is_local_folder, read_feed
    from app.core import notifications

    for bad in ("https://example.com/feed", "ftp://x/y", "\\\\server\\share", "//server/share", ""):
        assert not is_local_folder(bad)
    with pytest.raises(FeedError):
        read_feed("https://example.com/feed")
    notifications.set_update_folder("\\\\server\\share")
    assert check_configured("2.0.0") is None and notifications.items() == []


def test_fetch_copies_after_checking_the_hash_and_never_installs(tmp_path):
    from app.core.update_feed import FeedError, fetch_package, read_feed

    folder = tmp_path / "feed"
    payload = b"package bytes"
    (folder).mkdir()
    (folder / "pkg.zip").write_bytes(payload)
    _feed(folder, package="pkg.zip", sha=hashlib.sha256(payload).hexdigest())
    target = fetch_package(read_feed(folder), folder, tmp_path / "got")
    assert target.read_bytes() == payload and not list((tmp_path / "got").glob("*.part"))
    (folder / "pkg.zip").write_bytes(b"tampered")
    with pytest.raises(FeedError, match="damaged"):
        fetch_package(read_feed(folder), folder, tmp_path / "got2")
    assert not (tmp_path / "got2" / "pkg.zip").exists()
    (folder / "pkg.zip").unlink()
    with pytest.raises(FeedError, match="missing"):
        fetch_package(read_feed(folder), folder, tmp_path / "got3")
    _feed(folder, package="../escape.zip")
    with pytest.raises(FeedError):
        fetch_package(read_feed(folder), folder, tmp_path / "got4")
    _feed(folder)
    with pytest.raises(FeedError, match="no package"):
        fetch_package(read_feed(folder), folder, tmp_path / "got5")


def test_start_up_check_adds_one_notice_per_version(tmp_path):
    from app.core import notifications
    from app.core.update_feed import check_configured

    assert check_configured("2.0.0") is None                           # nothing configured: no check
    folder = tmp_path / "feed"
    _feed(folder, "2.0.1")
    notifications.set_update_folder(str(folder))
    first = check_configured("2.0.0")
    assert first["kind"] == "update" and first["params"] == {"version": "2.0.1", "n": 5}
    assert check_configured("2.0.0") is None and len(notifications.items()) == 1
    _feed(folder, "2.0.2")
    assert check_configured("2.0.0")["params"]["version"] == "2.0.2"


# ---- the bell ----------------------------------------------------------------------------------
def test_bell_counts_unread_and_lists_notices(qapp, localizer):
    from app.core import notifications as n
    from app.gui.notification_bell import BellButton

    bell = BellButton(localizer, 24)
    assert bell.unread() == 0 and bell.toolTip() == "Notifications"
    n.measurement_finished("Flux 03", 681)
    n.streak_reached(7)
    qapp.processEvents()
    assert bell.unread() == 2 and bell.toolTip() == "Notifications (2 unread)"
    panel = bell.show_panel()
    assert len(panel.rows) == 2 and panel.mark_button.isEnabled()
    panel.rows[0].activated.emit(panel.rows[0].record_id)
    assert bell.unread() == 1
    panel._mark_all()
    assert bell.unread() == 0 and not panel.mark_button.isEnabled()
    panel._clear()
    assert panel.rows == [] and panel.findChildren(QLabel, "noticeEmpty")
    panel.close()
    bell.deleteLater()


def test_update_row_offers_more_and_the_preview_lists_everything_grouped(qapp, localizer):
    from app.core import notifications as n
    from app.core.update_feed import UpdateInfo
    from app.gui.notification_bell import BellButton

    n.add("update", {"version": "2.0.1", "n": 5}, details=UpdateInfo("2.0.1", "2026-10-20", items=ITEMS).details())
    bell = BellButton(localizer, 24)
    panel = bell.show_panel()
    row = panel.rows[0]
    assert row.more_button is not None and row.more_button.text() == "… more"
    row.more_button.click()
    dialog = panel.preview
    html = dialog.body.toPlainText()
    for item in ITEMS:
        assert item["en"] in html                                          # the full list, not just three
    assert "New features" in html and "Improvements" in html and "Fixes" in html
    assert any("Released 2026-10-20" in label.text() for label in dialog.findChildren(QLabel))
    assert n.unread_count() == 0                                           # opening the preview reads it
    dialog.close()
    panel.close()


def test_skip_this_version_removes_the_notice_and_it_does_not_return(qapp, localizer, tmp_path):
    from app.core import notifications as n
    from app.core.update_feed import check_configured
    from app.gui.notification_bell import UpdatePreviewDialog

    folder = tmp_path / "feed"
    _feed(folder, "2.0.1")
    n.set_update_folder(str(folder))
    record = check_configured("2.0.0")
    dialog = UpdatePreviewDialog(localizer, record)
    dialog.skip_button.click()
    assert n.items() == [] and "2.0.1" in n.skipped_versions()
    assert check_configured("2.0.0") is None
    _feed(folder, "2.0.2")
    assert check_configured("2.0.0") is not None                         # only that version is skipped


def test_settings_menu_switches_kinds_and_mutes(qapp, localizer):
    from app.core import notifications as n
    from app.gui.notification_bell import BellButton

    bell = BellButton(localizer, 24)
    panel = bell.show_panel()
    assert [a.text() for a in panel.kind_actions.values()] == [
        "Software updates", "Measurement finished", "Days opened in a row", "Pet level-ups"]
    panel.kind_actions["measurement"].setChecked(False)
    assert n.add("measurement", {"name": "x"}) is None
    panel.mute_action.setChecked(True)
    assert n.add("update", {"version": "9"}) is None
    panel.refresh()
    assert not panel.kind_actions["update"].isEnabled()
    panel.close()


# ---- the pet bubble ------------------------------------------------------------------------------
def _pet_manager(qapp):
    from app.pet import registry
    from app.pet.character import Character
    from app.pet.manager import PetManager
    from app.pet.pet_window import PetWindow

    manager = PetManager()
    info = next(p for p in registry.PETS if not p.hidden)
    manager.window = PetWindow(Character(info.pack, 1))
    manager.window.move(500, 500)
    manager.window.show()
    return manager


def test_notice_without_a_pet_is_only_in_the_bell(qapp):
    from app.pet.manager import PetManager

    assert PetManager().notice("title", "body") is False


def test_bubble_shows_above_the_pet_queues_and_clicks_through(qapp):
    manager = _pet_manager(qapp)
    clicked = []
    assert manager.notice("New version available", "LabLogViewer 2.0.1 is available", on_click=lambda: clicked.append(1))
    bubble = manager._bubble
    assert bubble.isVisible() and bubble.title == "New version available"
    assert bubble.geometry().bottom() <= manager.window.frameGeometry().top() + bubble._head   # above the drawn head, close to it
    assert bubble.geometry().bottom() >= manager.window.frameGeometry().top() + bubble._head - 10
    manager.notice("Measurement finished", "“x” finished")
    assert [n[0] for n in manager._notices] == ["Measurement finished"]                  # waits its turn
    bubble.dismiss()
    assert manager._bubble.title == "Measurement finished" and manager._bubble.isVisible()
    manager._bubble.dismiss()
    assert not manager._bubble.isVisible() and manager._notices == []
    manager.notice("Again", "text", on_click=lambda: clicked.append(2))
    manager._bubble.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(5, 5), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    assert clicked == [2] and not manager._bubble.isVisible()
    manager._close_window()
    manager.window = None


def test_bubble_fades_out_after_six_seconds(qapp):
    from app.pet import notice_bubble

    manager = _pet_manager(qapp)
    manager.notice("t", "b")
    bubble = manager._bubble
    assert notice_bubble.SHOW_SECONDS == 6.0
    bubble._left = 0.3
    bubble._tick()
    assert 0.0 < bubble.windowOpacity() < 1.0
    bubble._left = 0.01
    bubble._tick()
    assert not bubble.isVisible()
    manager._close_window()


def test_bell_hands_a_new_notice_to_the_pet(qapp, localizer, monkeypatch):
    from app.core import notifications as n
    from app.gui.notification_bell import BellButton
    from app.pet import manager as pet_manager

    manager = _pet_manager(qapp)
    monkeypatch.setattr(pet_manager, "_MANAGER", manager)
    bell = BellButton(localizer, 24)
    n.measurement_finished("Flux 03", 681)
    qapp.processEvents()
    assert manager._bubble.title == "Measurement finished"
    assert manager._bubble.body == "“Flux 03” finished — 681 points"
    manager._close_window()


# ---- placement and the pet page --------------------------------------------------------------------
def test_browser_window_has_the_bell_before_the_annotation_button(qapp, tmp_path):
    from app.core.star_store import StarStore
    from app.gui.browser_window import BrowserWindow

    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    bell = win.notification_bell
    assert bell.objectName() == "notificationBell"
    row = bell.parentWidget().layout()
    indexes = [row.indexOf(w) for w in (bell, win.annotation_button)]
    assert 0 <= indexes[0] < indexes[1]
    win.close()


def test_pet_page_does_not_hint_at_hidden_pets(qapp, localizer, monkeypatch, tmp_path):
    from app.gui.pet_page import PetPage
    from app.pet import registry, store

    monkeypatch.setattr(store, "store_path", lambda: tmp_path / "pet.json")
    page = PetPage(localizer)
    assert not hasattr(page, "hidden_group")
    texts = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "idden" not in texts and "licence" not in texts.lower()
    assert {card.info.id for card in page.cards} == {p.id for p in registry.PETS if not p.hidden}
    hidden = next(p for p in registry.PETS if p.hidden)
    monkeypatch.setattr(registry, "hidden_unlocked", lambda info: info.id == hidden.id or not info.hidden)
    page.refresh()
    assert hidden.id in {card.info.id for card in page.cards}            # appears only once unlocked
    from app.localization.catalogs import EN, ZH_TW

    for catalog in (EN, ZH_TW):
        assert not any(key in catalog for key in ("pet.hidden", "pet.hidden_hint", "pet.hidden_none"))


def test_damaged_update_details_do_not_break_the_notice_text():
    from app.core import notifications

    for details in (5, "x", {"items": 3}, {"items": [1, None, {"en": 5}, {"en": "ok"}]}):
        record = {"kind": "update", "params": {"version": "2.1"}, "details": details}
        shown = notifications.describe(record, lambda key: key, "en")
        assert shown["lines"] in ([], ["ok"])


# ---- the GitHub feed (the only web address that is allowed) ----------------------------------------
FEED_TEXT = json.dumps({"version": "2.0.1", "date": "2026-10-10", "items": [
    {"category": "fixed", "en": "Star fix", "zh": "星號修正"}]})


def test_only_the_github_feed_address_is_allowed():
    from app.core.update_feed import DEFAULT_FEED_URL, is_feed_url

    assert is_feed_url(DEFAULT_FEED_URL)
    for bad in ("http://raw.githubusercontent.com/a/b/main/latest.json", "https://example.com/latest.json",
                "https://raw.githubusercontent.com.evil.com/x.json", "https://user@raw.githubusercontent.com/x.json",
                "\\\\nas\\share\\feed", "//nas/share", "ftp://raw.githubusercontent.com/x", ""):
        assert not is_feed_url(bad), bad


def test_read_feed_url_parses_and_refuses_other_addresses(monkeypatch):
    import io
    import urllib.request

    from app.core import update_feed as uf

    seen = []

    class Opener:
        def open(self, request, timeout=None):
            seen.append((request.full_url, timeout))
            return io.BytesIO(FEED_TEXT.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    info = uf.read_feed_url(uf.DEFAULT_FEED_URL)
    assert info.version == "2.0.1" and info.items[0]["zh"] == "星號修正" and seen[0][0] == uf.DEFAULT_FEED_URL
    with pytest.raises(uf.FeedError):
        uf.read_feed_url("https://example.com/latest.json")
    assert len(seen) == 1                                                      # nothing was requested
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *h: type("O", (), {"open": lambda self, r, timeout=None: (_ for _ in ()).throw(OSError("offline"))})())
    assert uf.read_feed_url(uf.DEFAULT_FEED_URL) is None                       # offline: quietly nothing


def test_startup_check_reads_the_github_feed_and_tells_bell_and_pet(qapp, localizer, tmp_path, monkeypatch):
    from app.core import notifications as n
    from app.core import update_feed as uf
    from app.core.star_store import StarStore
    from app.core.update_feed import UpdateInfo
    from app.gui.browser_window import BrowserWindow
    from app.pet import manager as pet_manager

    manager = _pet_manager(qapp)
    monkeypatch.setattr(pet_manager, "_MANAGER", manager)
    monkeypatch.delenv("LABLOGVIEWER_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(uf, "read_feed_url", lambda url, timeout=6.0: uf._parse_feed(json.loads(FEED_TEXT)))
    monkeypatch.setattr("app.gui.browser_window.__version__", "2.0.0")    # the feed offers 2.0.1 to a 2.0.0 app
    win = BrowserWindow(star_store=StarStore(tmp_path / "stars.json"))
    import time
    win._check_for_update()
    t = time.time()
    while not n.items() and time.time() - t < 5:
        qapp.processEvents(); time.sleep(0.01)
    assert [i["kind"] for i in n.items()] == ["update"] and n.items()[0]["params"]["version"] == "2.0.1"
    assert win.notification_bell.unread() == 1
    assert manager._bubble.title                                               # the pet announces it too
    manager._close_window()
    win.close()
