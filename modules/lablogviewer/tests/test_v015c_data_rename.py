from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.axis_preset_store import AxisPreset, AxisPresetStore, AxisRef
from app.core.comment_store import CommentStore
from app.core.data_identity import stable_data_identity
from app.core.data_rename import DataRenameError, rename_hdf5_data
from app.core.database_index_store import DatabaseIndexStore
from app.core.mark_store import MarkStore
from app.core.named_view_store import NamedViewStore
from app.core.overlay_store import OverlayStore, SavedOverlay
from app.core.session_store import SessionStore
from app.core.star_store import StarStore
from app.core.tag_store import TagStore
from app.core.viewer_display_state_store import ViewerDisplayStateStore
from tests.real_data import SMALL_FILE


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stores(root: Path):
    return {
        "star": StarStore(root / "stars.json"),
        "tag": TagStore(root / "tags.json", legacy_paths=[]),
        "comment": CommentStore(root / "comments.json"),
        "display": ViewerDisplayStateStore(root / "viewer_display_states.json"),
        "axis": AxisPresetStore(root / "axis_presets.json"),
        "overlay": OverlayStore(root / "overlays.json"),
        "mark": MarkStore(root / "marks.json"),
        "view": NamedViewStore(root / "named_views.json"),
        "session": SessionStore(root / "session.json"),
        "index": DatabaseIndexStore(root / "database_index.json"),
    }


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_rename_migrates_all_path_owned_state_and_preserves_file_bytes(tmp_path):
    database = tmp_path / "database"
    database.mkdir()
    source = database / "run_debg.hdf5"
    source.write_bytes(b"Labber bytes must not be opened or rewritten by Rename.\n")
    before = _digest(source)
    old_relative, new_relative = source.name, "renamed_debg.hdf5"
    old_identity = stable_data_identity(source)
    new_identity = stable_data_identity(database / new_relative)
    database_id = str(database.resolve())
    stores = _stores(tmp_path / "state")

    stores["star"].set_starred(database_id, old_relative, True)
    stores["tag"].set_tags(database_id, old_relative, {"BG"})
    stores["comment"].set_for_source(
        source, "external note", database_id=database_id, relative_path=old_relative
    )
    stores["display"].set(source, {"mode": "1d", "transform": "magnitude"})
    stores["axis"].save(AxisPreset(
        "Frequency", AxisRef("Frequency", "trace", "VNA - S21"),
        AxisRef("Current", "step", "Current"),
    ), old_identity)
    stores["overlay"].save(old_identity, SavedOverlay(
        "Overlay", [0], [0], 0,
    ))
    stores["mark"].save(old_identity, 1, ("1d", "Frequency"), {"marks": [1]})
    stores["view"].save(old_identity, "Magnitude", {"mode": "1d"})
    stores["session"].set({
        "database_path": database_id,
        "browser": {"selected_relative_path": old_relative},
        "viewers": [{"source_path": old_identity}],
    })
    stores["index"].replace_root(database_id, {
        old_relative: {"absolute_path": str(source), "relative_path": old_relative,
                      "file_name": source.name, "log_name": source.stem,
                      "status": "ok", "size_bytes": source.stat().st_size,
                      "mtime": source.stat().st_mtime, "metadata_complete": True},
    })

    destination = rename_hdf5_data(
        source,
        "renamed_debg",
        database_id=database_id,
        old_relative_path=old_relative,
        state_stores=tuple(stores.values()),
    )

    assert destination == database / new_relative
    assert not source.exists()
    assert _digest(destination) == before
    assert stores["star"].is_starred(database_id, new_relative)
    assert not stores["star"].is_starred(database_id, old_relative)
    assert stores["tag"].tags_for(database_id, new_relative) == {"BG"}
    assert stores["comment"].get_for_source(
        destination, database_id=database_id, relative_path=new_relative
    ) == "external note"
    assert stores["display"].get(destination)["transform"] == "magnitude"
    assert stores["axis"].list_all(new_identity)[0].name == "Frequency"
    assert stores["overlay"].list_all(new_identity)[0].name == "Overlay"
    assert stores["mark"].get(new_identity, 1, ("1d", "Frequency")) == {"marks": [1]}
    assert stores["view"].get(new_identity, "Magnitude") == {"mode": "1d"}
    session = stores["session"].get()
    assert session["browser"]["selected_relative_path"] == new_relative
    assert session["viewers"][0]["source_path"] == new_identity
    assert stores["index"].get(database_id, old_relative, destination.stat().st_size,
                                destination.stat().st_mtime) is None

    reloaded = _stores(tmp_path / "state")
    assert reloaded["star"].is_starred(database_id, new_relative)
    assert reloaded["tag"].tags_for(database_id, new_relative) == {"BG"}
    assert reloaded["comment"].get_for_source(
        destination, database_id=database_id, relative_path=new_relative
    ) == "external note"
    assert reloaded["view"].get(new_identity, "Magnitude") == {"mode": "1d"}


