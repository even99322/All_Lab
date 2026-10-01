from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _spin_until(qapp, predicate, timeout_ms=15000):
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)

    def check():
        if predicate():
            loop.quit()
        else:
            QTimer.singleShot(20, check)

    QTimer.singleShot(0, check)
    timer.start(timeout_ms)
    loop.exec()
    return bool(predicate())


def test_lifecycle_normal_exit_allows_regular_restore(tmp_path):
    from app.core.session_lifecycle import SessionLifecycleStore

    path = tmp_path / "lifecycle.json"
    first = SessionLifecycleStore(path)
    assert not first.begin().safe_recovery
    first.record_checkpoint("Database loaded successfully", database_path=str(tmp_path))
    first.record_operation("3D Surface rendering started")
    first.mark_clean()

    next_session = SessionLifecycleStore(path)
    context = next_session.begin()
    assert not context.safe_recovery
    assert context.previous_status == "clean"
    assert context.database_path == str(tmp_path.resolve())
    next_session.mark_clean()


def test_unclean_lifecycle_captures_last_operation_and_unknown_cause(tmp_path):
    from app.core.session_lifecycle import SessionLifecycleStore

    path = tmp_path / "lifecycle.json"
    crashed = SessionLifecycleStore(path)
    crashed.begin()
    crashed.record_checkpoint("Database loaded successfully", database_path=str(tmp_path))
    crashed.record_operation("3D Surface rendering started", rows=855, columns=501)

    restarted = SessionLifecycleStore(path)
    context = restarted.begin()
    assert context.safe_recovery
    assert context.last_operation == "3D Surface rendering started"
    assert context.last_checkpoint == "Database loaded successfully"
    assert context.database_path == str(tmp_path.resolve())
    assert context.last_exception is None
    restarted.mark_clean()


def test_corrupt_session_is_backed_up_and_requires_safe_recovery(tmp_path):
    from app.core.session_store import SessionStore

    path = tmp_path / "session.json"
    path.write_text("{bad json", encoding="utf-8")
    store = SessionStore(path)
    assert store.get() == {}
    assert store.recovered_from_corruption
    assert store.requires_safe_recovery
    assert path.with_suffix(".json.bak").exists()


def test_newer_session_schema_is_reported_as_unavailable(qapp, tmp_path):
    from app.core.session_lifecycle import StartupRecovery
    from app.core.session_store import SessionStore
    from app.gui.recovery_report import RecoveryReportDialog

    path = tmp_path / "session.json"
    path.write_text(json.dumps({"schema_version": 2, "session": {"future": True}}), encoding="utf-8")
    store = SessionStore(path)
    assert store.requires_safe_recovery
    assert not store.recovered_from_corruption
    dialog = RecoveryReportDialog(StartupRecovery(
        safe_recovery=True,
        session_state_unavailable=store.requires_safe_recovery,
    ))
    assert "newer-schema data was ignored" in dialog.report_text.toPlainText()
    assert "backup of the malformed session" not in dialog.report_text.toPlainText()
    dialog.close()


def test_recovery_report_never_invents_a_cause(qapp, tmp_path):
    from app.core.session_lifecycle import StartupRecovery
    from app.gui.recovery_report import RecoveryReportDialog

    dialog = RecoveryReportDialog(StartupRecovery(
        safe_recovery=True,
        last_operation="3D Surface rendering started",
        last_checkpoint="Database loaded successfully",
        database_path=str(tmp_path / "research-data"),
    ))
    report = dialog.report_text.toPlainText()
    assert "Safe Recovery Mode" in report
    assert "Last recorded operation: 3D Surface rendering started" in report
    assert "Possible cause: Unknown" in report
    assert "research-data" in report
    dialog.close()


