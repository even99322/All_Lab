import sys
import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_monitor_finds_the_busy_function_and_the_stall(qapp):
    from app.core.perf_monitor import PerformanceMonitor

    import sys
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.0005)          # let the sampler thread win the GIL during the busy loop
    monitor = PerformanceMonitor()
    monitor.start()

    def busy_work():                       # stays on the UI thread for ~1 s
        end = time.perf_counter() + 1.0
        while time.perf_counter() < end:
            sum(range(2000))  # noqa

    busy_work()
    end = time.perf_counter() + 6.0
    while time.perf_counter() < end and not monitor.recent_stalls():
        QApplication.processEvents()
        time.sleep(0.02)
    monitor.stop()
    sys.setswitchinterval(previous_interval)
    places = [place for place, _share in monitor.top_places()]
    assert any("busy_work" in place for place in places), places
    assert any("busy_work" in stall.where and stall.seconds >= 0.15 for stall in monitor.recent_stalls()), \
        (monitor.top_places(6), monitor.total_samples, monitor._thread.is_alive())
    assert "UI thread" in monitor.report()


def test_monitor_records_lag_and_resource_samples(qapp):
    from app.core.perf_monitor import PerformanceMonitor

    monitor = PerformanceMonitor()
    monitor.start()
    end = time.perf_counter() + 8.0
    while time.perf_counter() < end and len(monitor.samples) < 3:
        QApplication.processEvents()
        time.sleep(0.01)
    monitor.stop()
    assert len(monitor.samples) >= 3
    assert all(s.widgets >= 0 and s.threads >= 1 for s in monitor.samples)


def test_performance_window_opens_and_closes(qapp):
    from app.gui.performance_window import PerformanceWindow

    window = PerformanceWindow()
    window.show()
    end = time.perf_counter() + 0.8
    while time.perf_counter() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert "UI lag" in window.summary.text()
    window.close()
    assert not window.monitor.running


def test_rss_is_readable():
    from app.core.perf_monitor import current_rss_mb

    assert current_rss_mb() > 10


def test_shortcut_update_only_touches_our_own_shortcuts(tmp_path, monkeypatch):
    from app.gui import windows_shortcuts as ws

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / ws.SHORTCUT_NAME).write_bytes(b"x")
    commands = []

    def fake(script):
        commands.append(script)
        return "changed"

    monkeypatch.setattr(ws, "_powershell", fake)
    monkeypatch.setattr(ws, "refresh_shell", lambda: commands.append("refresh"))
    done = ws.update_shortcut_icons(2, folders=[desktop, tmp_path / "missing"], program=r"C:\x\LabLogViewer.exe")
    assert done == [str(desktop / ws.SHORTCUT_NAME)]
    assert "app_icon_2.ico" in commands[0] and "TargetPath -ieq" in commands[0]
    assert commands[-1] == "refresh"


def test_every_app_icon_has_an_ico():
    from app.gui import app_icon

    for number in range(1, app_icon.COUNT + 1):
        assert app_icon.icon_path(number).with_suffix(".ico").is_file()


def test_installer_choice_is_the_default_icon(tmp_path, monkeypatch):
    from app.settings.store import SettingsStore

    (tmp_path / "app_icon.txt").write_text("2", encoding="ascii")
    fake_exe = tmp_path / "LabLogViewer.exe"
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert SettingsStore(tmp_path / "settings.json").app_icon() == 2


def test_point_graph_stops_fps_measuring_and_follows_plot_appearance():
    import inspect
    from app.visualization3d.renderer import SurfaceRenderer

    fps = inspect.getsource(SurfaceRenderer._on_point_fps_changed)
    ensure = inspect.getsource(SurfaceRenderer._ensure_scatter_graph)
    assert "setMeasureFps(False)" in fps
    assert "apply_scientific_plot_appearance(self._plot_colors)" in ensure