def test_destination_state_conflict_rolls_file_and_prior_store_moves_back(tmp_path):
    database = tmp_path / "database"
    database.mkdir()
    source = database / "source.h5"
    source.write_bytes(b"original")
    old_identity = stable_data_identity(source)
    new_identity = stable_data_identity(database / "target.h5")
    database_id = str(database.resolve())
    stores = _stores(tmp_path / "state")
    stores["star"].set_starred(database_id, source.name, True)
    stores["view"].save(new_identity, "Orphaned name", {"mode": "old target"})

    with pytest.raises(DataRenameError, match="Named Views already exist"):
        rename_hdf5_data(
            source, "target", database_id=database_id,
            old_relative_path=source.name, state_stores=tuple(stores.values()),
        )

    assert source.read_bytes() == b"original"
    assert not (database / "target.h5").exists()
    assert stores["star"].is_starred(database_id, source.name)
    assert not stores["star"].is_starred(database_id, "target.h5")
    assert stores["view"].get(new_identity, "Orphaned name") == {"mode": "old target"}
    assert stores["view"].get(old_identity, "Orphaned name") is None


def test_existing_filename_and_unsafe_name_are_rejected_without_mutation(tmp_path):
    database = tmp_path / "database"
    database.mkdir()
    source = database / "source.hdf5"
    source.write_bytes(b"source")
    (database / "taken.hdf5").write_bytes(b"occupied")
    before = _digest(source)
    database_id = str(database.resolve())

    with pytest.raises(DataRenameError, match="already exists"):
        rename_hdf5_data(source, "taken", database_id=database_id,
                         old_relative_path=source.name, state_stores=[])
    with pytest.raises(DataRenameError, match="valid filename"):
        rename_hdf5_data(source, "../escape", database_id=database_id,
                         old_relative_path=source.name, state_stores=[])
    with pytest.raises(DataRenameError, match="reserved by Windows"):
        rename_hdf5_data(source, "CON.log", database_id=database_id,
                         old_relative_path=source.name, state_stores=[])
    assert source.exists() and _digest(source) == before


@pytest.mark.skipif(not SMALL_FILE.is_file(), reason="Real Labber sample is unavailable.")
def test_safe_copy_of_real_labber_log_can_be_renamed_without_byte_changes(tmp_path):
    database = tmp_path / "copy database"
    database.mkdir()
    source = database / "copied.hdf5"
    shutil.copy2(SMALL_FILE, source)
    before = _digest(source)
    destination = rename_hdf5_data(
        source, "renamed", database_id=str(database.resolve()),
        old_relative_path=source.name, state_stores=[],
    )
    assert destination.name == "renamed.hdf5"
    assert _digest(destination) == before


