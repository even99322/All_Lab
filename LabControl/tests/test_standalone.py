"""0.0.26：量測、讀檔各自是獨立程式（不用啟動器）——程式內更新、開機自動開啟、直接開讀檔程式、自我檢查。"""
from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from labcontrol import __version__, selfupdate


def _serve(routes):
    """小型 HTTP 伺服器：{路徑: (content-type, bytes)}。"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path not in routes:
                self.send_error(404)
                return
            ctype, body = routes[self.path]
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _info(ver="9.9.9", plat="windows", arch="", body=b"NEWEXE"):
    name = f"LabControl_v{ver}_{'windows.exe' if plat == 'windows' else 'macos-universal.dmg'}"
    return {"apps": [{"id": "labcontrol", "downloads": [
        {"name": name, "platform": plat, "arch": arch, "size": len(body), "version": ver, "notes": "新功能",
         "url": f"/get/labcontrol/{ver}/{name}"}]}, {"id": "lablogviewer", "downloads": []}]}, name


def test_pick_prefers_same_chip_then_universal():
    dl = [{"platform": "macos", "arch": "x86_64", "version": "1.0.1"},
          {"platform": "macos", "arch": "universal2", "version": "1.0.1"},
          {"platform": "macos", "arch": "arm64", "version": "1.0.0"},
          {"platform": "windows", "arch": "", "version": "1.0.1"}]
    assert selfupdate.pick(dl, "macos", "arm64")["arch"] == "arm64"
    assert selfupdate.pick(dl, "macos", "x86_64")["arch"] == "x86_64"
    assert selfupdate.pick(dl[1:2], "macos", "arm64")["arch"] == "universal2"
    assert selfupdate.pick(dl, "windows")["version"] == "1.0.1"
    assert selfupdate.pick(dl, "linux") is None


def test_latest_and_windows_update_replaces_running_exe(tmp_path, monkeypatch):
    info, name = _info()
    srv, url = _serve({"/get/info": ("application/json", json.dumps(info).encode()),
                       f"/get/labcontrol/9.9.9/{name}": ("application/octet-stream", b"NEWEXE")})
    try:
        monkeypatch.setattr(selfupdate, "this_platform", lambda: "windows")
        got = selfupdate.latest(bases=["http://127.0.0.1:1", url])          # 第一個連不到 → 試下一個（VPN）
        assert got["version"] == "9.9.9" and got["url"].startswith(url) and selfupdate.is_newer(got)
        assert not selfupdate.is_newer(dict(got, version=__version__))
        exe = tmp_path / "Lab" / "LabControl.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"OLDEXE")
        monkeypatch.setattr(selfupdate, "FROZEN", True)
        monkeypatch.setattr(selfupdate, "current_executable", lambda: exe)
        launched = []
        monkeypatch.setattr(selfupdate.subprocess, "Popen", lambda cmd, **kw: launched.append((cmd, kw)))
        prog = []
        new = selfupdate.update_to(got, lambda d, t: prog.append((d, t)), args=["--node", "--minimized"])
        assert new == exe and exe.read_bytes() == b"NEWEXE"                  # 同一個路徑：捷徑、開機啟動不用改
        assert (exe.parent / "LabControl.old.exe").read_bytes() == b"OLDEXE"
        assert launched[0][0] == [str(exe), "--node", "--minimized"]
        assert launched[0][1]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        assert prog and prog[-1] == (6, 6)
        selfupdate.cleanup_after_update()                                   # 下次開啟：清掉舊檔
        assert not (exe.parent / "LabControl.old.exe").exists()
        assert not list(exe.parent.glob("*.download"))
    finally:
        srv.shutdown()


def test_incomplete_download_is_refused(monkeypatch):
    info, name = _info(body=b"NEWEXE")
    f = info["apps"][0]["downloads"][0]
    srv, url = _serve({f["url"]: ("application/octet-stream", b"NEW")})        # 只有 3 bytes，平台記錄 6 bytes
    try:
        monkeypatch.setattr(selfupdate, "this_platform", lambda: "linux")
        with pytest.raises(RuntimeError, match="不完整"):
            selfupdate.download(dict(f, url=url + f["url"]))
    finally:
        srv.shutdown()


def test_python_version_never_replaces_itself(monkeypatch):
    monkeypatch.setattr(selfupdate, "FROZEN", False)
    with pytest.raises(RuntimeError, match="Python"):
        selfupdate.update_to({"version": "9.9.9", "name": "x", "url": "http://x"})


def test_autostart_linux(tmp_path, monkeypatch):
    from labcontrol import autostart

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    old = tmp_path / "autostart" / "qelplatform.desktop"
    old.parent.mkdir(parents=True)
    old.write_text("old launcher")
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    text = (tmp_path / "autostart" / "labcontrol.desktop").read_text(encoding="utf-8")
    assert "--node" in text and "--minimized" in text and autostart.is_enabled()
    assert not old.exists()                                              # 舊啟動器不再自動開啟
    autostart.set_enabled(False)
    assert not autostart.is_enabled()


def test_reader_open_path(tmp_path, monkeypatch):
    from labcontrol.integrations import reader

    monkeypatch.setattr(reader, "candidates", lambda: [tmp_path / "nope.exe"])
    with pytest.raises(reader.ReaderMissing, match="LabLogViewer"):
        reader.open_path(tmp_path)
    exe = tmp_path / "LabLogViewer.exe"
    exe.write_text("")
    monkeypatch.setattr(reader, "candidates", lambda: [tmp_path / "nope.exe", exe])
    calls = []
    monkeypatch.setattr(reader.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(reader.sys, "platform", "win32")
    assert reader.open_path(tmp_path / "a.nc") == exe and calls[-1] == [str(exe), str(tmp_path / "a.nc")]
    app = tmp_path / "LabLogViewer.app"
    app.mkdir()
    monkeypatch.setattr(reader, "candidates", lambda: [app])
    monkeypatch.setattr(reader.sys, "platform", "darwin")
    reader.open_path(tmp_path)
    assert calls[-1] == ["open", "-n", "-a", str(app), "--args", str(tmp_path)]


def test_self_test_passes():
    from labcontrol.self_test import run

    assert run() == 0


# ---- 主視窗 -------------------------------------------------------------------------------------
@pytest.fixture
def win(station):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt.workbench import LabControlWindow
    from labcontrol.scheme import Scheme

    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = LabControlWindow(station, Scheme("t"))
    w.show()
    yield w
    w.doc.dirty = False
    w.close()


def _pump(cond, s=5.0):
    from PyQt6 import QtWidgets

    t = time.time()
    while time.time() - t < s:
        QtWidgets.QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def _check(plan="code", ver="9.9.9", size=900_000):
    t = {"version": ver, "name": f"LabControl_v{ver}.zip", "url": "http://hub/x.zip", "size": size, "notes": "- 新功能 A\n- 修正 B"}
    return {"hub": "http://hub", "app": {}, "version": ver, "installer": None, "code": t, "plan": plan, "target": t,
            "notes": t["notes"], "current": __version__}


def test_push_shows_toast_button_and_version_dialog(win, monkeypatch):
    from PyQt6 import QtWidgets

    from labcontrol.apps.qt import updates

    ctl = win.updates
    ctl.found.emit(_check())                                            # 推播（背景長輪詢收到新版）
    assert _pump(lambda: ctl.toast.isVisible())
    assert "9.9.9" in ctl.toast.title.text() and "新功能 A" in ctl.toast.body.text() and "功能更新包" in ctl.toast.body.text()
    assert ctl.button.isVisible() and ctl.button.text() == "⬆ 更新到 9.9.9"
    ctl.toast.hide()
    ctl.found.emit(_check())                                            # 同一版只通知一次
    QtWidgets.QApplication.processEvents()
    assert not ctl.toast.isVisible()
    # 版本檢查視窗：目前版本、Hub 最新、更新方式、更新內容
    monkeypatch.setattr(updates.selfupdate, "check", lambda: _check(plan="wait"))
    dlg = updates.VersionDialog(ctl, win)
    assert _pump(lambda: dlg.latest.text() == "9.9.9" and "執行環境" in dlg.how.text())
    assert dlg.cur.text().startswith(__version__) and not dlg.b_update.isEnabled()
    assert "新功能 A" in dlg.notes.toPlainText()
    dlg.close()
    # 立即更新：量測中不更新；否則套用並結束（這裡攔下 apply 與 quit）
    applied = []
    monkeypatch.setattr(updates.selfupdate, "apply", lambda c, progress=None: applied.append(c["plan"]) or "code")
    monkeypatch.setattr(QtWidgets.QApplication, "quit", lambda: applied.append("quit"))
    ctl.result = _check()
    ctl.update_now(confirm=False)
    assert _pump(lambda: applied[-1:] == ["quit"])
    assert applied[0] == "code"


def test_watch_loop_emits(win, monkeypatch):
    from labcontrol.apps.qt import updates

    calls = []

    def fake_watch(version, wait=60):
        calls.append(version)
        if len(calls) == 1:
            return _check(ver="9.9.8")
        win.updates._stop.wait(5)
        return None
    monkeypatch.setattr(updates.selfupdate, "watch", fake_watch)
    win.updates._stop.clear()
    import threading

    threading.Thread(target=win.updates._watch_loop, daemon=True).start()
    assert _pump(lambda: win.updates.toast.isVisible() and "9.9.8" in win.updates.toast.title.text())
    win.updates.stop()


def test_portal_messages_open_reader(win, monkeypatch, tmp_path):
    """平台網頁的「在讀檔程式開啟」「開啟專案」送到量測程式：直接開這台電腦的 LabLogViewer。"""
    from labcontrol.integrations import reader

    f = tmp_path / "run.nc"
    f.write_bytes(b"x")
    opened = []
    monkeypatch.setattr(reader, "open_path", lambda p: opened.append(Path(p)) or Path("LabLogViewer.exe"))
    win.platform.handle_message({"type": "open_in", "module": "lablogviewer", "path": str(f)})
    assert opened == [f]
    win.platform.handle_message({"type": "open_in", "path": str(f), "host": "another-pc"})   # 指定給別台
    assert opened == [f]


# ---- 功能更新包（只換程式碼）-------------------------------------------------------------------------
def _code_zip(ver, requirements, extra=None):
    import io
    import zipfile

    root = Path(__file__).resolve().parents[1]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("LabControl/requirements.txt", requirements)
        z.writestr("LabControl/labcontrol/__init__.py",
                   (root / "labcontrol" / "__init__.py").read_text(encoding="utf-8").replace(
                       f'__version__ = "{__version__}"', f'__version__ = "{ver}"') + "\nOVERLAY = True\n")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


def test_plan_prefers_code_package_when_runtime_matches(monkeypatch):
    monkeypatch.setattr(selfupdate, "FROZEN", True)
    monkeypatch.setattr(selfupdate, "runtime", lambda: {"version": "0.0.26", "requirements": "abc"})
    monkeypatch.setattr(selfupdate, "this_platform", lambda: "windows")
    app = {"version": "0.0.30", "downloads": [{"platform": "windows", "arch": "", "version": "0.0.28", "url": "/e",
                                               "name": "e.exe", "size": 150e6}],
           "code": {"version": "0.0.30", "requirements": "abc", "url": "/c", "name": "c.zip", "size": 9e5}}
    c = selfupdate.plan_for(app, "http://hub", "0.0.26")
    assert c["plan"] == "code" and c["target"]["version"] == "0.0.30" and c["code"]["url"] == "http://hub/c"
    app["code"]["requirements"] = "NEW"                                  # 加了新套件：要換整個程式
    c = selfupdate.plan_for(app, "http://hub", "0.0.26")
    assert c["plan"] == "installer" and c["target"]["version"] == "0.0.28"
    c = selfupdate.plan_for(app, "http://hub", "0.0.28")                 # 安裝檔也沒有新的：等站長上傳
    assert c["plan"] == "wait" and c["target"]["version"] == "0.0.30"
    assert selfupdate.plan_for(app, "http://hub", "0.0.30")["plan"] is None
    monkeypatch.setattr(selfupdate, "FROZEN", False)
    assert selfupdate.plan_for(app, "http://hub", "0.0.26")["plan"] == "python"


def test_apply_code_extracts_and_checks(tmp_path, monkeypatch):
    import hashlib

    monkeypatch.setenv("LABCONTROL_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setattr(selfupdate, "FROZEN", True)
    monkeypatch.setattr(selfupdate, "runtime", lambda: {"requirements": selfupdate.requirements_fingerprint("numpy\n")})
    data = _code_zip("9.9.9", "numpy  # same\n")
    bad = _code_zip("9.9.10", "numpy\nnewpkg\n")
    srv, url = _serve({"/c.zip": ("application/zip", data), "/bad.zip": ("application/zip", bad)})
    try:
        info = {"version": "9.9.9", "name": "c.zip", "url": url + "/c.zip", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
        d = selfupdate.apply_code(info)
        assert d == tmp_path / "code" / "9.9.9" and (d / "labcontrol" / "__init__.py").exists()
        assert json.loads((d / ".complete").read_text())["requirements"] == selfupdate.requirements_fingerprint("numpy")
        with pytest.raises(RuntimeError, match="檢查碼"):
            selfupdate.apply_code(dict(info, sha256="0" * 64))
        with pytest.raises(RuntimeError, match="不同的套件"):
            selfupdate.apply_code({"version": "9.9.10", "name": "bad.zip", "url": url + "/bad.zip", "size": len(bad)})
        with pytest.raises(RuntimeError, match="版本"):
            selfupdate.apply_code(dict(info, version="9.9.11"))
        assert sorted(p.name for p in (tmp_path / "code").iterdir()) == ["9.9.9"]
    finally:
        srv.shutdown()


_BOOT = r"""
import json, os, sys
sys.frozen = True
sys._MEIPASS = sys.argv[1]
sys.argv = ["LabControl"]
os.chdir(sys.argv[0] and os.path.dirname(os.path.abspath(__file__)) or ".")
sys.path[:] = [p for p in sys.path if p not in ("", os.getcwd())]
exec(compile(open(os.environ["MAIN_PY"], encoding="utf-8").read().replace("from labcontrol.app import main", "import labcontrol as main"), "main.py", "exec"), {"__name__": "boot"})
import labcontrol
print(json.dumps({"version": labcontrol.__version__, "overlay": getattr(labcontrol, "OVERLAY", False),
                  "file": labcontrol.__file__, "base": os.environ.get("LABCONTROL_BASE_VERSION")}))
