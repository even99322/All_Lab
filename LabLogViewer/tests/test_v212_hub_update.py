"""v2.1.2: updates from the lab platform hub — feed, push (long poll), "Update now", "Check for updates"."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

INSTALLER = b"MZ fake LabLogViewer installer " * 1000


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


class FakeHub:
    """/get/feed/lablogviewer.json, /get/watch (long poll) and the installers, like the platform hub."""

    def __init__(self, version="9.0.0"):
        self.version = version
        self.cond = threading.Condition()
        self.requests: list[str] = []
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, data):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                hub.requests.append(self.path)
                if self.path.startswith("/get/feed/lablogviewer.json"):
                    return self._json(hub.feed())
                if self.path.startswith("/get/watch"):
                    from urllib.parse import parse_qs, urlparse

                    q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                    from app.core.app_update import version_key

                    end = time.monotonic() + float(q.get("wait", 0))
                    with hub.cond:
                        while version_key(hub.version) <= version_key(q["version"]) and time.monotonic() < end:
                            hub.cond.wait(end - time.monotonic())
                        changed = version_key(hub.version) > version_key(q["version"])
                    return self._json({"changed": changed, "app": {"version": hub.version}})
                if self.path.startswith("/get/lablogviewer/"):
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(INSTALLER)))
                    self.end_headers()
                    self.wfile.write(INSTALLER)
                    return
                self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def feed(self):
        v = self.version
        dl = lambda plat, arch, name: {"name": name, "platform": plat, "arch": arch, "version": v,
                                        "size": len(INSTALLER), "sha256": hashlib.sha256(INSTALLER).hexdigest(),
                                        "url": f"/get/lablogviewer/{v}/{name}"}
        return {"version": v, "date": "2026-10-11",
                "items": [{"category": "improved", "zh": "推播更新", "en": "Push updates"}],
                "downloads": [dl("macos", "x86_64", f"LabLogViewer_v{v}_macos-x86_64.dmg"),
                              dl("macos", "arm64", f"LabLogViewer_v{v}_macos-arm64.dmg"),
                              dl("windows", "x86_64", f"LabLogViewer_v{v}_windows-setup.exe")]}

    def publish(self, version):
        with self.cond:
            self.version = version
            self.cond.notify_all()

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def hub(monkeypatch):
    h = FakeHub()
    monkeypatch.setenv("QEL_HUB", h.url)
    yield h
    h.close()


def test_hub_addresses_default_to_the_lab_and_the_vpn(monkeypatch):
    from app.core import hub_update

    monkeypatch.delenv("QEL_HUB", raising=False)
    monkeypatch.delenv("QEL_GATEWAY", raising=False)
    assert hub_update.hub_urls() == ["http://192.168.50.2:8765", "http://100.114.33.20:8765"]
    monkeypatch.setenv("QEL_HUB", "10.0.0.5:8765, http://h:1/")
    assert hub_update.hub_urls() == ["http://10.0.0.5:8765", "http://h:1"]


def test_pick_download_prefers_this_computer():
    from app.core.hub_update import pick_download

    dls = [{"platform": "macos", "arch": "x86_64", "name": "x"}, {"platform": "macos", "arch": "universal2",
                                                                    "name": "u"},
           {"platform": "macos", "arch": "arm64", "name": "a"}, {"platform": "windows", "arch": "x86_64", "name": "w"}]
    assert pick_download(dls, "macos", "arm64")["name"] == "a"
    assert pick_download(dls[:2], "macos", "arm64")["name"] == "u"
    assert pick_download(dls[:1], "macos", "arm64")["name"] == "x"
    assert pick_download(dls, "windows", "x86_64")["name"] == "w"
    assert pick_download(dls, "linux", "x86_64") is None


def test_feed_carries_the_installer_with_a_full_address(hub, monkeypatch):
    from app.core import hub_update

    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    info, dl = hub_update.read_hub_feed()
    assert info.version == "9.0.0" and info.items
    assert dl["url"] == f"{hub.url}/get/lablogviewer/9.0.0/LabLogViewer_v9.0.0_windows-setup.exe"
    assert dl["hub"] == hub.url and dl["size"] == len(INSTALLER)


def test_unreachable_hub_raises(monkeypatch):
    from app.core import hub_update

    with pytest.raises(hub_update.HubUpdateError):
        hub_update.read_hub_feed(timeout=0.5, bases=["http://127.0.0.1:9"])


def test_watch_answers_as_soon_as_a_new_version_is_published(hub):
    from app.core import hub_update

    assert hub_update.watch("9.0.0", wait=0.3) is None                 # nothing newer: times out
    threading.Timer(0.4, hub.publish, args=("9.0.1",)).start()
    t0 = time.monotonic()
    info, _dl = hub_update.watch("9.0.0", wait=20)
    assert info.version == "9.0.1" and time.monotonic() - t0 < 5     # pushed, not after 20 s


def test_notify_adds_one_update_notice_with_the_installer(hub, monkeypatch):
    from app.core import hub_update, notifications

    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    info, dl = hub_update.read_hub_feed()
    record = hub_update.notify(info, dl, "2.1.2")
    assert record["kind"] == "update" and record["params"]["version"] == "9.0.0"
    assert record["details"]["install"]["sha256"] == hashlib.sha256(INSTALLER).hexdigest()
    assert hub_update.notify(info, dl, "2.1.2") is None                # announced once
    assert hub_update.notify(info, dl, "9.0.0") is None                # not newer
    assert len([r for r in notifications.items() if r["kind"] == "update"]) == 1


def test_can_install_only_when_packaged_and_for_this_computer(monkeypatch):
    from app.core import hub_update

    details = {"install": {"url": "http://h/x.exe", "platform": "windows"}}
    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    monkeypatch.setattr(hub_update, "FROZEN", False)
    assert not hub_update.can_install(details)
    monkeypatch.setattr(hub_update, "FROZEN", True)
    assert hub_update.can_install(details)
    assert not hub_update.can_install({"install": {"url": "http://h/x.dmg", "platform": "macos"}})
    assert not hub_update.can_install({})


def test_download_checks_size_and_checksum(hub, monkeypatch):
    from app.core import hub_update

    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    _info, dl = hub_update.read_hub_feed()
    seen = []
    path = hub_update.download(dl, lambda d, t: seen.append((d, t)))
    assert path.read_bytes() == INSTALLER and path.name == "LabLogViewer_v9.0.0_windows-setup.exe"
    assert seen[-1] == (len(INSTALLER), len(INSTALLER))
    with pytest.raises(hub_update.HubUpdateError):
        hub_update.download(dict(dl, sha256="0" * 64))
    with pytest.raises(hub_update.HubUpdateError):
        hub_update.download(dict(dl, size=len(INSTALLER) + 1))


def test_windows_install_runs_the_installer_silently_then_reopens(tmp_path, monkeypatch):
    from app.core import hub_update

    started = []
    monkeypatch.setattr(hub_update.subprocess, "Popen", lambda args, **kw: started.append(args))
    setup = tmp_path / "LabLogViewer_v9.0.0_windows-setup.exe"
    setup.write_bytes(b"x")
    script = hub_update.install_windows(setup, Path(r"C:\Users\lab\AppData\Local\Programs\LabLogViewer\LabLogViewer.exe"))
    text = script.read_text(encoding="utf-8")
    assert f'"{setup}" /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NOCANCEL' in text
    assert text.index("tasklist") < text.index("/SILENT") < text.index('start ""')
    assert "LabLogViewer.exe" in text.splitlines()[-1]
    assert started == [["cmd.exe", "/c", str(script)]]


def test_install_refuses_when_running_from_python(monkeypatch):
    from app.core import hub_update

    monkeypatch.setattr(hub_update, "FROZEN", False)
    with pytest.raises(hub_update.HubUpdateError):
        hub_update.install({"url": "http://h/x.exe"})


def test_update_preview_offers_update_now_for_an_installable_notice(qapp, localizer, hub, monkeypatch):
    from app.core import hub_update
    from app.gui import hub_updates
    from app.gui.notification_bell import UpdatePreviewDialog

    monkeypatch.setattr(hub_update, "this_platform", lambda: "windows")
    info, dl = hub_update.read_hub_feed()
    record = hub_update.notify(info, dl, "2.1.2")
    monkeypatch.setattr(hub_update, "FROZEN", False)
    assert UpdatePreviewDialog(localizer, record).findChild(object, "updateNowButton") is None
    monkeypatch.setattr(hub_update, "FROZEN", True)
    dialog = UpdatePreviewDialog(localizer, record)
    button = dialog.update_button
    assert button is not None and button.objectName() == "updateNowButton" and button.text() == "Update now"
    calls = []
    monkeypatch.setattr(hub_updates, "install_now", lambda parent, loc, details: calls.append(details) or True,
                        raising=True)
    button.click()
    assert calls and calls[0]["version"] == "9.0.0" and calls[0]["install"]["platform"] == "windows"


def test_install_now_downloads_installs_and_quits(qapp, localizer, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.core import hub_update
    from app.gui import hub_updates

    done = []

    def fake_install(info, progress):
        progress(5, 10)
        progress(10, 10)
        done.append(info)
        return "windows"

    monkeypatch.setattr(hub_update, "install", fake_install)
    shown, quits = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(("warn", a[2])))
    monkeypatch.setattr(QApplication, "quit", lambda: quits.append(1))
    assert hub_updates.install_now(None, localizer, {"version": "9.0.0", "install": {"url": "u"}})
    assert done == [{"url": "u"}] and quits == [1] and "9.0.0" in shown[-1]

    def broken(info, progress):
        raise hub_update.HubUpdateError("disk full")

    monkeypatch.setattr(hub_update, "install", broken)
    assert not hub_updates.install_now(None, localizer, {"version": "9.0.0", "install": {"url": "u"}})
    assert shown[-1][0] == "warn" and "disk full" in shown[-1][1] and quits == [1]


def test_check_for_updates_says_latest_unreachable_or_shows_the_update(qapp, localizer, hub, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from app.gui import hub_updates
    from app.gui import notification_bell

    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: said.append(a[2]))
    hub_updates.check_now(None, localizer, "9.0.0")
    assert said[-1] == "You have the latest version (9.0.0)."
    opened = []
    monkeypatch.setattr(notification_bell.UpdatePreviewDialog, "exec", lambda self: opened.append(self.record))
    hub_updates.check_now(None, localizer, "2.1.2")
    hub_updates.check_now(None, localizer, "2.1.2")                    # second time: the same notice again
    assert [r["params"]["version"] for r in opened] == ["9.0.0", "9.0.0"]
    monkeypatch.setenv("QEL_HUB", "http://127.0.0.1:9")
    hub_updates.check_now(None, localizer, "2.1.2")
    assert said[-1].startswith("The lab hub cannot be reached")


def test_settings_about_has_check_for_updates(qapp, localizer, monkeypatch):
    from app.gui import hub_updates
    from app.settings.dialog import SettingsDialog

    calls = []
    monkeypatch.setattr(hub_updates, "check_now", lambda parent, loc, version: calls.append(version))
    dialog = SettingsDialog(localizer)
    assert dialog.check_update_button.text() == "Check for updates"
    dialog.check_update_button.click()
    from app import __version__

    assert calls == [__version__]
    localizer.set_language("zh_TW")
    assert dialog.check_update_button.text() == "檢查更新"


def test_watcher_pushes_a_notice_into_the_bell(qapp, hub, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from app.core import notifications
    from app.gui.hub_updates import HubWatcher

    import app.core.hub_update as hu

    hub.version = "2.1.2"
    real_watch = hu.watch
    monkeypatch.setattr(hu, "watch", lambda version, wait=60.0, bases=None: real_watch(version, 2.0, bases))
    watcher = HubWatcher("2.1.2")
    watcher.start()
    time.sleep(0.5)
    assert not [r for r in notifications.items() if r["kind"] == "update"]
    hub.publish("2.1.3")
    end = time.monotonic() + 10
    while time.monotonic() < end and not [r for r in notifications.items() if r["kind"] == "update"]:
        QApplication.processEvents()
        time.sleep(0.05)
    watcher.stop()
    records = [r for r in notifications.items() if r["kind"] == "update"]
    assert [r["params"]["version"] for r in records] == ["2.1.3"]


def test_start_is_off_in_tests_and_with_no_hub(qapp, monkeypatch):
    from app.gui import hub_updates

    monkeypatch.setattr(hub_updates, "_WATCHER", None)
    assert hub_updates.start("2.1.2") is None                         # LABLOGVIEWER_NO_UPDATE_CHECK (conftest)
    monkeypatch.delenv("LABLOGVIEWER_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("LABLOGVIEWER_NO_HUB", "1")
    assert hub_updates.start("2.1.2") is None

