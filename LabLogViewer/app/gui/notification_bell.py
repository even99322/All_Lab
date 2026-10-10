"""The notification bell (Browser window, top right) with its list, and the update preview.

The bell shows a red count of unread notices. Clicking it opens a list of the latest notices; an
update notice shows its first lines and "… more", which opens the full preview (every change,
grouped, with "Skip this version"). The same notices appear above the desktop pet when one is shown.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
    QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from app.core import notifications
from app.icons import make_icon_only

PANEL_WIDTH = 380
PANEL_MAX_HEIGHT = 440


def _language(localizer) -> str:
    return "zh" if str(getattr(localizer, "language", "en")).startswith("zh") else "en"


def _stamp(iso: str, text=None) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if text is None:
        return then.strftime("%Y-%m-%d %H:%M")
    seconds = max(0, int((datetime.now() - then).total_seconds()))
    if seconds < 60:
        return text("notice.ago.now")
    if seconds < 3600:
        return text("notice.ago.min").format(n=seconds // 60)
    if seconds < 86400:
        return text("notice.ago.hour").format(n=seconds // 3600)
    return text("notice.ago.day").format(n=seconds // 86400)


KIND_BADGE = {"update": ("↑", "#0A84FF"), "measurement": ("✓", "#30D158"),
              "streak": ("★", "#FF9F0A"), "levelup": ("▲", "#BF5AF2")}


def _dark() -> bool:
    from PySide6.QtWidgets import QApplication

    return QApplication.palette().window().color().lightness() < 128


def _glass(dark: bool) -> dict:
    if dark:
        return {"card": "rgba(44,48,58,205)", "edge": "rgba(255,255,255,55)", "fg": "#F5F5F7", "dim": "rgba(245,245,247,160)"}
    return {"card": "rgba(250,250,252,205)", "edge": "rgba(255,255,255,200)", "fg": "#1D1D1F", "dim": "rgba(29,29,31,150)"}


class UpdatePreviewDialog(QDialog):
    """Every change of one update, grouped, with the choice to skip that version."""

    def __init__(self, localizer, record: dict, parent=None):
        super().__init__(parent)
        self.localizer, self.record = localizer, record
        text = localizer.text
        details = record.get("details") or {}
        version = details.get("version") or record.get("params", {}).get("version", "")
        self.setObjectName("updatePreviewDialog")
        self.setWindowTitle(text("notice.update_title").format(version=version))
        self.resize(540, 480)
        layout = QVBoxLayout(self)
        heading = QLabel(text("notice.update_title").format(version=version))
        heading.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(heading)
        lines = notifications.update_lines(record, _language(localizer))
        sub = [text("notice.update_date").format(date=details["date"])] if details.get("date") else []
        if lines:
            sub.append(text("notice.update_count").format(n=len(lines)))
        if sub:
            layout.addWidget(QLabel(" · ".join(sub)))
        self.body = QTextBrowser()
        self.body.setObjectName("updatePreviewBody")
        self.body.setOpenLinks(False)
        glass = _glass(_dark())
        self.body.setStyleSheet(f"QTextBrowser {{ background: {glass['card']}; border: 1px solid {glass['edge']};"
                                f" border-radius: 16px; padding: 8px; color: {glass['fg']}; }}")
        self.body.setHtml(self._html(details))
        layout.addWidget(self.body, 1)
        buttons = QDialogButtonBox()
        from app.core import hub_update

        self.update_button = None
        if hub_update.can_install(details):             # v2.1.2: install from the lab hub without a browser
            self.update_button = buttons.addButton(text("update.now"), QDialogButtonBox.ButtonRole.AcceptRole)
            self.update_button.setObjectName("updateNowButton")
            self.update_button.clicked.connect(self._update_now)
        self.skip_button = buttons.addButton(text("notice.skip"), QDialogButtonBox.ButtonRole.ActionRole)
        self.close_button = buttons.addButton(text("notice.close"), QDialogButtonBox.ButtonRole.RejectRole)
        self.skip_button.clicked.connect(self._skip)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        notifications.mark_read(record["id"])

    def _html(self, details: dict) -> str:
        language = _language(self.localizer)
        text = self.localizer.text
        groups = []
        for category in ("new", "improved", "fixed"):
            rows = [(e.get(language) or e.get("en") or e.get("zh") or "") for e in details.get("items", [])
                    if e.get("category") == category]
            rows = [r for r in rows if r]
            if rows:
                groups.append(f"<h3>{escape(text('notice.cat.' + category))}</h3><ul>"
                              + "".join(f"<li>{escape(r)}</li>" for r in rows) + "</ul>")
        return "".join(groups) or f"<p>{escape(text('notice.update_none'))}</p>"

    def _update_now(self) -> None:
        from app.gui.hub_updates import install_now

        details = dict(self.record.get("details") or {})
        details.setdefault("version", self.record.get("params", {}).get("version", ""))
        if install_now(self, self.localizer, details):
            self.accept()

    def _skip(self) -> None:
        version = (self.record.get("details") or {}).get("version") or self.record.get("params", {}).get("version")
        if version:
            notifications.skip_version(str(version))
        notifications.remove(self.record["id"])
        self.accept()


class _Row(QFrame):
    activated = Signal(int)
    more_requested = Signal(int)

    def __init__(self, localizer, record: dict):
        super().__init__()
        self.record_id = record["id"]
        shown = notifications.describe(record, localizer.text, _language(localizer))
        colors = _glass(_dark())
        self.setObjectName("noticeRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"#noticeRow {{ background: {colors['card']}; border: 1px solid {colors['edge']}; border-radius: 18px; }}"
            f"#noticeRow QLabel {{ background: transparent; color: {colors['fg']}; }}"
            f"#noticeRow QLabel#noticeStamp {{ color: {colors['dim']}; font-size: 12px; }}"
            "#noticeRow QPushButton#noticeMore { background: transparent; border: none; color: #0A84FF; padding: 0; }")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)
        glyph, tint = KIND_BADGE.get(record["kind"], ("•", "#8E8E93"))
        badge = QLabel(glyph)
        badge.setObjectName("noticeBadge")
        badge.setFixedSize(38, 38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"#noticeBadge {{ background: {tint}; color: white; border-radius: 10px; font-size: 18px;"
                            " font-weight: 700; }")
        outer.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(2)
        head = QHBoxLayout()
        title = QLabel(("● " if not shown["read"] else "") + shown["title"])
        font = title.font(); font.setBold(True); title.setFont(font)
        head.addWidget(title, 1)
        stamp = QLabel(_stamp(shown["time"], localizer.text))
        stamp.setObjectName("noticeStamp")
        head.addWidget(stamp, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(head)
        body = QLabel(shown["body"]); body.setWordWrap(True)
        column.addWidget(body)
        for line in shown["lines"]:
            item = QLabel("· " + line); item.setWordWrap(True)
            column.addWidget(item)
        self.more_button = None
        if shown["more"]:
            self.more_button = QPushButton(localizer.text("notice.more"))
            self.more_button.setObjectName("noticeMore")
            self.more_button.setFlat(True)
            self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.more_button.clicked.connect(lambda: self.more_requested.emit(self.record_id))
            column.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignLeft)
        outer.addLayout(column, 1)

    def mouseReleaseEvent(self, event):
        self.activated.emit(self.record_id)
        super().mouseReleaseEvent(event)


class NotificationPanel(QFrame):
    """The popup list; rebuilt whenever it is opened or the notices change."""

    changed = Signal()

    def __init__(self, localizer, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.localizer = localizer
        self.setObjectName("notificationPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(PANEL_WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(8)
        top = QHBoxLayout()
        self.mark_button = QPushButton(); self.mark_button.setObjectName("noticeMarkAll")
        self.clear_button = QPushButton(); self.clear_button.setObjectName("noticeClear")
        self.settings_button = QToolButton(); self.settings_button.setObjectName("noticeSettings")
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.mark_button.clicked.connect(self._mark_all)
        self.clear_button.clicked.connect(self._clear)
        top.addWidget(self.mark_button); top.addWidget(self.clear_button); top.addStretch(1)
        top.addWidget(self.settings_button)
        outer.addLayout(top)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        outer.addWidget(self.scroll)
        self.settings_menu = QMenu(self.settings_button)
        self.settings_button.setMenu(self.settings_menu)
        self.refresh()

    def refresh(self) -> None:
        text = self.localizer.text
        colors = _glass(_dark())
        small = (f"QPushButton, QToolButton {{ background: {colors['card']}; border: 1px solid {colors['edge']};"
                 f" border-radius: 12px; padding: 4px 12px; color: {colors['fg']}; }}"
                 "QToolButton::menu-indicator { image: none; }")
        for widget in (self.mark_button, self.clear_button, self.settings_button):
            widget.setStyleSheet(small)
        self.mark_button.setText(text("notice.mark_all"))
        self.clear_button.setText(text("notice.clear"))
        self.settings_button.setText("⚙")
        self.settings_button.setStyleSheet(
            small + f"QToolButton {{ font-size: 22px; padding: 0 8px; border-radius: 16px; }}")
        self.settings_button.setMinimumSize(40, 32)
        self.settings_button.setToolTip(text("notice.settings"))
        self._build_menu()
        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        records = notifications.items()
        if not records:
            empty = QLabel(text("notice.empty"))
            empty.setObjectName("noticeEmpty")
            glass = _glass(_dark())
            empty.setStyleSheet(f"#noticeEmpty {{ background: {glass['card']}; border: 1px solid {glass['edge']};"
                                f" border-radius: 18px; color: {glass['fg']}; }}")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(90)
            column.addWidget(empty)
        self.rows = []
        for record in records:
            row = _Row(self.localizer, record)
            row.activated.connect(self._row_activated)
            row.more_requested.connect(self.open_preview)
            column.addWidget(row)
            self.rows.append(row)
        column.addStretch(1)
        self.scroll.setWidget(content)
        content.adjustSize()
        height = min(PANEL_MAX_HEIGHT, max(90, content.sizeHint().height()))
        self.scroll.setFixedHeight(height + 4)
        self.mark_button.setEnabled(notifications.unread_count() > 0)
        self.clear_button.setEnabled(bool(records))
        self.adjustSize()

    def _build_menu(self) -> None:
        text = self.localizer.text
        self.settings_menu.clear()
        state = notifications.load()["prefs"]
        mute = QAction(text("notice.mute"), self.settings_menu, checkable=True)
        mute.setChecked(state["muted"])
        mute.toggled.connect(lambda on: (notifications.set_muted(on), self.changed.emit()))
        self.settings_menu.addAction(mute)
        self.settings_menu.addSeparator()
        self.kind_actions = {}
        for kind in notifications.KINDS:
            action = QAction(text(f"notice.kind.{kind}"), self.settings_menu, checkable=True)
            action.setChecked(state["kinds"][kind])
            action.setEnabled(not state["muted"])
            action.toggled.connect(lambda on, k=kind: (notifications.set_kind_enabled(k, on), self.changed.emit()))
            self.settings_menu.addAction(action)
            self.kind_actions[kind] = action
        self.mute_action = mute

    def _row_activated(self, notice_id: int) -> None:
        notifications.mark_read(notice_id)
        self.refresh()
        self.changed.emit()

    def _mark_all(self) -> None:
        notifications.mark_all_read()
        self.refresh()
        self.changed.emit()

    def _clear(self) -> None:
        notifications.clear()
        self.refresh()
        self.changed.emit()

    def open_preview(self, notice_id: int) -> UpdatePreviewDialog | None:
        record = notifications.get(notice_id)
        if record is None:
            return None
        self.hide()
        dialog = UpdatePreviewDialog(self.localizer, record, self.parentWidget())
        dialog.finished.connect(lambda *_: self.changed.emit())
        self.preview = dialog
        dialog.show()
        dialog.raise_()
        return dialog


class BellButton(QToolButton):
    """Toolbar bell with an unread count; also hands new notices to the desktop pet."""

    _event = Signal(str, object)           # notifications may arrive from any thread

    def __init__(self, localizer, size: int, parent=None):
        super().__init__(parent)
        self.localizer = localizer
        self.setObjectName("notificationBell")
        make_icon_only(self, "bell", localizer.text("notice.bell"), size, flat=True)
        self.panel = None
        self._event.connect(self._on_event)
        self._listener = lambda event, record: self._event.emit(event, record)
        notifications.add_listener(self._listener)
        self.destroyed.connect(lambda *_: notifications.remove_listener(self._listener))
        self.clicked.connect(self.show_panel)
        if hasattr(localizer, "language_changed"):
            localizer.language_changed.connect(self._sync)
        self._sync()

    def unread(self) -> int:
        return notifications.unread_count()

    def _sync(self) -> None:
        n = self.unread()
        label = self.localizer.text("notice.bell_unread").format(n=n) if n else self.localizer.text("notice.bell")
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.update()

    def _on_event(self, event: str, record) -> None:
        self._sync()
        if self.panel is not None and self.panel.isVisible():
            self.panel.refresh()
        if event == "added" and record is not None:
            self._tell_pet(record)

    def _tell_pet(self, record: dict) -> None:
        try:
            from app.pet.manager import manager

            mgr = manager()
            if mgr is None:
                return
            shown = notifications.describe(record, self.localizer.text, _language(self.localizer))
            mgr.notice(shown["title"], shown["body"], on_click=self.show_panel)
        except Exception:
            pass

    def show_panel(self) -> NotificationPanel:
        if self.panel is None:
            self.panel = NotificationPanel(self.localizer, self.window())
            self.panel.changed.connect(self._sync)
        self.panel.refresh()
        below = self.mapToGlobal(QPoint(self.width(), self.height() + 4))
        self.panel.move(below.x() - self.panel.width(), below.y())
        self.panel.show()
        self.panel.raise_()
        return self.panel

    def paintEvent(self, event):
        super().paintEvent(event)
        n = self.unread()
        if not n:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label = str(n) if n < 100 else "99+"
        font = QFont(self.font()); font.setPixelSize(10); font.setBold(True)
        painter.setFont(font)
        width = max(16, painter.fontMetrics().horizontalAdvance(label) + 8)
        rect = QRect(self.width() - width - 1, 1, width, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FF3B30"))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor("white"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