def test_unclean_browser_restore_reopens_database_but_skips_viewers(qapp, tmp_path):
    from app.core.session_lifecycle import SessionLifecycleStore
    from app.core.session_store import SessionStore
    from app.core.star_store import StarStore
    from app.core.data_identity import stable_data_identity
    from app.gui.browser_window import BrowserWindow
    from tests.real_data import SMALL_FILE

    if not SMALL_FILE.exists():
        pytest.skip("Real Labber fixture is unavailable.")

    database = tmp_path / "database"
    database.mkdir()
    copied_data = database / "sample.hdf5"
    shutil.copy2(SMALL_FILE, copied_data)
    session_path = tmp_path / "session.json"
    lifecycle_path = tmp_path / "lifecycle.json"
    store = SessionStore(session_path)
    store.set({
        "database_path": str(database),
        "browser": {"selected_relative_path": copied_data.name},
        "viewers": [{"source_path": stable_data_identity(copied_data), "display": {"mode": "3d"}}],
    })

    crashed = SessionLifecycleStore(lifecycle_path)
    crashed.begin()
    crashed.record_checkpoint("Database loaded successfully", database_path=str(database))
    crashed.record_operation("3D Surface rendering started")
    restarting = SessionLifecycleStore(lifecycle_path)
    recovery = restarting.begin()

    browser = BrowserWindow(
        star_store=StarStore(tmp_path / "stars.json"),
        session_store=SessionStore(session_path),
        session_lifecycle=restarting,
        startup_recovery=recovery,
    )
    browser.show()
    assert browser.restore_previous_session()
    assert _spin_until(qapp, lambda: browser.scan_result is not None)
    assert browser._selected_data()[1].relative_path == copied_data.name
    assert not [viewer for viewer in browser._viewers if viewer.isVisible()]
    assert browser._recovery_report_dialog is not None
    assert "Safe Recovery Mode" in browser._recovery_report_dialog.report_text.toPlainText()
    browser.close()
    restarting.mark_clean()


def test_surface_colormap_is_camera_independent_and_finite_safe(qapp):
    from PySide6.QtDataVisualization import Q3DCamera

    from app.visualization3d.renderer import _apply_camera_pose, map_surface_colors

    values = np.array([[-30.0, -20.0, -10.0], [np.nan, -5.0, 0.0]])
    before = map_surface_colors(values, "LabLog BWR", (-30.0, 0.0))
    camera = Q3DCamera()
    _apply_camera_pose(camera, 35, 25, 100, camera.target())
    _apply_camera_pose(camera, 205, 65, 220, camera.target())
    after = map_surface_colors(values, "LabLog BWR", (-30.0, 0.0))
    assert np.array_equal(before, after)
    assert before[0, 0, 0] > before[0, 0, 2]
    assert before[1, 0, 3] == 0
    assert np.all(before[np.isfinite(values), 3] == 255)


