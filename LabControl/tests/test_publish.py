"""發佈中心（0.0.14）：辨識 zip、控制代理更新論文庫（建置映像、資料不動、失敗換回）。"""
from __future__ import annotations

import io
import json
import socket
import threading
import zipfile
from pathlib import Path

import pytest

from labhub.agent import AgentConfig, make_agent_server
from labhub.packages import PackageError, identify
from labmonitor.client import AgentClient
from tests.fake_docker import FakeDocker
from tests.test_agent import _run

TOKEN = "tk"


def zbytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def paperlib_files(version: str, broken: bool = False, prefix: str = "paperlib/") -> dict:
    return {f"{prefix}app/taxonomy.py": "X = 1\n",f"{prefix}Dockerfile": "FROM python:3.11-slim\n", f"{prefix}requirements.txt": "fastapi\n",
            f"{prefix}app/__init__.py": "",
            f"{prefix}app/config.py": f'import os\nDATA = os.environ.get("PAPERLIB_DATA")\nVERSION = "{version}"\n',
            f"{prefix}app/main.py": "app = None\n" + ("BROKEN = 1\n" if broken else ""),
            f"{prefix}docker-compose.yml": "services: {new: 1}\n", f"{prefix}seed/x.txt": "seed"}


def test_identify_each_module():
    cases = {
        "labcontrol": {"LabControl/labcontrol/__init__.py": '__version__ = "0.0.14"', "LabControl/labhub/server.py": "",
                       "LabControl/labhub/__init__.py": '__version__ = "0.0.14"',
                       "LabControl/docs/releases/v0.0.14.md": "# x\n## 摘要（貼）\n- 新東西\n## 新增\n"},
        "monitor": {"LabControlMonitor/labmonitor/__init__.py": '__version__ = "0.0.14"',
                    "LabControlMonitor/labhub/server.py": "", "LabControlMonitor/main.py": ""},
        "launcher": {"QELLauncher/qel_launcher/__init__.py": '__version__ = "0.0.14"'},
        "hub": {"LabControlHub/labhub/server.py": "", "LabControlHub/labhub/__init__.py": '__version__ = "0.0.14"'},
        "lablogviewer": {"L/app/__init__.py": '__version__ = "1.0.5"', "L/main.py": "LabLogViewer entry",
                         "L/update.txt": "v1.0.5 - SOMETHING NEW (folder)\n"},
        "paperlib": paperlib_files("1.5.2"),
    }
    for tid, files in cases.items():
        r = identify(zbytes(files), f"{tid}.zip")
        assert r["target"] == tid, (tid, r)
    assert identify(zbytes(cases["labcontrol"]))["notes"] == "- 新東西"
    assert identify(zbytes(cases["lablogviewer"]))["notes"] == "SOMETHING NEW (folder)"
    r = identify(zbytes(cases["paperlib"]))
    assert r["kind"] == "web" and r["version"] == "1.5.2"
    r = identify(zbytes({"X/qel-module.json": json.dumps({"id": "newtool", "version": "2.0.1"}), "X/main.py": ""}))
    assert r["target"] == "newtool" and r["kind"] == "module" and r["version"] == "2.0.1"
    with pytest.raises(PackageError, match="原始碼"):
        identify(zbytes({"Q/qel-module.json": json.dumps({"id": "qelplatform", "version": "1", "kind": "source"}),
                         "Q/qel_launcher/__init__.py": '__version__ = "1"'}))
    with pytest.raises(PackageError, match="認不出"):
        identify(zbytes({"a.txt": "x"}))
    with pytest.raises(PackageError, match="不是 zip"):
        identify(b"nope")


