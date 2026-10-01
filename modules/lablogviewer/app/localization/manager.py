"""Runtime localization for existing and dynamically-created Qt widgets."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject, QEvent, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QMenu, QTableWidget,
    QTabWidget, QTreeWidget, QWidget,
)

import re

from app.localization.catalogs import CATALOGS
from app.localization.terminology import SCIENTIFIC_TERMS
from app.localization.ui_strings import PATTERNS, UI
from app.settings.store import SettingsStore


_DEFAULT_STORE = None
_DEFAULT_MANAGER = None
_ACTIVE = None               # the manager installed on the application
_BYPASS = [False]            # True while the manager itself sets texts (keeps the English source)


def _compile_patterns():
    compiled = []
    # most specific first: "LabLogViewer v{} — Database Browser" before "LabLogViewer v{}"
    for english, chinese in sorted(PATTERNS, key=lambda pair: -len(pair[0].replace("{}", ""))):
        parts = english.split("{}")
        regex = "^" + "(.*?)".join(re.escape(part) for part in parts) + "$"
        compiled.append((re.compile(regex, re.DOTALL), chinese, parts[0][:6]))
    return compiled


_PATTERNS = _compile_patterns()
_UI_REVERSE = {chinese: english for english, chinese in UI.items()}


def translate_text(text: str, language: str) -> str:
    """English interface text -> ``language`` (catalogue, UI strings, then patterns)."""
    if not text or language == "en" or text in SCIENTIFIC_TERMS:
        return text
    manager = _DEFAULT_MANAGER
    if manager is not None:
        key = manager._source_by_english.get(text)
        if key is not None:
            return CATALOGS.get(language, {}).get(key, text)
    elif text in _EN_KEYS:
        return CATALOGS.get(language, {}).get(_EN_KEYS[text], text)
    if text in UI:
        return UI[text]
    stripped = text.strip()
    if stripped != text and stripped in UI:
        return text.replace(stripped, UI[stripped])
    for regex, chinese, prefix in _PATTERNS:
        if prefix and not text.startswith(prefix):
            continue
        match = regex.match(text)
        if match:
            values = [translate_text(v, language) if v in UI else v for v in match.groups()]
            try:
                return chinese.format(*values)
            except (IndexError, KeyError):
                return text
    return text


_EN_KEYS = {value: key for key, value in CATALOGS["en"].items()}


class LocalizationManager(QObject):
    language_changed = Signal(str)

    def __init__(self, store: SettingsStore | None = None, parent=None):
        super().__init__(parent)
        self.store = store or SettingsStore()
        self._language = self.store.language()
        self._bound: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self._source_by_english = {value: key for key, value in CATALOGS["en"].items()}
        self._source_by_chinese = {value: key for key, value in CATALOGS["zh_TW"].items()}

    @property
    def language(self) -> str:
        return self._language

    def text(self, key: str, **values) -> str:
        template = CATALOGS.get(self._language, CATALOGS["en"]).get(
            key, CATALOGS["en"].get(key, key)
        )
        return template.format(**values) if values else template

    def set_language(self, language: str) -> None:
        if language not in CATALOGS:
            raise ValueError(f"Unsupported language: {language}")
        if language == self._language:
            return
        self.store.set_language(language)
        self._language = language
        from shiboken6 import isValid

        for widget in tuple(self._bound):
            if widget is None or not isValid(widget):          # closed and deleted windows
                self._bound.discard(widget)
                continue
            self.retranslate_tree(widget)
        self.language_changed.emit(language)

    def install(self, app) -> None:
        global _ACTIVE
        _ACTIVE = self
        app.installEventFilter(self)
        for widget in app.topLevelWidgets():
            self.bind(widget)

    def bind(self, widget: QWidget) -> None:
        self._bound.add(widget)
        self.retranslate_tree(widget)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802 - Qt API spelling
        kind = event.type()
        if kind == QEvent.Type.Show and isinstance(watched, QWidget):
            if watched.isWindow() or isinstance(watched, QMenu):
                self.bind(watched)
        elif kind == QEvent.Type.Paint and self._language != "en" and isinstance(watched, QComboBox) \
                and not watched.isEditable():
            return _paint_translated_combo(watched, self)
        return False

    def _translated(self, source: str) -> str:
        if source in SCIENTIFIC_TERMS:
            return source
        key = self._source_by_english.get(source) or self._source_by_chinese.get(source)
        if key is not None:
            return CATALOGS[self._language].get(key, source)
        if source in _UI_REVERSE:
            source = _UI_REVERSE[source]
        return translate_text(source, self._language)

    def _source(self, obj, property_name: str, current: str) -> str:
        source = obj.property(property_name)
        if source is None:
            obj.setProperty(property_name, current)
            return current
        source = str(source)
        # the text on screen may be the source's translation in the previous language
        displayed = {source, self._translated(source)}
        for language in CATALOGS:
            key = self._source_by_english.get(source)
            displayed.add(CATALOGS[language].get(key, source) if key else translate_text(source, language))
        if current not in displayed:
            obj.setProperty(property_name, current)
            return current
        return source

    def _translate_action(self, action: QAction) -> None:
        source = self._source(action, "_lv_source_text", action.text())
        action.setText(self._translated(source))
        tooltip = action.toolTip()
        if tooltip:
            source_tip = self._source(action, "_lv_source_tooltip", tooltip)
            action.setToolTip(self._translated(source_tip))

    def _translate_widget(self, widget: QWidget) -> None:
        if isinstance(widget, QComboBox):
            # Drop-down items keep their text (code reads it); they are translated where drawn.
            widget.update()
        if isinstance(widget, QTabWidget):
            sources = getattr(widget, "_lv_tab_sources", None)
            if sources is None:
                sources = [widget.tabText(index) for index in range(widget.count())]
            elif len(sources) < widget.count():
                sources.extend(widget.tabText(index) for index in range(len(sources), widget.count()))
            widget._lv_tab_sources = sources
            for index, source in enumerate(sources[:widget.count()]):
                widget.setTabText(index, self._translated(str(source)))
        if isinstance(widget, (QTreeWidget, QTableWidget)):
            header = widget.headerItem() if isinstance(widget, QTreeWidget) else None
            count = header.columnCount() if header is not None else widget.columnCount()
            sources = getattr(widget, "_lv_header_sources", None)
            if sources is None:
                sources = []
                for index in range(count):
                    item = widget.horizontalHeaderItem(index) if header is None else None
                    sources.append(header.text(index) if header is not None else item.text() if item else "")
            elif len(sources) < count:
                for index in range(len(sources), count):
                    item = widget.horizontalHeaderItem(index) if header is None else None
                    sources.append(header.text(index) if header is not None else item.text() if item else "")
            widget._lv_header_sources = sources
            for index, source in enumerate(sources[:count]):
                if header is not None:
                    header.setText(index, self._translated(str(source)))
                elif widget.horizontalHeaderItem(index) is not None:
                    widget.horizontalHeaderItem(index).setText(self._translated(str(source)))
        elif isinstance(widget, QAbstractButton) and not bool(widget.property("_lv_content_value")):
            source = self._source(widget, "_lv_source_text", widget.text())
            widget.setText(self._translated(source))
        elif isinstance(widget, QLabel):
            source = self._source(widget, "_lv_source_text", widget.text())
            widget.setText(self._translated(source))
        if isinstance(widget, QGroupBox):
            source = self._source(widget, "_lv_source_title", widget.title())
            widget.setTitle(self._translated(source))
        if isinstance(widget, QLineEdit) and widget.placeholderText():
            source = self._source(widget, "_lv_source_placeholder", widget.placeholderText())
            widget.setPlaceholderText(self._translated(source))
        tooltip = widget.toolTip()
        if tooltip:
            source_tip = self._source(widget, "_lv_source_tooltip", tooltip)
            widget.setToolTip(self._translated(source_tip))
        if widget.isWindow() and widget.windowTitle():
            source = self._source(widget, "_lv_source_window_title", widget.windowTitle())
            widget.setWindowTitle(self._translated(source))
        for action in widget.actions():
            self._translate_action(action)

    def retranslate_tree(self, root: QWidget) -> None:
        _BYPASS[0] = True
        try:
            self._translate_widget(root)
            for child in root.findChildren(QWidget):
                self._translate_widget(child)
            for action in root.findChildren(QAction):
                self._translate_action(action)
        finally:
            _BYPASS[0] = False


def _paint_translated_combo(combo: QComboBox, manager) -> bool:
    """Draw a closed drop-down with its current item translated (the item text itself stays English)."""
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox, QStylePainter

    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    translated = manager._translated(option.currentText)
    if translated == option.currentText:
        return False
    option.currentText = translated
    painter = QStylePainter(combo)
    painter.setPen(combo.palette().color(combo.foregroundRole()))
    painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
    painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
    painter.end()
    return True


def translate_for_display(text: str) -> str:
    manager = _ACTIVE or _DEFAULT_MANAGER
    if manager is None or manager.language == "en":
        return text
    return manager._translated(text)


# -- texts set after a window is shown ---------------------------------------------------------
_PATCHED = [False]


def ask_before_replacing(save_dialog, args: list, kwargs: dict):
    """Run a getSaveFileName with the system's "Replace?" off and ask Overwrite / Keep Both / Cancel."""
    from PySide6.QtWidgets import QFileDialog

    from app.gui.save_target import confirm_save

    args = list(args)
    if len(args) > 5:
        args[5] = args[5] | QFileDialog.Option.DontConfirmOverwrite
    else:
        kwargs = dict(kwargs)
        kwargs["options"] = kwargs.get("options", QFileDialog.Option(0)) | QFileDialog.Option.DontConfirmOverwrite
    path, selected = save_dialog(*args, **kwargs)
    parent = args[0] if args else kwargs.get("parent")
    return confirm_save(parent, path, selected), selected


