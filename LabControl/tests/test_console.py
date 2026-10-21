"""0.0.18：網頁控制台（上傳 → 發佈，Hub / 論文庫交給控制代理）、公開下載頁、量測電腦設定。"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from labhub.server import Config, make_server
from tests import fake_paperlib
from tests.test_publish import TOKEN, paperlib_files, plenv, zbytes  # noqa: F401


def _call(url, method="GET", body=None, raw=None, cookie=None, token=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
            b = r.read()
            return r.status, (json.loads(b) if r.headers.get_content_type() == "application/json" else b)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def hub(tmp_path, plenv):  # noqa: F811
    ac, fd, agent, pl = plenv
    psrv, _, purl = fake_paperlib.start()
    cfg = Config(tmp_path / "plat", token=TOKEN, paperlib_url=purl, agent_url=ac.url)
    srv, state = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    from qelcomm import Session

    owner = Session.login(url, "admin", "adminpw", save=False)
    alice = Session.login(url, "alice", "alicepw", save=False)
    yield url, state, owner, alice, ac
    srv.shutdown()
    psrv.shutdown()


def test_console_publishes_modules_and_paperlib_through_agent(hub):
    url, state, owner, alice, ac = hub
    C = url + "/api/platform/console"
    # 一般使用者不能用控制台
    assert _call(C + "/items", token=alice.token)[0] == 403
    # 上傳：模塊 zip、啟動器 .exe、論文庫部分不完整的包、亂七八糟的檔
    z = zbytes({"QELLauncher/qel_launcher/__init__.py": '__version__ = "0.0.18"'})
    code, it = _call(C + "/stage?filename=QELLauncher_v0.0.18.zip", "POST", raw=z, token=owner.token)
    assert code == 200 and it["target"] == "launcher" and it["version"] == "0.0.18" and not it["online"]
    code, exe = _call(C + "/stage?filename=QELLauncher_v0.0.18_windows.exe", "POST", raw=b"MZ", token=owner.token)
    assert exe["platform"] == "windows"
    code, pl = _call(C + "/stage?filename=paperlib-1.5.2.zip", "POST", raw=zbytes(paperlib_files("1.5.2")),
                     token=owner.token)
    assert pl["target"] == "paperlib" and pl["kind"] == "web"
    code, err = _call(C + "/stage?filename=x.zip", "POST", raw=zbytes({"a.txt": "?"}), token=owner.token)
    assert code == 400 and "認不出" in err["error"]
    items = _call(C + "/items", token=owner.token)[1]["items"]
    assert [x["target"] for x in items] == ["paperlib", "launcher", "launcher"]       # 論文庫先、安裝檔排在 zip 後
    assert items[1]["file"].endswith(".zip") and items[2]["file"].endswith(".exe")
    # 發佈模塊：直接加到平台
    for x in items[1:]:
        code, r = _call(C + f"/publish/{x['id']}", "POST", token=owner.token)
        assert code == 200 and r["done"], r
    m = {x["id"]: x for x in owner.modules()}["launcher"]
    assert m["latest"] == "0.0.18" and [f["platform"] for f in m["downloads"]] == ["windows"]
    # 再看一次：線上已是這一版
    items = _call(C + "/items", token=owner.token)[1]["items"]
    assert all(x["online"] for x in items if x["target"] == "launcher")
    # 論文庫：交給控制代理，跟著工作進度到結束
    code, r = _call(C + f"/publish/{pl['id']}", "POST", token=owner.token)
    assert code == 200 and r["job"], r
    t0, after, v = time.time(), 0, {}
    while time.time() - t0 < 60:
        code, v = _call(C + f"/jobs/{pl['id']}?after={after}&wait=5", token=owner.token)
        after = v.get("n_lines", after)
        if v.get("done"):
            break
    assert v.get("ok"), v
    assert ac.status()["paperlib"]["version"] == "1.5.2"
    assert v["item"]["done"] and v["item"]["status"].startswith("✔")
    # 清除已完成
    _call(C + "/clear", "POST", token=owner.token)
    assert _call(C + "/items", token=owner.token)[1]["items"] == []
    # 狀態：控制代理、硬碟、論文庫
    st = _call(C + "/status", token=owner.token)[1]
    assert st["agent"]["online"] and st["disk"]["total"] > 0 and st["platform"]["paperlib"]["online"]


def test_public_download_page_and_measure_pc_token(hub):
    url, state, owner, alice, ac = hub
    state.platform.add_release("labcontrol", "0.0.26", "LabControl_v0.0.26_windows.exe", b"MZEXE", "boss",
                               notes="新版")
    state.platform.add_release("labcontrol", "0.0.26", "LabControl_v0.0.26_macos-universal.dmg", b"DMG", "boss")
    state.platform.add_release("labcontrol", "0.0.26", "LabControl_v0.0.26.zip", b"LC", "boss")
    state.platform.add_release("lablogviewer", "2.1.1", "LabLogViewer-Windows-setup.exe", b"MZSETUP", "boss")
    state.platform.add_release("launcher", "0.0.18", "QELLauncher_v0.0.18_macos-arm64.dmg", b"OLD", "boss")
    # 1.0.7：下載頁要登入（程式用的 /get/info、安裝檔下載照舊不用登入）
    code, page = _call(url + "/get")
    assert code == 200 and "登入".encode() in page and "不常下載".encode() not in page
    code, info = _call(url + "/get/info")
    apps = {a["id"]: a for a in info["apps"]}
    assert list(apps) == ["labcontrol", "lablogviewer", "hfss"] and info["downloads"] == []   # 1.0.18：模擬工具
    lc = {f["platform"]: f for f in apps["labcontrol"]["downloads"]}
    assert apps["labcontrol"]["version"] == "0.0.26" and lc["windows"]["notes"] == "新版"
    assert lc["windows"]["url"] == "/get/labcontrol/0.0.26/LabControl_v0.0.26_windows.exe"
    assert lc["macos"]["arch"] == "universal2"
    assert _call(url + lc["windows"]["url"]) == (200, b"MZEXE")
    assert _call(url + apps["lablogviewer"]["downloads"][0]["url"]) == (200, b"MZSETUP")
    # 0.0.27：功能更新包（Python zip）也可以匿名下載（程式內更新用）；其他模塊不行
    assert _call(url + "/get/labcontrol/0.0.26/LabControl_v0.0.26.zip") == (200, b"LC")
    assert _call(url + "/get/monitor/0.0.26/x.exe")[0] == 404
    assert apps["labcontrol"]["code"]["version"] == "0.0.26"
    code, me_mods = _call(url + "/api/platform/modules", token=alice.token)
    hidden = {m["id"] for m in me_mods["modules"] if m.get("hidden")}
    assert {"launcher", "monitor"} <= hidden
    # 沒登入打開網址 → 1.0.7：登入頁
    req = urllib.request.Request(url + "/")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        op.open(req, timeout=10)
        loc = None
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
    assert loc == "/login?next=/portal"
    # 量測電腦：只有站長拿得到裝置 token
    assert _call(url + "/api/platform/console/measure-pc", "POST", {"name": "LAB-A"}, token=alice.token)[0] == 403
    code, r = _call(url + "/api/platform/console/measure-pc", "POST", {"name": "LAB-A"}, token=owner.token)
    assert code == 200 and r["token"] == TOKEN


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_module_patch_package_merges_onto_base(hub):
    """更新包（只有改過的檔案，例如 LabLogViewer 2.1.0）：Hub 拿平台上的 2.0.0 完整 zip 合併成 2.1.0。"""
    import io
    import zipfile

    url, state, owner, alice, ac = hub
    C = url + "/api/platform/console"
    base = zbytes({"LabLogViewer2.0/main.py": "# LabLogViewer", "LabLogViewer2.0/app/__init__.py": '__version__ = "2.0.0"',
                   "LabLogViewer2.0/app/core/a.py": "old", "LabLogViewer2.0/icons/big.png": "PNG"})
    patch = zbytes({"LabLogViewer2.1/qel-module.json": json.dumps({"id": "lablogviewer-patch", "target": "lablogviewer",
                                                                   "version": "2.1.0", "base": "2.0.0", "notes": "讀 .nc"}),
                    "LabLogViewer2.1/app/__init__.py": '__version__ = "2.1.0"',
                    "LabLogViewer2.1/app/core/a.py": "new", "LabLogViewer2.1/app/core/b.py": "added"})
    # 沒有基底 → 發佈時說明要先發佈 2.0.0
    code, p = _call(C + "/stage?filename=LabLogViewer_v2.1.0_patch.zip", "POST", raw=patch, token=owner.token)
    assert code == 200 and p["partial"] and p["base"] == "2.0.0" and p["target"] == "lablogviewer"
    code, err = _call(C + f"/publish/{p['id']}", "POST", token=owner.token)
    assert code == 409 and "2.0.0" in err["error"]
    # 基底和更新包一起丟：完整 zip 先發佈
    code, b = _call(C + "/stage?filename=LabLogViewer2_0.zip", "POST", raw=base, token=owner.token)
    items = [x for x in _call(C + "/items", token=owner.token)[1]["items"] if x["target"] == "lablogviewer"]
    assert [x["id"] for x in items] == [b["id"], p["id"]]
    for x in items:
        code, r = _call(C + f"/publish/{x['id']}", "POST", token=owner.token)
        assert code == 200 and r["done"], r
    m = {x["id"]: x for x in owner.modules()}["lablogviewer"]
    assert m["latest"] == "2.1.0" and m["versions"][0]["notes"] == "讀 .nc"
    f = m["versions"][0]["files"][0]
    assert f["name"] == "LabLogViewer_v2.1.0.zip"
    data = owner._download(f["url"], Path(state.cfg.data_dir) / "dl.zip").read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = {n: z.read(n).decode() for n in z.namelist()}
    assert files == {"LabLogViewer2.0/main.py": "# LabLogViewer", "LabLogViewer2.0/app/__init__.py": '__version__ = "2.1.0"',
                     "LabLogViewer2.0/app/core/a.py": "new", "LabLogViewer2.0/app/core/b.py": "added",
                     "LabLogViewer2.0/icons/big.png": "PNG"}


def test_binary_names_and_versions():
    """安裝檔檔名：有版本號直接用；LabLogViewer GitHub Actions 的檔名沒有版本 → 讀 Windows 版本資源，或要站長輸入。"""
    from labhub.packages import NeedVersion, identify, pe_version

    b = identify(b"MZ", "LabControl_v0.0.26_windows.exe")
    assert (b["target"], b["version"], b["platform"]) == ("labcontrol", "0.0.26", "windows")
    b = identify(b"x", "LabLogViewer_v2.1.1_macos-universal.dmg")
    assert (b["target"], b["version"], b["platform"]) == ("lablogviewer", "2.1.1", "macos")
    # Inno Setup 安裝檔的版本資源（VS_VERSION_INFO 裡的 ProductVersion 字串）
    res = b"MZ" + b"\0" * 64 + "ProductVersion".encode("utf-16-le") + b"\0\0" + "2.1.1.0".encode("utf-16-le") + b"\0\0"
    assert pe_version(res) == "2.1.1"
    b = identify(res, "LabLogViewer-Windows-setup.exe")
    assert (b["target"], b["version"]) == ("lablogviewer", "2.1.1")
    with pytest.raises(NeedVersion, match="需要版本號"):
        identify(b"DMG", "LabLogViewer-macOS-universal.dmg")
    b = identify(b"DMG", "LabLogViewer-macOS-universal.dmg", version="2.1.1")
    assert (b["version"], b["platform"]) == ("2.1.1", "macos")
    b = identify(b"MZ", "LabLogViewer-Windows-setup (1).exe", version="2.1.1")
    assert b["target"] == "lablogviewer"


def test_console_stage_asks_for_version(hub):
    url, state, owner, alice, ac = hub
    C = url + "/api/platform/console"
    code, r = _call(C + "/stage?filename=LabLogViewer-macOS-universal.dmg", "POST", raw=b"DMG", token=owner.token)
    assert code == 400 and r["error"].startswith("需要版本號")
    code, r = _call(C + "/stage?filename=LabLogViewer-macOS-universal.dmg&version=2.1.1", "POST", raw=b"DMG",
                    token=owner.token)
    assert code == 200 and r["version"] == "2.1.1" and r["target"] == "lablogviewer"


def test_watch_push_and_code_package(hub):
    """程式內推播：/get/watch 等著，站長一發佈新版就立刻回應；功能更新包附上執行環境指紋。"""
    import io
    import zipfile

    from labhub.appupdates import requirements_fingerprint

    url, state, owner, alice, ac = hub
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("LabControl/requirements.txt", "numpy>=1.24   # 註解\nPyQt6>=6.5\n")
        z.writestr("LabControl/.python-version", "3.12\n")
        z.writestr("LabControl/labcontrol/__init__.py", '__version__ = "0.0.30"')
    state.platform.add_release("labcontrol", "0.0.29", "LabControl_v0.0.29_windows.exe", b"MZ", "boss")
    code, r = _call(url + "/get/watch?app=labcontrol&version=0.0.29&wait=0")
    assert code == 200 and r["changed"] is False
    got = {}

    def waiter():
        t0 = time.time()
        got["r"] = _call(url + "/get/watch?app=labcontrol&version=0.0.29&wait=20")[1]
        got["dt"] = time.time() - t0
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.5)
    state.platform.add_release("labcontrol", "0.0.30", "LabControl_v0.0.30.zip", buf.getvalue(), "boss", notes="新功能")
    th.join(10)
    assert got["r"]["changed"] and got["dt"] < 5                         # 不是等到 20 秒逾時
    app = got["r"]["app"]
    assert app["version"] == "0.0.30" and app["installer_version"] == "0.0.29"
    c = app["code"]
    assert c["version"] == "0.0.30" and c["python"] == "3.12" and c["notes"] == "新功能"
    assert c["requirements"] == requirements_fingerprint("pyqt6>=6.5\nnumpy>=1.24")      # 順序、註解、大小寫不影響
    assert c["sha256"] and _call(url + c["url"])[1] == buf.getvalue()
    assert _call(url + "/get/watch?app=monitor&version=0")[0] == 404


def test_lablogviewer_feed(hub):
    url, state, owner, alice, ac = hub
    state.platform.add_release("lablogviewer", "2.1.2", "LabLogViewer_v2.1.2_windows-setup.exe", b"MZ", "boss",
                               notes="- 讀 .nc\n- 程式內更新")
    state.platform.add_release("lablogviewer", "2.1.2", "LabLogViewer_v2.1.2_macos-universal.dmg", b"DMG", "boss")
    code, feed = _call(url + "/get/feed/lablogviewer.json")
    assert code == 200 and feed["version"] == "2.1.2" and feed["date"]
    assert [i["zh"] for i in feed["items"]] == ["讀 .nc", "程式內更新"]
    assert {f["platform"] for f in feed["downloads"]} == {"windows", "macos"}
    assert all(f["sha256"] for f in feed["downloads"])


def test_windows_one_click_setup_plan(hub):
    """0.0.28：Windows 一鍵安裝（QELSetup）讀 /get/setup.txt：量測、讀檔最新的 *setup*.exe；/get/setup 轉到最新的安裝程式。"""
    import hashlib

    url, state, owner, alice, ac = hub
    code, r = _call(url + "/get/setup")
    assert code == 404
    p = state.platform
    p.add_release("labcontrol", "0.0.27", "LabControl_v0.0.27_windows.exe", b"MZold", "boss")     # 舊的單一 .exe
    p.add_release("labcontrol", "0.0.28", "LabControl_v0.0.28_windows-setup.exe", b"MZlc", "boss")
    p.add_release("labcontrol", "0.0.28", "LabControl_v0.0.28_macos-arm64.dmg", b"DMG", "boss")
    p.add_release("lablogviewer", "2.1.3", "LabLogViewer_v2.1.3_windows-setup.exe", b"MZllv", "boss")
    p.add_release("setup", "1.0.0", "QELSetup_v1.0.0_windows.exe", b"MZboot", "boss")
    code, text = _call(url + "/get/setup.txt")
    lines = [ln for ln in text.decode().splitlines() if ln and not ln.startswith("#")]
    assert [ln.split("|")[:3] for ln in lines] == [["labcontrol", "0.0.28", "LabControl_v0.0.28_windows-setup.exe"],
                                                  ["lablogviewer", "2.1.3", "LabLogViewer_v2.1.3_windows-setup.exe"]]
    app, ver, name, size, sha, path = lines[0].split("|")
    assert int(size) == 4 and sha == hashlib.sha256(b"MZlc").hexdigest()
    assert _call(url + path)[1] == b"MZlc"                                  # 不用登入就能下載
    code, info = _call(url + "/get/info")
    assert info["setup"]["name"] == "QELSetup_v1.0.0_windows.exe"
    assert "setup" not in [a["id"] for a in info["apps"]]                    # 不是一般的程式卡片
    req = urllib.request.Request(url + "/get/setup")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=10) as resp:                               # 302 → 安裝程式本身
        assert resp.read() == b"MZboot"
    from labhub.packages import identify_binary

    assert identify_binary("QELSetup_v1.0.0_windows.exe")["target"] == "setup"


def test_mac_install_script_and_plan(hub):
    """0.0.29：Mac 一行安裝（curl -fsSL http://<NAS>/get/mac | bash）：清單選這台 Mac 晶片的 .dmg。"""
    url, state, owner, alice, ac = hub
    p = state.platform
    p.add_release("labcontrol", "0.0.29", "LabControl_v0.0.29_macos-arm64.dmg", b"A", "boss")
    p.add_release("labcontrol", "0.0.29", "LabControl_v0.0.29_macos-x86_64.dmg", b"X", "boss")
    p.add_release("lablogviewer", "2.1.4", "LabLogViewer_v2.1.4_macos-universal.dmg", b"U", "boss")
    p.add_release("lablogviewer", "2.1.3", "LabLogViewer_v2.1.3_macos-arm64.dmg", b"old", "boss")
    for arch, lc in (("arm64", "LabControl_v0.0.29_macos-arm64.dmg"), ("x86_64", "LabControl_v0.0.29_macos-x86_64.dmg")):
        code, text = _call(url + f"/get/setup.txt?platform=macos&arch={arch}")
        rows = [ln.split("|") for ln in text.decode().splitlines() if ln and not ln.startswith("#")]
        assert [r[2] for r in rows] == [lc, "LabLogViewer_v2.1.4_macos-universal.dmg"]   # 新版優先，再挑晶片
    code, script = _call(url + "/get/mac")
    text = script.decode()
    assert code == 200 and text.startswith("#!/bin/bash") and f'BASE="{url}"' in text
    assert "shasum -a 256 -c" in text and "xattr -dr com.apple.quarantine" in text and 'main "$@"' in text
    import shutil
    import subprocess

    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n"], input=script, capture_output=True).returncode == 0