def test_identify_real_release_zips(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, str(root / "tools" / "make_release.py"), str(tmp_path)], check=True,
                   capture_output=True)
    from labhub import __version__
    got = {}
    for p in tmp_path.glob("*.zip"):
        try:
            got[identify(p)["target"]] = identify(p)["version"]
        except PackageError as e:
            assert "NAS 安裝包" in str(e) and p.name.startswith("QELPlatform_NAS_")
    assert got == {"labcontrol": __version__, "hub": __version__, "monitor": __version__}


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def plenv(tmp_path):
    root = Path(__file__).resolve().parents[1]
    import shutil
    hub_dir = tmp_path / "LabControlHub"
    shutil.copytree(root / "labhub", hub_dir / "labhub", ignore=shutil.ignore_patterns("__pycache__"))
    (hub_dir / "data").mkdir()
    pl = tmp_path / "paperlib"
    for k, v in paperlib_files("1.5.1", prefix="").items():
        (pl / k).parent.mkdir(parents=True, exist_ok=True)
        (pl / k).write_text(v)
    (pl / "docker-compose.yml").write_text("services: {mine: 1}  # TUNNEL_TOKEN=secret\n")
    (pl / "data").mkdir()
    import sqlite3
    con = sqlite3.connect(pl / "data" / "library.db")
    con.execute("CREATE TABLE papers (title TEXT)")
    con.execute("INSERT INTO papers VALUES ('precious')")
    con.commit()
    con.close()
    (pl / "import").mkdir()
    hub_port, pl_port = _port(), _port()
    fd = FakeDocker(hub_dir, "labcontrol-hub", hub_port, TOKEN)
    fd.add_paperlib("paperlib", pl_port, "paperlib:1.5.1", pl)
    cfg = AgentConfig(hub_dir, "labcontrol-hub", f"http://127.0.0.1:{hub_port}", fd.url, TOKEN, verify_s=20,
                      paperlib_dir=pl, paperlib_container="paperlib", paperlib_url=f"http://127.0.0.1:{pl_port}",
                      paperlib_verify_s=15)
    srv, agent = make_agent_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ac = AgentClient(f"http://127.0.0.1:{srv.server_address[1]}", TOKEN)
    yield ac, fd, agent, pl
    srv.shutdown()
    fd.close()


def test_agent_updates_paperlib(plenv):
    import time
    ac, fd, agent, pl = plenv
    t0 = time.time()
    while ac.status()["paperlib"].get("version") != "1.5.1" and time.time() - t0 < 10:
        time.sleep(0.2)
    st = ac.status()["paperlib"]
    assert st["configured"] and st["version"] == "1.5.1" and st["code_version"] == "1.5.1"
    v, lines = _run(ac, "paperlib_update", data=zbytes(paperlib_files("1.5.2")))
    assert v["ok"], (v, lines)
    assert [s["status"] for s in v["steps"]] == ["ok"] * 9
    import sqlite3
    assert sqlite3.connect(pl / "data" / "library.db").execute("SELECT title FROM papers").fetchall() == \
        [("precious",)]                                                              # 資料不動
    assert list((pl.parent / "LabControlHub" / "backups").glob("paperlib_db_*.db"))      # 停機後備份資料庫
    assert "TUNNEL_TOKEN" in (pl / "docker-compose.yml").read_text()                 # 使用者的 compose 不動
    assert ac.status()["paperlib"]["version"] == "1.5.2"
    tag, names = fd.builds[-1]
    assert tag == "paperlib:1.5.1" and "Dockerfile" in names and not any(n.startswith("data") for n in names)
    # 壞掉的新版 → 自動換回 1.5.2
    v, lines = _run(ac, "paperlib_update", data=zbytes(paperlib_files("1.5.3", broken=True)))
    assert not v["ok"] and "換回" in v["result"]
    assert ac.status()["paperlib"]["version"] == "1.5.2" and agent.paperlib_code_version() == "1.5.2"
    # 不是論文庫的 zip：在動任何東西前就拒絕
    v, _ = _run(ac, "paperlib_update", data=zbytes({"x/labhub/server.py": ""}))
    assert not v["ok"] and v["steps"][1]["status"] in ("pending", "skip")
    # 回到備份（1.5.1）
    bk = next(b for b in ac.status()["paperlib"]["backups"] if b["version"] == "1.5.1")
    v, _ = _run(ac, "paperlib_rollback", backup=bk["name"])
    assert v["ok"] and ac.status()["paperlib"]["version"] == "1.5.1"
    v, _ = _run(ac, "paperlib_restart")
    assert v["ok"]


def test_hub_serves_module_releases_to_nodes(tmp_path):
    from labhub.server import Config, make_server
    srv, state = make_server(Config(tmp_path / "hub", token="t"), "127.0.0.1", 0)
    z = zbytes({"LabControl/main.py": "print(1)", "LabControl/labcontrol/__init__.py": '__version__ = "0.0.99"'})
    state.platform.add_release("labcontrol", "0.0.99", "LabControl_v0.0.99.zip", z, "boss")
    assert "0.0.99" in state.releases() and state.latest_version() == "0.0.99"
    with zipfile.ZipFile(state.release_zip("0.0.99")) as f:
        assert sorted(f.namelist()) == ["labcontrol/__init__.py", "main.py"]          # main.py 在最上層
    srv.server_close()