"""


def _boot(tmp_path, env_extra=None):
    import os
    import subprocess
    import sys as _sys

    root = Path(__file__).resolve().parents[1]
    mei = tmp_path / "mei"
    mei.mkdir(exist_ok=True)
    (mei / "runtime.json").write_text(json.dumps({"version": __version__, "requirements": "abc"}))
    script = tmp_path / "boot.py"
    script.write_text(_BOOT, encoding="utf-8")
    env = dict(os.environ, MAIN_PY=str(root / "main.py"), LABCONTROL_CODE_DIR=str(tmp_path / "code"),
               PYTHONPATH=str(root), **(env_extra or {}))
    r = subprocess.run([_sys.executable, str(script), str(mei)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _install(tmp_path, ver, req="abc", body=""):
    import shutil

    root = Path(__file__).resolve().parents[1]
    d = tmp_path / "code" / ver / "labcontrol"
    shutil.copytree(root / "labcontrol", d, ignore=shutil.ignore_patterns("__pycache__"))
    (d / "__init__.py").write_text((root / "labcontrol" / "__init__.py").read_text(encoding="utf-8").replace(
        f'__version__ = "{__version__}"', f'__version__ = "{ver}"') + "\nOVERLAY = True\n" + body, encoding="utf-8")
    (tmp_path / "code" / ver / ".complete").write_text(json.dumps({"version": ver, "requirements": req}))


def test_boot_uses_code_package_and_falls_back(tmp_path):
    """main.py（打包版的入口）：較新的功能更新包放到打包內容前面；執行環境不同、或上次沒開成功的不用。"""
    r = _boot(tmp_path)
    assert r["version"] == __version__ and not r["overlay"] and r["base"] == __version__
    _install(tmp_path, "9.9.1")
    _install(tmp_path, "9.9.2", req="other")                              # 執行環境不同：跳過
    r = _boot(tmp_path)
    assert r["version"] == "9.9.1" and r["overlay"] and "9.9.1" in r["file"]
    state = json.loads((tmp_path / "code" / "state.json").read_text())
    assert state["trial"] == "9.9.1" and state["trial_at"]               # 還沒回報開啟成功
    assert _boot(tmp_path)["version"] == "9.9.1"                         # 30 秒內又開一次（同時開兩個）：先不判定
    state = json.loads((tmp_path / "code" / "state.json").read_text())
    state["trial_at"] -= 60
    (tmp_path / "code" / "state.json").write_text(json.dumps(state))
    r = _boot(tmp_path)                                                   # 上次沒開成功 → 不再用 9.9.1
    assert r["version"] == __version__ and not r["overlay"]
    assert "9.9.1" in json.loads((tmp_path / "code" / "state.json").read_text())["bad"]
    _install(tmp_path, "9.9.3")
    r = _boot(tmp_path)
    assert r["version"] == "9.9.3"
    assert json.loads((tmp_path / "code" / "state.json").read_text())["trial"] == "9.9.3"
    import os

    os.environ["LABCONTROL_CODE_VERSION"] = "9.9.3"                       # 主視窗開起來 → mark_good
    os.environ["LABCONTROL_CODE_DIR"] = str(tmp_path / "code")
    try:
        selfupdate.mark_good()
    finally:
        del os.environ["LABCONTROL_CODE_VERSION"], os.environ["LABCONTROL_CODE_DIR"]
    st = json.loads((tmp_path / "code" / "state.json").read_text())
    assert st["good"] == "9.9.3" and not st["trial"]
    assert _boot(tmp_path)["version"] == "9.9.3"                         # 之後一直用它
    assert _boot(tmp_path, {"LABCONTROL_NO_OVERLAY": "1"})["version"] == __version__     # 救援


def test_boot_broken_code_package_falls_back_immediately(tmp_path):
    _install(tmp_path, "9.9.5", body="raise ImportError('需要 .exe 沒有的模組')\n")
    r = _boot(tmp_path)
    assert r["version"] == __version__ and not r["overlay"]
    st = json.loads((tmp_path / "code" / "state.json").read_text())
    assert "9.9.5" in st["bad"] and "ImportError" in st["last_error"]


def test_windows_setup_update_runs_the_installer_after_exit(tmp_path, monkeypatch):
    """0.0.28：Windows 版改用安裝程式：程式內更新下載 *setup*.exe，Lab Control 結束後安靜安裝並重開。"""
    import hashlib

    data = b"MZ setup"
    monkeypatch.setattr(selfupdate, "FROZEN", True)
    monkeypatch.setattr(selfupdate, "this_platform", lambda: "windows")
    monkeypatch.setattr(selfupdate, "current_executable", lambda: tmp_path / "old" / "LabControl.exe")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    started = []
    monkeypatch.setattr(selfupdate.subprocess, "Popen", lambda args, **kw: started.append(args))
    def fetch(info, folder, progress=None):
        (folder / info["name"]).write_bytes(data)
        return folder / info["name"]
    monkeypatch.setattr(selfupdate, "_fetch", fetch)
    info = {"name": "LabControl_v0.0.29_windows-setup.exe", "url": "http://x/s.exe", "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}
    exe = selfupdate.update_to(info, args=["--node"])
    assert exe == tmp_path / "AppData" / "Programs" / "Lab Control" / "LabControl.exe"   # 預設安裝位置
    script = Path(started[0][2])
    text = script.read_text(encoding="utf-8")
    assert started[0][:2] == ["cmd.exe", "/c"]
    assert text.index("tasklist") < text.index("/SILENT") < text.index('start ""')
    assert f'"{exe}" --node' in text and "LabControl_v0.0.29_windows-setup.exe" in text
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "unins000.exe").write_text("")                                  # 用安裝程式裝的 → 同一個位置
    assert selfupdate.installed_exe() == tmp_path / "old" / "LabControl.exe"
    with pytest.raises(RuntimeError, match="檢查碼"):
        selfupdate.update_to(dict(info, sha256="0" * 64))
    assert selfupdate.is_setup("LabLogViewer_v2.1.3_windows-setup.exe") and not selfupdate.is_setup("LabControl_v0.0.27_windows.exe")