def test_llv_pets_zip_is_optional_in_the_plan(hub):
    """0.0.30：讀檔的寵物圖檔 LabLogViewerPets_v<版本>.zip（模塊 llvpets）：只有 ?pets=1 的清單會列出（舊的一鍵安裝看不到）。"""
    import hashlib
    import io
    import zipfile

    from labhub.packages import identify

    url, state, owner, alice, ac = hub
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("capybara/lv01/meta.json", "{}")
        z.writestr("pets_manifest.json", json.dumps({"version": "1.0.0", "pets": ["capybara"]}))
        z.writestr("qel-module.json", json.dumps({"id": "llvpets", "version": "1.0.0", "kind": "module"}))
    data = buf.getvalue()
    assert identify(data, "LabLogViewerPets_v1.0.0.zip")["target"] == "llvpets"
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as z:                                     # 沒有 qel-module.json 也認得
        z.writestr("pets_manifest.json", json.dumps({"version": "1.0.1"}))
    r = identify(buf2.getvalue(), "LabLogViewerPets_v1.0.1.zip")
    assert (r["target"], r["version"]) == ("llvpets", "1.0.1")

    p = state.platform
    p.add_release("lablogviewer", "2.1.5", "LabLogViewer_v2.1.5_windows-setup.exe", b"MZllv", "boss")
    p.add_release("llvpets", "1.0.0", "LabLogViewerPets_v1.0.0.zip", data, "boss")
    code, text = _call(url + "/get/setup.txt")
    assert "pets|" not in text.decode()
    for q in ("?pets=1", "?platform=macos&arch=arm64&pets=1"):
        code, text = _call(url + "/get/setup.txt" + q)
        rows = [ln.split("|") for ln in text.decode().splitlines() if ln.startswith("pets|")]
        assert len(rows) == 1
        app, ver, name, size, sha, path = rows[0]
        assert (ver, name, int(size), sha) == ("1.0.0", "LabLogViewerPets_v1.0.0.zip", len(data),
                                               hashlib.sha256(data).hexdigest())
    assert _call(url + path)[1] == data                                      # 不用登入就能下載
    code, info = _call(url + "/get/info")
    assert "llvpets" not in [a["id"] for a in info["apps"]]
    code, script = _call(url + "/get/mac")
    assert b"&pets=1" in script and b"QEL_PETS" in script and b"install_pets" in script