def test_bwr_scalar_mapping_and_native_surface_gradient_match(qapp):
    from app.visualization3d.renderer import (
        build_surface_gradient, map_surface_colors,
        surface_color_mapping_diagnostics,
    )

    # Runtime-like limits are deliberately data-driven rather than copied
    # from the user's screenshot.
    values = np.array([[-36.2, -21.0, -0.4], [-31.0, -17.7, -1.1]])
    low, high = float(values.min()), float(values.max())
    mapped = map_surface_colors(np.array([[low, (low + high) / 2.0, high]]), "LabLog BWR", (low, high))[0]
    assert mapped[0, 0] > mapped[0, 2]  # low endpoint is the red side
    assert np.allclose(mapped[1, :3], [255, 255, 255], atol=2)
    assert mapped[2, 2] > mapped[2, 0]  # high endpoint is the blue side
    assert len({tuple(color) for color in mapped[:, :3]}) == 3

    gradient = build_surface_gradient("LabLog BWR", (low, high), (low, high))
    stops = gradient.stops()
    assert len(stops) == 257
    assert stops[0][1].red() > stops[0][1].blue()
    assert np.allclose(stops[len(stops) // 2][1].getRgb()[:3], [255, 255, 255], atol=2)
    assert stops[-1][1].blue() > stops[-1][1].red()

    diagnostics = surface_color_mapping_diagnostics(values, "LabLog BWR", (low, high))
    assert [round(item["normalized"], 3) for item in diagnostics] == [0.0, 0.5, 1.0]
    assert [item["rgba"] for item in diagnostics] == [list(map(int, color)) for color in mapped]


def test_surface_series_binds_colorbar_semantics_to_real_surface_path(qapp):
    from PySide6.QtGui import QLinearGradient, QImage

    from app.visualization3d.renderer import configure_surface_series_colors

    class Series:
        def __init__(self):
            self.calls = []

        def setBaseColor(self, value):
            self.calls.append(("base-color", value.name()))

        def setTexture(self, value):
            self.calls.append(("texture", value.isNull()))

        def setBaseGradient(self, value):
            self.calls.append(("gradient", value))

        def setColorStyle(self, value):
            self.calls.append(("style", value))

    series = Series()
    gradient = QLinearGradient()
    assert configure_surface_series_colors(
        series, color_follows_height=True, gradient=gradient, texture=QImage(),
        range_gradient_style="range", uniform_style="uniform",
    ) == "range-gradient"
    assert ("texture", True) in series.calls
    assert ("gradient", gradient) in series.calls
    assert ("style", "range") in series.calls

    series = Series()
    image = QImage(2, 2, QImage.Format.Format_RGBA8888)
    assert configure_surface_series_colors(
        series, color_follows_height=False, gradient=gradient, texture=image,
        range_gradient_style="range", uniform_style="uniform",
    ) == "texture"
    assert ("texture", False) in series.calls
    assert ("style", "uniform") in series.calls


def test_native_qt_surface_series_accepts_range_gradient_binding(qapp):
    from PySide6.QtDataVisualization import Q3DTheme, QSurface3DSeries
    from PySide6.QtGui import QImage

    from app.visualization3d.renderer import (
        build_surface_gradient, configure_surface_series_colors,
    )

    series = QSurface3DSeries()
    gradient = build_surface_gradient(
        "LabLog BWR", (-36.0, -0.5), (-36.0, -0.5)
    )
    mode = configure_surface_series_colors(
        series,
        color_follows_height=True,
        gradient=gradient,
        texture=QImage(),
        range_gradient_style=Q3DTheme.ColorStyle.ColorStyleRangeGradient,
        uniform_style=Q3DTheme.ColorStyle.ColorStyleUniform,
    )
    assert mode == "range-gradient"
    assert series.colorStyle() == Q3DTheme.ColorStyle.ColorStyleRangeGradient
    assert series.texture().isNull()
    assert len(series.baseGradient().stops()) == 257


def test_pick_readout_uses_original_physical_and_transformed_values():
    from app.visualization3d.data import prepare_surface_grid, prepare_surface_mesh
    from app.visualization3d.renderer import format_pick_readout

    source = SimpleNamespace(
        x_values=np.array([5.019e9, 5.020e9]),
        y_values=np.array([130.4, 131.0]),
        z_values=np.array([[-20.0, -16.84], [-15.0, -9.0]]),
        x_name="Frequency", x_unit="Hz",
        y_name="Average Current", y_unit="mA",
        z_name="VNA - S21", z_unit="dB", transform="magnitude_db",
    )
    grid = prepare_surface_grid(source)
    exaggerated = prepare_surface_mesh(grid, z_scale=5)
    assert exaggerated.coordinates[0, 1, 1] != pytest.approx(grid.z_values[0, 1])
    text = format_pick_readout(grid, 0, 1)
    assert "Frequency: 5.02e+09 Hz" in text
    assert "Average Current: 130.4 mA" in text
    assert "-16.84 dB" in text
    assert "-84.2 dB" not in text


def test_qt_mouse_mapping_disables_native_rotation_but_keeps_zoom_and_pick():
    from app.visualization3d.renderer import configure_surface_input_handler

    class Handler:
        rotation = zoom = selection = False

        def setRotationEnabled(self, enabled):
            self.rotation = enabled

        def setZoomEnabled(self, enabled):
            self.zoom = enabled

        def setSelectionEnabled(self, enabled):
            self.selection = enabled

    handler = Handler()
    configure_surface_input_handler(handler)
    assert not handler.rotation and handler.zoom and handler.selection


def test_surface_mouse_state_machine_separates_click_rotate_and_pan(qapp):
    from PySide6.QtCore import QEvent, QPointF, Qt, QObject

    from app.visualization3d.renderer import _SurfaceInteractionFilter

    class Event:
        def __init__(self, kind, position=(0.0, 0.0), button=Qt.MouseButton.NoButton):
            self._kind = kind
            self._position = QPointF(*position)
            self._button = button
            self.accepted = False

        def type(self):
            return self._kind

        def position(self):
            return self._position

        def button(self):
            return self._button

        def accept(self):
            self.accepted = True

    class Watched:
        def __init__(self):
            self.cursor = None

        def setCursor(self, cursor):
            self.cursor = cursor

        def unsetCursor(self):
            self.cursor = None

    class Renderer(QObject):
        def __init__(self):
            super().__init__()
            self.operations = []

        def _rotate_camera(self, dx, dy):
            self.operations.append(("rotate", dx, dy))

        def _pan_camera(self, dx, dy):
            self.operations.append(("pan", dx, dy))

        def _interaction_start(self):
            pass

        def _interaction_end(self):
            pass

    renderer = Renderer()
    controller = _SurfaceInteractionFilter(renderer)
    viewport = Watched()

    # A short left press remains in Qt's selection/picking pipeline.
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonPress, button=Qt.MouseButton.LeftButton)
    ) is False
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseMove, (3, 3))
    ) is False
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonRelease, button=Qt.MouseButton.LeftButton)
    ) is False
    assert renderer.operations == []

    # Crossing the threshold transfers ownership to rotation and consumes release,
    # so a drag cannot also trigger a scientific point pick.
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonPress, button=Qt.MouseButton.LeftButton)
    ) is False
    assert controller.eventFilter(viewport, Event(QEvent.Type.MouseMove, (10, 0))) is True
    assert renderer.operations == [("rotate", 10.0, 0.0)]
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonRelease, button=Qt.MouseButton.LeftButton)
    ) is True

    # Middle drag is consumed end-to-end and can only call the pan path.
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonPress, button=Qt.MouseButton.MiddleButton)
    ) is True
    assert controller.eventFilter(viewport, Event(QEvent.Type.MouseMove, (12, -4))) is True
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonRelease, button=Qt.MouseButton.MiddleButton)
    ) is True
    assert renderer.operations[-1] == ("pan", 12.0, -4.0)
    assert [item[0] for item in renderer.operations].count("rotate") == 1

    # Wheel and right-button input are left unbound here; native wheel zoom
    # remains enabled, while native rotation has been disabled separately.
    assert controller.eventFilter(viewport, Event(QEvent.Type.Wheel)) is False
    assert controller.eventFilter(
        viewport, Event(QEvent.Type.MouseButtonPress, button=Qt.MouseButton.RightButton)
    ) is False