@pytest.mark.skipif(not SMALL_FILE.is_file(), reason="Real Labber sample is unavailable.")
def test_browser_right_click_rename_dialog_cancel_refresh_and_open(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox
    from app.core.database_scanner import DatabaseScanner
    from app.gui.browser_window import BrowserWindow

    database = tmp_path / "database"
    database.mkdir()
    source = database / "copied.hdf5"
    shutil.copy2(SMALL_FILE, source)
    before = _digest(source)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    browser = BrowserWindow(star_store=StarStore(state_dir / "stars.json"))
    browser.scanner_root = str(database.resolve())
    result = DatabaseScanner.scan(database, index_store=browser.database_index_store)
    browser._on_scan_finished(result)
    entry = result.entries[0]
    browser.star_store.set_starred(result.database_id, entry.relative_path, True)
    browser.tag_store.set_tags(result.database_id, entry.relative_path, {"BG"})
    item = browser.data_list.topLevelItem(0)
    blocker = browser.data_list.blockSignals(True)
    browser.data_list.setCurrentItem(item)
    browser.data_list.blockSignals(blocker)
    browser.show()
    qapp.processEvents()

    def right_click_rename(answer: str | None) -> None:
        errors: list[str] = []
        dialog_tries = 0
        menu_tries = 0

        def accept_dialog() -> None:
            nonlocal dialog_tries
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QInputDialog):
                dialog = next((widget for widget in QApplication.topLevelWidgets()
                               if isinstance(widget, QInputDialog) and widget.isVisible()), None)
            if not isinstance(dialog, QInputDialog):
                if isinstance(dialog, QMessageBox):
                    errors.append(f"Rename unexpectedly showed a message: {dialog.text()}")
                    dialog.accept()
                    return
                dialog_tries += 1
                if dialog_tries < 100:
                    QTimer.singleShot(20, accept_dialog)
                else:
                    errors.append("Rename dialog did not open after selecting the context action.")
                return
            assert dialog.textValue() == "copied"
            if answer is None:
                dialog.reject()
            else:
                dialog.setTextValue(answer)
                dialog.accept()

        def select_action() -> None:
            nonlocal menu_tries
            menu = QApplication.activePopupWidget()
            if not isinstance(menu, QMenu):
                menu_tries += 1
                if menu_tries < 100:
                    QTimer.singleShot(10, select_action)
                else:
                    errors.append("Browser context menu did not open after right-click.")
                return
            action = next((candidate for candidate in menu.actions()
                           if candidate.text() == "Rename File..."), None)
            if action is None:
                errors.append("Rename File action was absent from the right-click menu.")
                menu.close()
                return
            QTimer.singleShot(50, accept_dialog)
            action.trigger()
            menu.close()

        rect = browser.data_list.visualItemRect(item)
        assert rect.isValid()
        QTimer.singleShot(0, select_action)
        viewport = browser.data_list.viewport()
        QTest.mouseClick(viewport, Qt.RightButton, pos=rect.center())
        global_position = viewport.mapToGlobal(rect.center())
        QApplication.sendEvent(
            viewport,
            QContextMenuEvent(QContextMenuEvent.Mouse, rect.center(), global_position),
        )
        assert not errors

    right_click_rename(None)
    assert source.exists() and not (database / "renamed_debg.hdf5").exists()

    right_click_rename("renamed_debg")
    destination = database / "renamed_debg.hdf5"
    assert destination.is_file()
    assert browser._scan_worker is not None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        qapp.processEvents()
        worker = browser._scan_worker
        if worker is not None and not worker.isRunning() and browser.scan_result is not result:
            break
        QTest.qWait(10)
    assert destination.is_file()
    assert not source.exists()
    assert _digest(destination) == before
    assert browser.scan_result is not result
    selected_item, selected_entry = browser._selected_data()
    assert selected_item is not None and selected_entry.file_name == destination.name
    assert browser.star_store.is_starred(result.database_id, destination.name)
    assert browser.tag_store.tags_for(result.database_id, destination.name) == {"BG"}

    viewer = browser._open_viewer_for(selected_entry)
    assert viewer is not None and viewer.experiment.data_identity == stable_data_identity(destination)
    blocked_messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: blocked_messages.append(args))
    assert not browser._rename_entry(selected_entry, "must_not_rename_open_data")
    assert blocked_messages and destination.exists()
    viewer.close()
    browser.close()
    qapp.processEvents()