def test_paperlib_service_account_lists_projects_for_devices(hub):
    """0.0.31：量測電腦沒有登入時，Hub 用論文庫服務帳號查專案（帳密只存在 Hub 資料夾，不在程式裡）。"""
    url, state, owner, alice, ac = hub
    C = url + "/api/platform/console/paperlib-service"
    p = state.platform
    p._proj_cache = (0.0, [])
    code, r = _call(url + "/api/platform/projects", token=TOKEN)          # 裝置 token、還沒設定：空的
    assert code == 200 and r["projects"] == []
    assert _call(C, token=alice.token)[0] == 403                          # 只有站長
    code, r = _call(C, "PUT", {"user": "alice", "password": "wrong"}, token=owner.token)
    assert code == 400 and not p.service_info()["configured"]
    code, r = _call(C, "PUT", {"user": "alice", "password": "alicepw"}, token=owner.token)
    assert code == 200 and r["configured"] and r["user"] == "alice" and "password" not in json.dumps(r)
    code, r = _call(url + "/api/platform/projects", token=TOKEN)
    assert code == 200 and "YIG mirror coupling" in [x["name"] for x in r["projects"]]
    f = p.data_dir / "paperlib_service.json"
    assert json.loads(f.read_text())["user"] == "alice"
    p._service_session = "expired"                                        # session 過期 → 自己重登
    p._proj_cache = (0.0, [])
    code, r = _call(url + "/api/platform/projects?refresh=1", token=TOKEN)
    assert code == 200 and r["projects"] and p._service_session != "expired"
    code, r = _call(C, "PUT", {"user": ""}, token=owner.token)            # 清除
    assert not r["configured"] and not f.exists()
