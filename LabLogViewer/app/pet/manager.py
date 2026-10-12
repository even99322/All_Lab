"""Owns the one pet window: starts it with LabLogViewer, applies Settings > Pet, closes on quit.

Public API for the rest of the app (all safe to call when no pet is shown):
  award(kind, key, per_day=False)   research EXP for the current pet (see progress.WEIGHTS)
  notify(event)                     play a shared pet event, e.g. "alert" for an essential warning
Essential alerts reach the pet at every level and even while it sleeps; uncaught errors raise one.
"""

from __future__ import annotations

import os
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from app.pet import actions, journal, progress, registry, store

_MANAGER = None


class PetManager(QObject):
    _alert = Signal(str)               # marshals notifications from any thread to the GUI thread
    _award = Signal(str, str, bool)
    changed = Signal()
    level_up = Signal(str, int)

    def __init__(self, localizer=None, open_settings=None):
        super().__init__()
        self.localizer = localizer
        self.open_settings = open_settings
        self.window = None
        self.panel = None
        self.tracker = actions.ActiveViewerTracker()
        self._bubble = None
        self._notices = []
        self._alert.connect(self._deliver)
        self._award.connect(self._do_award)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    # -- text ----------------------------------------------------------------------------------
    def text(self, key: str, default: str = "") -> str:
        if self.localizer is None:
            return default
        value = self.localizer.text(key)
        return default if value == key else value

    # -- lifecycle -------------------------------------------------------------------------------
    def apply(self) -> None:
        """Show / swap / hide the pet to match the saved Settings > Pet choice."""
        state = store.load()
        info = registry.get(state["current"])
        if not state["enabled"] or info is None or not registry.hidden_unlocked(info):
            self._close_window()
            self.changed.emit()
            return
        level = store.look_of(state, info.id)         # chosen look, never above the level reached
        if self.window is not None and self.window.char.id == info.id and self.window.char.level == level:
            self._refresh_bar(state)
            self.window.set_float_on_top(state["float_on_top"])
            self.window.show()
            return
        pos = self.window.pos() if self.window is not None else None
        self._close_window()
        from app.pet.character import Character
        from app.pet.pet_window import PetWindow

        try:
            char = Character(info.pack, level)
        except (OSError, ValueError, KeyError):
            self._offer_pictures()
            return                                   # assets missing: never let the pet break the app
        developer = os.environ.get("LLV_PET_DEV") == "1"
        self.window = PetWindow(char, text=self.text, developer=developer,
                                loader=(lambda lv: Character(info.pack, lv)) if developer else None)
        self.window.settings_requested.connect(self._open_settings)
        self.window.bar_clicked.connect(self._bar_clicked)
        self.window.activate_requested.connect(self.raise_app)
        self.window.set_float_on_top(state["float_on_top"])
        self.window.dropped.connect(self.receive_drop)
        self._refresh_bar(state)
        self.window.hide_requested.connect(lambda: (store.set_enabled(False), self.apply()))
        if pos is not None:
            self.window.move(pos)
        else:
            self.window.place_bottom_right()
        self.window.show()
        self.changed.emit()

    def _offer_pictures(self) -> None:
        """v2.1.5: pet pictures are an optional download; offer it once per run when they are missing."""
        from app.pet import pack

        if getattr(self, "_pictures_offered", False) or pack.available() or os.environ.get("LLV_NO_PET_OFFER"):
            return
        self._pictures_offered = True

        def ask():
            from app.gui import pet_download

            parent = self.tracker.browser() or self.tracker.viewer()
            if pet_download.offer(parent, lambda key: self.text(key, key)):
                self.apply()
        QTimer.singleShot(2500, ask)

    def raise_app(self) -> None:
        """Bring LabLogViewer to the front (the Viewer used last, else the Browser)."""
        window = self.tracker.viewer() or self.tracker.browser()
        if window is None:
            return
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    # -- hover bar / panel / quick actions ------------------------------------------------------
    def _refresh_bar(self, state=None) -> None:
        if self.window is None:
            return
        state = state or store.load()
        _bag, slots = progress.capacity(store.level_of(state, state["current"]))
        items = [("panel", "▦", self.text("pet.bar.panel", "Pet panel"))]
        for aid in state["quick_actions"][:slots][:4]:
            action = actions.get(aid)
            if action is not None:
                items.append((action.id, action.glyph, self.text(action.label_key, action.id)))
        self.window.set_bar_items(items)

    def _bar_clicked(self, item: str) -> None:
        if item == "panel":
            self.open_panel()
        else:
            self.run_action(item)

    def open_panel(self) -> None:
        if self.window is None:
            return
        from app.pet.panel import CompanionPanel

        if self.panel is None:
            self.panel = CompanionPanel(self.text, self.tracker)
            self.panel.action_requested.connect(self.run_action)
            self.panel.changed.connect(lambda: self._refresh_bar())
        self.panel.popup_near(self.window)

    def receive_drop(self, mime) -> None:
        """Items dragged onto the pet go into the backpack (drag-to-pet unlocks at Lv.10)."""
        from app.pet import backpack

        state = store.load()
        level = store.level_of(state, state["current"]) if state["current"] else 1
        if level < 10:
            self.window.say(self.text("pet.drop.locked", "Drag-to-pet unlocks at Lv.10."))
            return
        cap = progress.capacity(level)[0]
        added = 0
        try:
            if mime.hasUrls() and any(u.isLocalFile() for u in mime.urls()):
                for url in mime.urls():
                    if url.isLocalFile():
                        backpack.add_path(url.toLocalFile(), cap); added += 1
            elif mime.hasImage():
                backpack.add_image(mime.imageData(), self.text("pet.drop.image", "Picture"), cap); added += 1
            elif mime.hasText():
                text = mime.text()
                backpack.add_text(text, text.strip().splitlines()[0][:60] if text.strip() else "Text", cap); added += 1
        except backpack.BackpackFull:
            self.window.say(self.text("pet.drop.full", "The backpack is full."))
        except OSError:
            self.window.say(self.text("pet.drop.failed", "Could not keep that item."))
        if added:
            self.window.trigger("receive")
            if self.panel is not None and self.panel.isVisible():
                self.panel.refresh()

    def run_action(self, action_id: str) -> None:
        reason = actions.run(action_id, self.tracker)
        if reason and self.window is not None:
            self.window.say(self.text(reason, reason))

    # -- research EXP ----------------------------------------------------------------------------
    def award(self, kind: str, key: str, per_day: bool = False) -> None:
        """Thread-safe: grant research EXP to the current pet (duplicates pay nothing)."""
        self._award.emit(kind, str(key or ""), per_day)

    def apply_potions(self) -> list[str]:
        """Use growth potions from installed licences once (DevTools items); returns the features used."""
        from app._guard import gate

        state = store.load()
        before = store.level_of(state, state["current"]) if state["current"] else 0
        used = store.apply_potions(gate.pet_potions())
        if used:
            journal.add_note("info", self.text("pet.note.potion", "Growth potion used"))
            self.apply()
            state = store.load()
            if state["current"] and store.level_of(state, state["current"]) > before and self.window is not None:
                self.window.trigger("levelup")
            self._refresh_bar()
        return used

    def note_daily_open(self) -> None:
        """Opening the program on a new day: update the streak and give the small daily EXP."""
        state = store.load()
        if state["current"] is None:
            return
        if progress.note_open_day(state):
            store.save(state)
            self._do_award("daily", "open", True)
            try:
                from app.core import notifications

                notifications.streak_reached(int(state.get("streak", 0)))
            except Exception:
                pass

    def _do_award(self, kind: str, key: str, per_day: bool) -> None:
        if kind != "daily":
            try:
                journal.record_research(kind, key)
                if kind in ("analysis", "export", "debackground"):
                    name = key.split("|", 1)[-1].replace("\\", "/").rsplit("/", 1)[-1]
                    journal.add_note("success", self.text(f"pet.note.{kind}", kind).format(name=name))
            except Exception:
                pass
        state = store.load()
        pet = state["current"]
        if pet is None:
            return
        before = store.level_of(state, pet)
        if progress.award(state, kind, key, per_day=per_day) <= 0:
            return
        store.save(state)
        after = store.level_of(state, pet)
        if after > before:
            self.level_up.emit(pet, after)
            try:
                from app.core import notifications

                notifications.pet_level_up(after)
            except Exception:
                pass
            journal.add_note("info", self.text("pet.note.levelup", "Lv.{level}").format(level=after))
            if progress.milestone(after) != progress.milestone(before):
                self.apply()                         # new look; the swap plays the level-up clip
                if self.window is not None:
                    self.window.trigger("levelup")
            elif self.window is not None:
                self.window.trigger("levelup")
            self._refresh_bar(state)
        if self.panel is not None and self.panel.isVisible():
            self.panel.refresh()

    def notice(self, title: str, body: str, on_click=None) -> bool:
        """Show a notification bubble above the pet (queued when one is already showing).

        Nothing is shown without a pet; the notice is still in the bell.
        """
        if self.window is None:
            return False
        self._notices.append((title, body, on_click))
        if self._bubble is None or not self._bubble.isVisible():
            self._next_notice()
        return True

    def _next_notice(self) -> None:
        if not self._notices or self.window is None:
            self._notices.clear()
            return
        from app.pet.notice_bubble import NoticeBubble

        title, body, on_click = self._notices.pop(0)
        if self._bubble is None or self._bubble.pet is not self.window:
            if self._bubble is not None:
                self._bubble.deleteLater()
            self._bubble = NoticeBubble(self.window)
            self._bubble.finished.connect(self._next_notice)
        self._bubble.callback = on_click
        self._bubble.show_notice(title, body)

    def _close_window(self) -> None:
        self._notices.clear()
        if self._bubble is not None:
            self._bubble.dismiss()
            self._bubble.deleteLater()
            self._bubble = None
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
            self.window = None

    def shutdown(self) -> None:
        self._close_window()

    def _open_settings(self) -> None:
        if self.open_settings is not None:
            self.open_settings()

    # -- notifications ---------------------------------------------------------------------------
    def notify(self, event: str) -> None:
        """Thread-safe: play a shared pet event (e.g. "alert") if a pet is shown."""
        self._alert.emit(event)

    def _deliver(self, event: str) -> None:
        if event == "alert":
            try:
                journal.add_note("error", self.text("pet.note.alert", "Unexpected error"))
            except Exception:
                pass
        if self.window is not None:
            self.window.trigger(event)

    def install_error_alerts(self) -> None:
        """Uncaught errors (any thread) make the pet raise an essential alert. Chains existing hooks."""
        previous = sys.excepthook

        def hook(exc_type, exc, tb):
            try:
                self.notify("alert")
            except Exception:
                pass
            previous(exc_type, exc, tb)

        sys.excepthook = hook
        if hasattr(threading, "excepthook"):
            previous_thread = threading.excepthook

            def thread_hook(args):
                try:
                    self.notify("alert")
                except Exception:
                    pass
                previous_thread(args)

            threading.excepthook = thread_hook


def manager() -> PetManager | None:
    return _MANAGER


def start(localizer=None, open_settings=None) -> PetManager:
    """Create the manager once and show the saved pet shortly after the main window."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = PetManager(localizer, open_settings)
        _MANAGER.install_error_alerts()
        QTimer.singleShot(800, _MANAGER.apply)
        QTimer.singleShot(1500, _MANAGER.note_daily_open)
        QTimer.singleShot(1700, _MANAGER.apply_potions)
    return _MANAGER