def test_surface_pan_changes_target_without_changing_camera_orientation(qapp):
    from PySide6.QtGui import QVector3D
    from PySide6.QtDataVisualization import Q3DCamera

    from app.visualization3d.renderer import SurfaceRenderer

    class Container:
        def width(self):
            return 800

        def height(self):
            return 600

    camera = Q3DCamera()
    camera.setCameraPosition(35.0, 25.0, 100.0)
    camera.setTarget(QVector3D(0.1, -0.2, 0.3))
    before_rotation = (camera.xRotation(), camera.yRotation(), camera.zoomLevel())
    before_target = camera.target()
    renderer = SimpleNamespace(
        graph=SimpleNamespace(scene=lambda: SimpleNamespace(activeCamera=lambda: camera)),
        container=Container(), _camera_update_count=0, _world_span=lambda: 1.0,
        _publish_diagnostics=lambda: None,
    )
    SurfaceRenderer._pan_camera(renderer, 30.0, -18.0)
    assert camera.target() != before_target
    assert (camera.xRotation(), camera.yRotation(), camera.zoomLevel()) == before_rotation
    assert renderer._camera_update_count == 1


def test_left_drag_camera_rotation_changes_orientation_only(qapp):
    from PySide6.QtGui import QVector3D
    from PySide6.QtDataVisualization import Q3DCamera

    from app.visualization3d.renderer import SurfaceRenderer

    camera = Q3DCamera()
    camera.setCameraPosition(35.0, 25.0, 140.0)
    camera.setTarget(QVector3D(0.2, 0.1, -0.3))
    before_target = camera.target()
    renderer = SimpleNamespace(
        graph=SimpleNamespace(scene=lambda: SimpleNamespace(activeCamera=lambda: camera)),
        _camera_update_count=0, _publish_diagnostics=lambda: None,
    )
    SurfaceRenderer._rotate_camera(renderer, 12.0, -8.0)
    assert camera.xRotation() == pytest.approx(41.0)
    assert camera.yRotation() == pytest.approx(29.0)
    assert camera.zoomLevel() == pytest.approx(140.0)
    assert camera.target() == before_target
    assert renderer._camera_update_count == 1