def test_publish_center_one_click(plenv, tmp_path):
    """Monitor 發佈中心：丟三個 zip（論文庫、讀檔模塊、Hub）→ 一鍵全部發佈，Hub 最後。"""
    import time as _t

    from PyQt6 import QtWidgets

    from labhub.server import Config, make_server
    from labmonitor.publish_tab import PublishTab
    from qelcomm import Session
    from tests import fake_paperlib
    from tests.test_agent import _zip_of

    ac, fd, agent, pl = plenv
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "plat", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    owner = Session.login(f"http://127.0.0.1:{srv.server_address[1]}", "admin", "adminpw", save=False)

    class Plat:
        session = owner

        def login_or_out(self):
            raise AssertionError("不應該要求登入")

    files = {"paperlib-1.5.2.zip": zbytes(paperlib_files("1.5.2")),
             "LabLogViewer_v1.0.5.zip": zbytes({"LabLogViewer_v1.0.5/app/__init__.py": '__version__ = "1.0.5"',
                                                "LabLogViewer_v1.0.5/main.py": "# LabLogViewer",
                                                "LabLogViewer_v1.0.5/update.txt": "v1.0.5 - FASTER PLOTS (x)\n"}),
             "LabControlHub_v0.0.99.zip": _zip_of(Path(agent.code_dir), version="0.0.99"),
             "junk.zip": zbytes({"readme.txt": "?"})}
    paths = []
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
        paths.append(str(tmp_path / n))
    app = QtWidgets.QApplication.instance()
    tab = PublishTab(lambda: ac.url, lambda: TOKEN, Plat())
    tab.show()
    tab.add_files(paths)
    assert [x["target"] for x in tab.items] == ["paperlib", "lablogviewer", "hub"]        # junk 被拒絕、Hub 排最後
    assert "junk.zip" in tab.log.toPlainText()
    t0 = _t.time()
    while not tab.current.get("paperlib") and _t.time() - t0 < 10:
        app.processEvents()
        _t.sleep(0.05)
    assert tab.current["paperlib"] == "1.5.1"
    tab.publish_all()
    t0 = _t.time()
    while (tab.running or any(not x["done"] for x in tab.items)) and _t.time() - t0 < 90:
        app.processEvents()
        _t.sleep(0.05)
    assert all(x["done"] for x in tab.items), [x["status"] for x in tab.items]
    log = tab.log.toPlainText()
    assert log.index("論文庫") < log.index("LabLogViewer v1.0.5：") < log.index("Hub 網站（NAS 中繼站） v0.0.99：")
    assert ac.status()["paperlib"]["version"] == "1.5.2" and ac.status()["hub_version"] == "0.0.99"
    m = {x["id"]: x for x in owner.modules()}["lablogviewer"]
    assert m["latest"] == "1.0.5" and m["versions"][0]["notes"] == "FASTER PLOTS (x)"
    tab.close()
    srv.shutdown()
    psrv.shutdown()


def test_agent_applies_partial_paperlib_update(plenv):
    """paperlib 1.7.0 這種只有改過的 app/ 檔案的更新包：只覆蓋那些檔，缺基底檔案時事前拒絕。"""
    import sqlite3
    import time
    ac, fd, agent, pl = plenv
    t0 = time.time()
    while ac.status()["paperlib"].get("version") != "1.5.1" and time.time() - t0 < 10:
        time.sleep(0.2)
    cfgtxt = 'import os\nDATA = os.environ.get("PAPERLIB_DATA")\nVERSION = "1.7.0"\n'
    # 需要 projects.py（新基底版才有）→ 檢查時就拒絕，什麼都不動
    need = zbytes({"app/config.py": cfgtxt, "app/main.py": "from . import projects\napp = None\n",
                   "UPDATE_NOTES_1.7.0.md": "# 1.7.0"})
    r = identify(need)
    assert r["target"] == "paperlib" and r["partial"] and r["notes"] == "# 1.7.0"
    v, _ = _run(ac, "paperlib_update", data=need)
    assert not v["ok"] and "projects.py" in v["result"] and agent.paperlib_code_version() == "1.5.1"
    # 基底檔案都在 → 只覆蓋包裡的檔案
    ok = zbytes({"app/config.py": cfgtxt, "app/main.py": "from . import taxonomy\napp = None\n",
                 "app/static/new.js": "1", "UPDATE_NOTES_1.7.0.md": "# 1.7.0"})
    v, lines = _run(ac, "paperlib_update", data=ok)
    assert v["ok"], (v, lines)
    assert ac.status()["paperlib"]["version"] == "1.7.0"
    assert (pl / "app" / "taxonomy.py").exists() and (pl / "Dockerfile").exists()      # 其他檔案保留
    assert (pl / "app" / "static" / "new.js").exists()
    # 部分更新包壞掉 → 程式與資料庫都換回 1.7.0
    con = sqlite3.connect(pl / "data" / "library.db")
    before = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    con.close()
    bad = zbytes({"app/config.py": cfgtxt.replace("1.7.0", "1.7.1"), "app/main.py": "BROKEN = 1\n"})
    v, _ = _run(ac, "paperlib_update", data=bad)
    assert not v["ok"] and ac.status()["paperlib"]["version"] == "1.7.0"
    assert sqlite3.connect(pl / "data" / "library.db").execute("SELECT COUNT(*) FROM papers").fetchone()[0] == before