def install_dynamic_translation() -> None:
    """Translate texts that code sets while windows are open (status, dialogs, labels ...).

    Each widget keeps the English text as its source, so switching the language
    back restores English.
    """
    if _PATCHED[0]:
        return
    _PATCHED[0] = True
    from PySide6.QtWidgets import (
        QFileDialog, QInputDialog, QMessageBox, QStatusBar, QTreeWidget as _Tree,
    )

    def text_setter(cls, name, prop):
        original = getattr(cls, name)

        def setter(self, text, *args):
            if _BYPASS[0] or not isinstance(text, str):
                return original(self, text, *args)
            self.setProperty(prop, text)
            return original(self, translate_for_display(text), *args)

        setattr(cls, name, setter)

    text_setter(QLabel, "setText", "_lv_source_text")
    text_setter(QAbstractButton, "setText", "_lv_source_text")
    text_setter(QGroupBox, "setTitle", "_lv_source_title")
    text_setter(QWidget, "setToolTip", "_lv_source_tooltip")
    text_setter(QWidget, "setWindowTitle", "_lv_source_window_title")
    text_setter(QLineEdit, "setPlaceholderText", "_lv_source_placeholder")
    text_setter(QAction, "setText", "_lv_source_text")
    text_setter(QAction, "setToolTip", "_lv_source_tooltip")

    show_message = QStatusBar.showMessage

    def status(self, text, *args):
        return show_message(self, translate_for_display(text) if isinstance(text, str) else text, *args)

    QStatusBar.showMessage = status

    headers = _Tree.setHeaderLabels

    def header_labels(self, labels):
        labels = list(labels)
        if not _BYPASS[0]:
            self._lv_header_sources = labels
        return headers(self, [translate_for_display(str(label)) for label in labels])

    _Tree.setHeaderLabels = header_labels
    # Table headers are not translated here: data tables show channel names.

    for name in ("information", "warning", "critical", "question"):
        original = getattr(QMessageBox, name)

        def box(*args, _original=original, **kwargs):
            args = list(args)
            # (parent, title, text, ...)
            for index in (1, 2):
                if index < len(args) and isinstance(args[index], str):
                    args[index] = translate_for_display(args[index])
            return _original(*args, **kwargs)

        setattr(QMessageBox, name, staticmethod(box))
    for cls, name, positions in ((QMessageBox, "setText", (0,)), (QMessageBox, "setInformativeText", (0,))):
        original = getattr(cls, name)

        def method(self, *args, _original=original):
            args = [translate_for_display(a) if isinstance(a, str) else a for a in args]
            return _original(self, *args)

        setattr(cls, name, method)
    for cls, name in ((QInputDialog, "getText"), (QInputDialog, "getItem"), (QInputDialog, "getInt"),
                      (QInputDialog, "getDouble"), (QFileDialog, "getOpenFileName"), (QFileDialog, "getSaveFileName"),
                      (QFileDialog, "getExistingDirectory"), (QFileDialog, "getOpenFileNames")):
        original = getattr(cls, name)

        def dialog(*args, _original=original, _labels=(1, 2) if cls is QInputDialog else (1,), **kwargs):
            args = list(args)
            for index in _labels:
                if index < len(args) and isinstance(args[index], str):
                    args[index] = translate_for_display(args[index])
            if _original.__name__ != "getSaveFileName":
                return _original(*args, **kwargs)
            return ask_before_replacing(_original, args, kwargs)

        setattr(cls, name, staticmethod(dialog))


def get_localization_manager(store: SettingsStore | None = None) -> LocalizationManager:
    global _DEFAULT_MANAGER, _DEFAULT_STORE
    if store is not None:
        return LocalizationManager(store)
    if _DEFAULT_MANAGER is None:
        _DEFAULT_STORE = SettingsStore()
        _DEFAULT_MANAGER = LocalizationManager(_DEFAULT_STORE)
    return _DEFAULT_MANAGER


def initialize_localization(app, store: SettingsStore | None = None) -> LocalizationManager:
    manager = get_localization_manager(store)
    manager.install(app)
    install_dynamic_translation()
    return manager