def test_surface_help_uses_final_mouse_mapping_in_all_locales():
    from app.localization.catalogs import EN, ZH_TW

    for catalog in (EN, ZH_TW):
        hint = catalog["viewer.surface_pick_hint"]
        assert "Left-drag" in hint or "左鍵拖曳" in hint
        assert "Middle-drag" in hint or "中鍵拖曳" in hint
        assert "Wheel" in hint or "滾輪" in hint
        assert "Right-drag: Rotate" not in hint
        assert "右鍵拖曳：旋轉" not in hint


def test_tag_value_buttons_are_not_localized_but_categories_are(qapp, tmp_path):
    from app.core.tag_store import TagStore
    from app.gui.tag_assignment_dialog import TagAssignmentDialog
    from app.localization import LocalizationManager
    from app.settings.store import SettingsStore

    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    localizer.install(qapp)
    tags = TagStore(tmp_path / "tags.json", legacy_paths=[])
    tags.create_tag("好數據", "Other")
    dialog = TagAssignmentDialog("sample", tags, set())
    dialog.show()
    qapp.processEvents()
    localizer.set_language("zh_TW")
    assert dialog.tag_checkboxes["Good Data"].text() == "Good Data"
    assert dialog.tag_checkboxes["Debug"].text() == "Debug"
    assert dialog.tag_checkboxes["好數據"].text() == "好數據"
    assert dialog.checkbox_layout.itemAt(0).widget().title() == "專案"
    localizer.set_language("en")
    assert dialog.tag_checkboxes["好數據"].text() == "好數據"
    assert dialog.tag_checkboxes["Good Data"].text() == "Good Data"
    dialog.close()


def test_category_combo_keeps_canonical_tag_category_id(qapp, tmp_path):
    from app.core.tag_store import TagStore
    from app.gui.tag_management_dialog import TagManagementDialog
    from app.localization import LocalizationManager
    from app.settings.store import SettingsStore

    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    localizer.install(qapp)
    tags = TagStore(tmp_path / "tags.json", legacy_paths=[])
    dialog = TagManagementDialog(tags)
    dialog.show()
    qapp.processEvents()
    localizer.set_language("zh_TW")
    index = dialog.category_combo.findData("Project")
    assert index >= 0
    dialog.category_combo.setCurrentIndex(index)
    assert dialog.category_combo.currentData() == "Project"
    from app.localization.manager import translate_for_display

    assert translate_for_display(dialog.category_combo.itemText(index)) == "專案"   # shown in Chinese
    dialog.close()


def test_settings_easter_eggs_about_and_debug_developer_identity(qapp, tmp_path):
    from app import __version__
    from app.localization import LocalizationManager
    from app.settings.debug_info import DebugPage
    from app.settings.dialog import SettingsDialog
    from app.settings.store import SettingsStore

    localizer = LocalizationManager(SettingsStore(tmp_path / "settings.json"))
    dialog = SettingsDialog(localizer)
    assert dialog.sections.count() == 6
    assert [dialog.sections.item(index).text() for index in range(dialog.sections.count())] == [
        "General", "Appearance", "Personal", "3D", "Debug", "About",
    ]
    assert dialog.traditional_chinese.text() == "中文（繁體）「不好好練一下英文」"
    assert f"Version v{__version__}" in dialog.about_content.text()
    assert "戦わずに恋をする" in dialog.about_content.text()
    assert "張譯文" in dialog.about_content.text()

    dialog.traditional_chinese.setChecked(True)
    assert localizer.language == "zh_TW"
    assert dialog.english.text() == "English「按下去我欣賞你」"
    assert dialog.traditional_chinese.text() == "中文（繁體）"
    assert SettingsStore(tmp_path / "settings.json").language() == "zh_TW"

    page = DebugPage(localizer)
    report = page.output.toPlainText()
    assert "開發者: 戦わずに恋をする" in report
    assert "共同開發者: 張譯文" in report
    dialog.english.setChecked(True)
    assert localizer.language == "en"
    assert dialog.traditional_chinese.text() == "中文（繁體）「不好好練一下英文」"
    assert SettingsStore(tmp_path / "settings.json").language() == "en"
    page.close()
    dialog.close()