def test_identify_launcher_installers():
    """0.0.17：啟動器的 Windows .exe、macOS .dmg 看檔名辨識。"""
    r = identify(b"MZ...", "QELLauncher_v0.0.17_windows.exe")
    assert (r["target"], r["version"], r["platform"]) == ("launcher", "0.0.17", "windows")
    r = identify(b"", "QELLauncher_v0.0.17_macos-arm64.dmg")
    assert (r["target"], r["platform"]) == ("launcher", "macos")
    r = identify(zbytes({"量子工程啟動器.app/Contents/Info.plist": "x"}), "QELLauncher_v0.0.17_macos-x86_64.zip")
    assert r["platform"] == "macos"
    with pytest.raises(PackageError, match="安裝檔"):
        identify(b"", "setup.exe")


def test_installers_added_to_existing_version_and_offered_per_os(tmp_path):
    """發佈中心：同一版補上 .exe / .dmg（不會被當成「已是這一版」略過）；平台列出各作業系統的下載。"""
    import time as _t

    from PyQt6 import QtWidgets

    from labhub.server import Config, make_server
    from labmonitor.publish_tab import PublishTab
    from qelcomm import Session
    from tests import fake_paperlib

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "plat", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    owner = Session.login(url, "admin", "adminpw", save=False)
    z = zbytes({"QELLauncher/qel_launcher/__init__.py": '__version__ = "0.0.17"'})
    state.platform.add_release("launcher", "0.0.17", "QELLauncher_v0.0.17.zip", z, "boss", "新版說明")

    class Plat:
        session = owner

        def login_or_out(self):
            raise AssertionError("不應該要求登入")

    paths = []
    for n, b in {"QELLauncher_v0.0.17_windows.exe": b"MZexe", "QELLauncher_v0.0.17_macos-arm64.dmg": b"dmg1",
                 "QELLauncher_v0.0.17_macos-x86_64.dmg": b"dmg2"}.items():
        (tmp_path / n).write_bytes(b)
        paths.append(str(tmp_path / n))
    app = QtWidgets.QApplication.instance()
    tab = PublishTab(lambda: "", lambda: "", Plat())
    tab.add_files(paths)
    assert len(tab.items) == 3                                   # 兩種 Mac 晶片各一個，不互相取代
    t0 = _t.time()
    while not tab.current.get("launcher") and _t.time() - t0 < 10:
        app.processEvents()
        _t.sleep(0.05)
    tab.publish_all()
    t0 = _t.time()
    while (tab.running or any(not x["done"] for x in tab.items)) and _t.time() - t0 < 30:
        app.processEvents()
        _t.sleep(0.05)
    assert all(x["status"].startswith("✔ 已發佈") for x in tab.items), [x["status"] for x in tab.items]
    m = {x["id"]: x for x in owner.modules()}["launcher"]
    assert m["latest"] == "0.0.17" and m["versions"][0]["notes"] == "新版說明"          # 說明保留
    dl = {(f["platform"], f["arch"]): f for f in m["downloads"]}
    assert set(dl) == {("windows", ""), ("macos", "arm64"), ("macos", "x86_64")}
    assert owner._download(dl[("windows", "")]["url"], tmp_path / "dl.exe").read_bytes() == b"MZexe"
    # 再丟一次同一個檔 → 略過
    tab.items = []
    tab.add_files(paths[:1])
    tab.refresh_current()
    t0 = _t.time()
    while not tab.published.get("launcher") and _t.time() - t0 < 10:
        app.processEvents()
        _t.sleep(0.05)
    tab.publish_all()
    assert tab.items[0]["status"].startswith("✔ 線上已有")
    # 只有安裝檔的新版本不算「最新版」（啟動器沒有 zip 可以安裝）
    state.platform.add_release("launcher", "0.0.18", "QELLauncher_v0.0.18_windows.exe", b"MZ2", "boss")
    m = {x["id"]: x for x in owner.modules()}["launcher"]
    assert m["latest"] == "0.0.17"
    assert next(f for f in m["downloads"] if f["platform"] == "windows")["version"] == "0.0.18"
    tab.close()
    srv.shutdown()
    psrv.shutdown()
