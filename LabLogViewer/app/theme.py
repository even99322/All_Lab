"""Global presentation theme; scientific data styling remains independent."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from pathlib import Path
import weakref

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from app.settings.store import SettingsStore


_THEME_APPLICATION_ACTIVE = False
APPEARANCE_BUTTON_SIZE = 40
TOOLBAR_ICON_SIZE = 32
ANALYSIS_ICON_SIZE = 20


# All colors live in app/palette.py; these names are kept for existing imports.
from app.palette import (  # noqa: E402
    APP_DARK as DARK, APP_LIGHT as LIGHT, ARROW, BUTTON_GLASS, CAPSULE_HOVER,
    PLOT_DARK as DARK_PLOT, PLOT_WHITE as WHITE_PLOT, ScientificPlotColors, ThemeColors, rgba_css,
)


class ThemeManager(QObject):
    """Own the persisted mode and apply its resolved palette app-wide."""

    _active_managers: weakref.WeakKeyDictionary[QApplication, ThemeManager] = weakref.WeakKeyDictionary()

    mode_changed = Signal(str)
    theme_changed = Signal(str)
    scientific_plot_appearance_changed = Signal(str)
    export_plot_background_changed = Signal(str)
    glass_material_changed = Signal(float, float, bool)

    def __init__(self, app: QApplication, store: SettingsStore, parent=None):
        super().__init__(parent or app)
        previous = self._active_managers.get(app)
        if previous is not None:
            previous.detach()
        self._active_managers[app] = self
        self.app = app
        self.store = store
        self._themed_widgets: weakref.WeakSet[QWidget] = weakref.WeakSet()
        self._applying_show_event = False
        self._mode = store.appearance()
        self._plot_appearance = store.scientific_plot_appearance()
        self._export_plot_background = store.export_plot_background()
        self._glass_material = store.glass_material()
        self._initial_system_dark = app.palette().color(QPalette.ColorRole.Window).lightness() < 128
        self._resolved = self._resolve_mode()
        self.app.installEventFilter(self)
        hints = self.app.styleHints()
        signal = getattr(hints, "colorSchemeChanged", None)
        if signal is not None:
            signal.connect(self._system_scheme_changed)
            self._system_scheme_signal = signal
        else:
            self._system_scheme_signal = None
        self._apply_theme()

    def detach(self) -> None:
        self.app.removeEventFilter(self)
        if self._active_managers.get(self.app) is self:
            self._active_managers.pop(self.app, None)
        if self._system_scheme_signal is not None:
            try:
                self._system_scheme_signal.disconnect(self._system_scheme_changed)
            except (RuntimeError, TypeError):
                pass

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def resolved_theme(self) -> str:
        return self._resolved

    @property
    def colors(self) -> ThemeColors:
        from app.core.personal import effective

        return effective("app_dark", DARK) if self._resolved == "dark" else effective("app_light", LIGHT)

    @property
    def scientific_plot_appearance(self) -> str:
        return self._plot_appearance

    @property
    def plot_colors(self) -> ScientificPlotColors:
        from app.core.personal import effective

        return effective("plot_dark", DARK_PLOT) if self._plot_appearance == "dark" \
            else effective("plot_white", WHITE_PLOT)

    @property
    def export_plot_background(self) -> str:
        return self._export_plot_background

    @property
    def export_plot_colors(self) -> ScientificPlotColors:
        from app.core.personal import effective

        return effective("plot_dark", DARK_PLOT) if self._export_plot_background == "dark" \
            else effective("plot_white", WHITE_PLOT)

    def refresh_personal(self) -> None:
        """Re-apply after the user changed Personal colours (Settings > Personal)."""
        self._apply_theme()
        self._apply_plot_appearance()
        self.theme_changed.emit(self._resolved)
        self.scientific_plot_appearance_changed.emit(self._plot_appearance)

    @property
    def glass_material(self) -> tuple[float, float]:
        """(thickness, frost) dials for the toolbar glass, each 0..1."""
        return self._glass_material

    def set_glass_material(self, thickness: float, frost: float, *, final: bool = True) -> None:
        """Update the live glass dials; only a final value is persisted."""
        values = (min(max(float(thickness), 0.0), 1.0), min(max(float(frost), 0.0), 1.0))
        changed = values != self._glass_material
        self._glass_material = values
        if final:
            self.store.set_glass_material(*values)
        if changed or final:
            self.glass_material_changed.emit(values[0], values[1], final)

    def set_mode(self, mode: str) -> None:
        if mode not in {"light", "dark", "system"}:
            raise ValueError(f"Unsupported appearance mode: {mode}")
        if mode == self._mode:
            return
        self.store.set_appearance(mode)
        self._mode = mode
        self.mode_changed.emit(mode)
        before = self._resolved
        after = self._resolve_mode()
        try:
            from app.gui.effects import theme_transition
        except Exception:
            self._resolve_and_apply()
            return
        # Circle from the pressed button; if the look stays the same the two colours "fight" first.
        rival = (LIGHT if after == "dark" else DARK).window
        theme_transition(self._resolve_and_apply, same_look=(before == after),
                         theme_color=self.colors.accent, rival_color=rival)

    def set_scientific_plot_appearance(self, appearance: str) -> None:
        if appearance not in {"white", "dark"}:
            raise ValueError(f"Unsupported scientific plot appearance: {appearance}")
        if appearance == self._plot_appearance:
            return
        self.store.set_scientific_plot_appearance(appearance)
        self._plot_appearance = appearance
        self._apply_plot_appearance()
        self.scientific_plot_appearance_changed.emit(appearance)

    def set_export_plot_background(self, appearance: str) -> None:
        if appearance not in {"white", "dark"}:
            raise ValueError(f"Unsupported export plot background: {appearance}")
        if appearance == self._export_plot_background:
            return
        self.store.set_export_plot_background(appearance)
        self._export_plot_background = appearance
        self.export_plot_background_changed.emit(appearance)

    def _resolve_mode(self) -> str:
        if self._mode == "light":
            return "light"
        if self._mode == "dark":
            return "dark"
        try:
            scheme = self.app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            if scheme == Qt.ColorScheme.Light:
                return "light"
        except (AttributeError, RuntimeError):
            pass
        windows_scheme = _windows_app_color_scheme()
        if windows_scheme is not None:
            return windows_scheme
        return "dark" if self._initial_system_dark else "light"

    def _system_scheme_changed(self, *_args) -> None:
        if self._mode == "system":
            self._resolve_and_apply()

    def _resolve_and_apply(self) -> None:
        resolved = self._resolve_mode()
        if resolved == self._resolved:
            return
        self._resolved = resolved
        self._apply_theme()
        self.theme_changed.emit(resolved)

    def _apply_theme(self) -> None:
        global _THEME_APPLICATION_ACTIVE
        if self._applying_show_event or _THEME_APPLICATION_ACTIVE:
            return
        _THEME_APPLICATION_ACTIVE = True
        self._applying_show_event = True
        try:
            self._apply_theme_contents()
        finally:
            self._applying_show_event = False
            _THEME_APPLICATION_ACTIVE = False

    def _apply_theme_contents(self) -> None:
        colors = self.colors
        palette = QPalette(self.app.palette())
        roles = {
            QPalette.ColorRole.Window: colors.window,
            QPalette.ColorRole.WindowText: colors.text,
            QPalette.ColorRole.Base: colors.input,
            QPalette.ColorRole.AlternateBase: colors.alternate,
            QPalette.ColorRole.ToolTipBase: colors.panel,
            QPalette.ColorRole.ToolTipText: colors.text,
            QPalette.ColorRole.Text: colors.text,
            QPalette.ColorRole.Button: colors.control,
            QPalette.ColorRole.ButtonText: colors.text,
            QPalette.ColorRole.Highlight: colors.selected,
            QPalette.ColorRole.HighlightedText: colors.text,
            QPalette.ColorRole.Link: colors.accent,
            QPalette.ColorRole.PlaceholderText: colors.secondary,
        }
        for role, color in roles.items():
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors.secondary))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors.secondary))
        if self.app.palette() != palette:
            self.app.setPalette(palette)
        for window in self.app.topLevelWidgets():
            if window.isVisible():
                self._apply_window_style(window)
        for widget in tuple(self._themed_widgets):
            try:
                if widget.isVisible():
                    self._apply_widget(widget)
            except RuntimeError:
                self._themed_widgets.discard(widget)

    def _apply_widget(self, widget: QWidget) -> None:
        if widget.isWindow():
            self._apply_window_style(widget)
        apply_theme = getattr(widget, "apply_lablog_theme", None)
        if callable(apply_theme):
            apply_theme(self.colors)
        apply_plot_appearance = getattr(widget, "apply_scientific_plot_appearance", None)
        if callable(apply_plot_appearance):
            apply_plot_appearance(self.plot_colors)
        _theme_pyqtgraph_widget(widget, self.plot_colors)
        _theme_matplotlib_figure(widget, self.plot_colors)
        if callable(apply_theme) or callable(getattr(widget, "getPlotItem", None)) or any(
            hasattr(getattr(widget, name, None), "get_axes") for name in ("figure", "fig")
        ) or callable(apply_plot_appearance):
            self._themed_widgets.add(widget)

    def _apply_window_style(self, widget: QWidget) -> None:
        stylesheet = _stylesheet(self.colors)
        if widget.styleSheet() != stylesheet:
            widget.setStyleSheet(stylesheet)

    def _apply_plot_appearance(self) -> None:
        for widget in tuple(self._themed_widgets):
            try:
                if widget.isVisible():
                    apply = getattr(widget, "apply_scientific_plot_appearance", None)
                    if callable(apply):
                        apply(self.plot_colors)
                    _theme_pyqtgraph_widget(widget, self.plot_colors)
                    _theme_matplotlib_figure(widget, self.plot_colors)
            except RuntimeError:
                self._themed_widgets.discard(widget)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802 - Qt API spelling
        global _THEME_APPLICATION_ACTIVE
        # Every event of the application passes here: leave at once unless it is one of
        # the four kinds this filter handles (this runs tens of thousands of times).
        kind = event.type()
        if kind not in _THEME_EVENTS or self._applying_show_event or _THEME_APPLICATION_ACTIVE:
            return False
        # Qt reports Unknown on some Windows configurations; the OS still
        # sends ThemeChange, so re-resolve Follow System from it.
        if (kind == QEvent.Type.ThemeChange and self._mode == "system"
                and (watched is self.app or (isinstance(watched, QWidget) and watched.isWindow()))):
            self._resolve_and_apply()
            return False
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress) and isinstance(watched, QComboBox):
            _fit_combo_popup(watched)
        if kind == QEvent.Type.Show and isinstance(watched, QWidget):
            _THEME_APPLICATION_ACTIVE = True
            self._applying_show_event = True
            try:
                self._apply_widget(watched)
            finally:
                self._applying_show_event = False
                _THEME_APPLICATION_ACTIVE = False
        return False


_DEFAULT_MANAGER: ThemeManager | None = None
_THEME_EVENTS = frozenset({QEvent.Type.ThemeChange, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
                           QEvent.Type.Show})


def _fit_combo_popup(combo: QComboBox) -> None:
    """Make the popup list at least as wide as its longest entry.

    Narrow sidebar combos otherwise clipped entries such as "3D Surface".
    """
    view = combo.view()
    if view is None or combo.count() == 0:
        return
    content = view.sizeHintForColumn(0)
    if content <= 0:
        return
    extra = 2 * view.frameWidth() + view.verticalScrollBar().sizeHint().width() + 12
    view.setMinimumWidth(max(combo.width(), content + extra))


def initialize_theme(app: QApplication, store: SettingsStore) -> ThemeManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is not None:
        _DEFAULT_MANAGER.detach()
        _DEFAULT_MANAGER.deleteLater()
    _DEFAULT_MANAGER = ThemeManager(app, store, app)
    from app.gui.app_icon import apply_app_icon

    apply_app_icon(store)                     # every process: main, 3D, Figure Builder
    return _DEFAULT_MANAGER


def get_theme_manager(app: QApplication | None = None, store: SettingsStore | None = None) -> ThemeManager | None:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        app = app or QApplication.instance()
        if app is not None:
            if store is None:
                from app.localization import get_localization_manager

                store = get_localization_manager().store
            _DEFAULT_MANAGER = ThemeManager(app, store, app)
    return _DEFAULT_MANAGER


def current_theme_colors() -> ThemeColors:
    app = QApplication.instance()
    manager = ThemeManager._active_managers.get(app) if app is not None else None
    manager = manager or _DEFAULT_MANAGER
    return manager.colors if manager is not None else LIGHT


def _windows_app_color_scheme() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except (ImportError, OSError):
        return None
    return "light" if value else "dark"


_GLYPHS = {
    "branch_closed": '<path d="M4.5 2.5 L8 6 L4.5 9.5"/>',
    "branch_open": '<path d="M2.5 4.5 L6 8 L9.5 4.5"/>',
    "spin_up": '<path d="M2.5 7.5 L6 4 L9.5 7.5"/>',
    "spin_down": '<path d="M2.5 4.5 L6 8 L9.5 4.5"/>',
}


def _glyph_url(kind: str, color: str) -> str:
    """Stylesheet url() for a small arrow drawn in ``color`` (palette ARROW).

    Generated on demand (cached by color) so a changed palette recolors the
    tree and spin-box arrows without editing icon files.
    """
    import tempfile

    folder = Path(tempfile.gettempdir()) / "lablogviewer-glyphs"
    path = folder / f"{kind}_{color.lstrip('#').lower()}.svg"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
            f'<g fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" '
            f'stroke-linejoin="round">{_GLYPHS[kind]}</g></svg>', encoding="utf-8")
    return path.as_posix()


def _branch_rules(colors: ThemeColors) -> str:
    """Explicit tree expand/collapse arrows.

    With the application stylesheet active, the native macOS branch arrows are
    drawn in a color that disappears on the Light theme, so every tree view
    gets theme-colored chevrons.
    """
    arrow = ARROW["dark" if QColor(colors.window).lightness() < 128 else "light"]
    closed = _glyph_url("branch_closed", arrow)
    opened = _glyph_url("branch_open", arrow)
    return f"""
        QTreeView::branch {{ background: transparent; border-image: none; }}
        QTreeView::branch:has-children:!has-siblings:closed,
        QTreeView::branch:closed:has-children:has-siblings {{ image: url("{closed}"); }}
        QTreeView::branch:open:has-children:!has-siblings,
        QTreeView::branch:open:has-children:has-siblings {{ image: url("{opened}"); }}
    """


def _spin_rules(colors: ThemeColors) -> str:
    """Visible spin-box step buttons in both themes.

    Styling QSpinBox borders makes Qt draw the step buttons itself, and the
    default arrows vanished on Light and Dark alike.
    """
    arrow = ARROW["dark" if QColor(colors.window).lightness() < 128 else "light"]
    up = _glyph_url("spin_up", arrow)
    down = _glyph_url("spin_down", arrow)
    return f"""
        QAbstractSpinBox {{ padding-right: 15px; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
            subcontrol-origin: border; width: 14px; background: transparent;
            border-left: 1px solid {colors.border}; }}
        QAbstractSpinBox::up-button {{ subcontrol-position: top right;
            border-bottom: 1px solid {colors.border}; }}
        QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
        QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
            background-color: {colors.alternate}; }}
        QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{
            background-color: {colors.selected}; }}
        QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
        QAbstractSpinBox::down-arrow {{ image: url("{down}"); width: 9px; height: 9px; }}
        QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off,
        QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}
    """


def _rgba(color: str, alpha: float) -> str:
    value = QColor(color)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {round(alpha * 255)})"


def _glass_button_rules(colors: ThemeColors) -> str:
    """Liquid-glass push buttons: specular top rim, split body gradient, soft edge.

    Stylesheets cannot refract, so the material is approximated with a bright
    upper half, a slightly deeper lower half and a lighter top border; hover,
    press and checked states tint it with the theme accent.
    """
    glass = BUTTON_GLASS["dark" if QColor(colors.window).lightness() < 128 else "light"]
    top, upper, lower, bottom = (rgba_css(stop) for stop in glass["body"])
    edge, rim, low_edge = rgba_css(glass["edge"]), rgba_css(glass["rim"]), rgba_css(glass["low_edge"])
    hover_top, hover_bottom = (rgba_css(stop) for stop in glass["hover"])
    press_top, press_bottom = (_rgba(colors.accent, alpha) for alpha in glass["pressed_accent_alpha"])
    body = ("qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {0}, stop:0.48 {1}, "
            "stop:0.52 {2}, stop:1 {3})")
    return f"""
        QPushButton, QToolButton {{ color: {colors.text};
            background-color: {body.format(top, upper, lower, bottom)};
            border: 1px solid {edge}; border-top-color: {rim}; border-bottom-color: {low_edge};
            border-radius: 10px; padding: 3px 12px; min-height: 18px; }}
        QPushButton:hover, QToolButton:hover {{
            background-color: {body.format(hover_top, hover_top, hover_bottom, hover_bottom)};
            border-color: {_rgba(colors.accent, 0.55)}; border-top-color: {rim}; }}
        QPushButton:pressed, QToolButton:pressed {{
            background-color: {body.format(press_top, press_top, press_bottom, press_bottom)};
            border-color: {_rgba(colors.accent, 0.75)}; }}
        QPushButton:checked, QPushButton:checked:hover, QToolButton:checked, QToolButton:checked:hover {{
            background-color: {body.format(_rgba(colors.accent, 0.30), _rgba(colors.accent, 0.22),
                                           _rgba(colors.accent, 0.16), _rgba(colors.accent, 0.24))};
            border: 1px solid {colors.accent}; border-top-color: {rim}; }}
        QPushButton:disabled, QToolButton:disabled {{ color: {colors.secondary};
            background-color: {body.format(*(_rgba(colors.control, a) for a in (0.55, 0.45, 0.40, 0.45)))};
            border-color: {_rgba(colors.border, 0.6)}; }}
        QToolButton {{ padding: 2px 6px; min-height: 16px; }}
        QPushButton:focus, QToolButton:focus {{ outline: none; }}
        QPushButton:default {{ border-color: {_rgba(colors.accent, 0.8)}; border-top-color: {rim}; }}
    """


def _capsule_button_rules(colors: ThemeColors) -> str:
    """Buttons inside glass capsules get round, translucent hover lenses."""
    lens = CAPSULE_HOVER["dark" if QColor(colors.window).lightness() < 128 else "light"]
    hover, hover_edge = rgba_css(lens["fill"]), rgba_css(lens["edge"])
    hosts = ("QWidget#browserGlassToolbar QToolButton", "QToolBar#viewerApplicationToolbar QToolButton",
             "QToolBar#threeDToolbar QToolButton")
    def select(state: str = "") -> str:
        return ", ".join(f"{host}{state}" for host in hosts)
    radius = TOOLBAR_ICON_SIZE // 2 + 4
    return f"""
        {select()} {{ background-color: transparent; border: 1px solid transparent;
            border-radius: {radius}px; padding: 3px; }}
        {select(":hover")} {{ background-color: {hover}; border: 1px solid {hover_edge}; }}
        {select(":pressed")}, {select(":checked")}, {select(":checked:hover")} {{
            background-color: {_rgba(colors.accent, 0.20)}; border: 1px solid {_rgba(colors.accent, 0.85)}; }}
        {select(":disabled")} {{ background-color: transparent; border: 1px solid transparent; }}
    """


def _stylesheet(colors: ThemeColors) -> str:
    return _glass_button_rules(colors) + _branch_rules(colors) + _spin_rules(colors) + f"""
        QWidget {{ color: {colors.text}; }}
        QMainWindow, QDialog, QMenuBar, QStatusBar {{ background-color: {colors.window}; }}
        QToolBar {{ background-color: {colors.alternate}; border: 0; }}
        QGroupBox {{ color: {colors.text}; background-color: {colors.panel};
            border: 1px solid {colors.border}; margin-top: 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left;
            left: 6px; padding: 0 3px; color: {colors.secondary}; }}
        QTreeWidget, QTableWidget, QListWidget, QPlainTextEdit, QTextEdit,
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            color: {colors.text}; background-color: {colors.input};
            selection-background-color: {colors.selected};
            selection-color: {colors.text}; border: 1px solid {colors.border};
        }}
        QHeaderView::section {{ color: {colors.text}; background-color: {colors.alternate};
            border: 1px solid {colors.border}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
            border: 1px solid {colors.accent}; }}
        QTabWidget::pane {{ background-color: {colors.panel}; border: 1px solid {colors.border}; }}
        QTabBar::tab {{ color: {colors.secondary}; background-color: {colors.panel};
            border: 1px solid {colors.border}; border-bottom: 2px solid {colors.border};
            padding: 4px 12px; }}
        QTabBar::tab:selected {{ color: {colors.text}; background-color: {colors.selected};
            border-bottom: 2px solid {colors.accent}; }}
        QSplitter::handle {{ background-color: {colors.border}; }}
        QMenu {{ color: {colors.text}; background-color: {colors.panel}; border: 1px solid {colors.border}; }}
        QMenu::item:selected {{ background-color: {colors.selected}; }}
        QToolTip {{ color: {colors.text}; background-color: {colors.panel}; border: 1px solid {colors.border}; }}
        QToolButton::menu-indicator {{ image: none; width: 0px; }}
        QToolBar QToolButton, QToolButton[lvFlat="true"] {{ background-color: transparent;
            border: 1px solid transparent; border-radius: 6px; padding: 3px; }}
        QToolBar QToolButton:hover, QToolButton[lvFlat="true"]:hover {{
            background-color: {colors.alternate}; border: 1px solid {colors.border}; }}
        QToolBar QToolButton:pressed, QToolButton[lvFlat="true"]:pressed,
        QToolBar QToolButton:checked, QToolButton[lvFlat="true"]:checked,
        QToolBar QToolButton:checked:hover, QToolButton[lvFlat="true"]:checked:hover {{
            background-color: {colors.selected}; border: 1px solid {colors.accent}; }}
        QToolBar QToolButton:disabled, QToolButton[lvFlat="true"]:disabled {{
            background-color: transparent; border: 1px solid transparent; }}
        QLabel#formulaPreview {{ color: {colors.secondary}; background-color: {colors.alternate};
            border: 1px solid {colors.border}; padding: 2px 4px; }}
        QLabel#glassDialLabel {{ color: {colors.secondary}; background: transparent; font-size: 11px; }}
        QToolButton#appearanceToggle {{ background-color: {colors.control};
            border: 1px solid {colors.border}; border-radius: {APPEARANCE_BUTTON_SIZE // 2}px; }}
        QToolButton#appearanceToggle:hover {{ border: 1px solid {colors.accent}; }}
        QToolButton#appearanceToggle:pressed {{ background-color: {colors.selected}; }}
        QTabBar#yigAnalysisTabs::tab {{ padding: 10px 14px; }}
        QToolButton#annotationToggleLarge {{ background-color: {colors.control};
            border: 1px solid {colors.border}; border-radius: 17px; padding: 4px; }}
        QToolButton#annotationToggleLarge:hover {{ border: 1px solid {colors.accent}; }}
        QToolButton#annotationToggleLarge:checked {{ background-color: {colors.selected};
            border: 1px solid {colors.accent}; }}
    """ + _capsule_button_rules(colors)


FOREGROUND_LINE_GID = "lablog-foreground"


def _theme_matplotlib_figure(widget: QWidget, colors: ScientificPlotColors) -> None:
    for name in ("figure", "fig"):
        figure = getattr(widget, name, None)
        if figure is None or not hasattr(figure, "get_axes"):
            continue
        figure.set_facecolor(colors.plot_background)
        for axis in figure.get_axes():
            axis.set_facecolor(colors.plot_panel)
            axis.tick_params(axis="both", colors=colors.text)
            axis.xaxis.label.set_color(colors.text)
            axis.yaxis.label.set_color(colors.text)
            axis.title.set_color(colors.text)
            for spine in axis.spines.values():
                spine.set_color(colors.border)
            for label in (*axis.get_xticklabels(), *axis.get_yticklabels()):
                label.set_color(colors.text)
            for line in (*axis.get_xgridlines(), *axis.get_ygridlines()):
                if line.get_visible():
                    line.set_color(colors.border)
                    line.set_alpha(0.45)
            # Neutral data lines (e.g. Residual) follow the text color so they
            # stay visible on dark plot backgrounds; colored data lines keep theirs.
            for line in axis.get_lines():
                if line.get_gid() == FOREGROUND_LINE_GID:
                    line.set_color(colors.text)
            legend = axis.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor(colors.plot_panel)
                legend.get_frame().set_edgecolor(colors.border)
                for label in legend.get_texts():
                    label.set_color(colors.text)
        canvas = getattr(widget, "canvas", None)
        if canvas is not None and hasattr(canvas, "draw_idle"):
            canvas.draw_idle()


def _theme_pyqtgraph_widget(widget: QWidget, colors: ScientificPlotColors) -> None:
    get_plot_item = getattr(widget, "getPlotItem", None)
    if not callable(get_plot_item):
        return
    widget.setBackground(colors.plot_background)
    plot_item = get_plot_item()
    for axis in plot_item.axes.values():
        axis_item = axis["item"]
        axis_item.setPen(colors.secondary)
        axis_item.setTextPen(colors.text)
        axis_item.setGrid(75)
        label = getattr(axis_item, "label", None)
        if label is not None and hasattr(label, "setColor"):
            label.setColor(colors.text)
    widget.update()
