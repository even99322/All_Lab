"""1.0.16：模擬工具（HFSS 自動化工具）放進平台的模塊下載：認得出 zip、發佈、首頁的下載按鈕。"""
from __future__ import annotations

import io
import json
import zipfile


def _hfss_zip(manifest: bool = False, top: str = "HFSS自動化工具 v3.2/") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(top + "config.py", 'import os\nAPP_VERSION = "v3.2"\nDEFAULT_AEDT_VERSION = "2026.1"\n')
        z.writestr(top + "hfss_worker.py", "# worker\n")
        z.writestr(top + "main.py", "print('hi')\n")
        z.writestr(top + "README.md", "# HFSS\n\n## 版本紀錄\n\n### v3.2\n- 新增 YIG 磁性球\n\n### v3.1\n- 舊的\n")
        if manifest:
            z.writestr(top + "qel-module.json", json.dumps({"id": "hfss", "version": "3.2"}))
    return buf.getvalue()


def test_identify_hfss_tool_zip():
    from labhub.packages import identify

    r = identify(_hfss_zip(), "HFSS自動化工具v3.2.zip")
    assert (r["target"], r["kind"], r["version"]) == ("hfss", "module", "3.2")
    assert r["notes"] == "- 新增 YIG 磁性球"
    assert identify(_hfss_zip(manifest=True), "HFSSTool_v3.2.zip")["target"] == "hfss"
    assert identify(_hfss_zip(top=""), "x.zip")["version"] == "3.2"               # 沒有最上層資料夾也可以


def test_publish_hfss_shows_download_tile(tmp_path):
    from labhub.server import Config, make_server

    srv, state = make_server(Config(tmp_path / "hub", token="dev"), "127.0.0.1", 0)
    try:
        plat = state.platform
        # 舊版 Hub 發佈時自動建立的空白設定不會蓋掉內建的名稱與說明
        plat.set_module("hfss", {"name": "hfss", "kind": "python"})
        con = state.console
        it = con.stage("HFSS自動化工具v3.2.zip", _hfss_zip(), "boss")
        assert it["target"] == "hfss" and it["version"] == "3.2"
        con.publish(it["id"], "boss")
        m = plat.modules()["hfss"]
        assert m["name"] == "模擬" and m["kind"] == "app" and m["note"] and m["latest"] == "3.2"
        files = m["versions"][0]["files"]
        assert [f["name"] for f in files] == ["HFSS_v3.2.zip"]                     # 中文檔名換成英數
        assert m["downloads"] == []                                                # 沒有 .exe / .dmg：首頁給 zip 按鈕
        assert "新增 YIG" in m["versions"][0]["notes"]
        assert any(x["id"] == "hfss" and x["allowed"] for x in plat.modules_for({"username": "a", "owner": False}))
    finally:
        srv.server_close()


def test_hfss_exe_from_github():
    """1.0.17：GitHub 發佈 hfss-v3.2 的 HFSSTool_v3.2_windows.exe → 模擬工具的 Windows 版。"""
    from labhub.github_sync import PREFIXES, guess
    from labhub.packages import identify

    assert "hfss-v" in PREFIXES
    g = guess("HFSSTool_v3.2_windows.exe")
    assert g["target"] == "hfss" and g["version"] == "3.2" and g["platform"] == "windows"
    assert identify(b"MZ" + b"x" * 100, "HFSSTool_v3.2_windows.exe")["target"] == "hfss"


def test_hfss_update_api(tmp_path):
    """1.0.18：模擬工具的程式內更新：/get/info 有 hfss、/get/watch?app=hfss 推播、exe 不用登入就能下載。"""
    import http.client
    import threading

    from labhub.server import Config, make_server

    srv, state = make_server(Config(tmp_path / "hub", token="dev"), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        exe = b"MZ" + b"h" * 500
        state.platform.add_release("hfss", "3.3", "HFSSTool_v3.3_windows.exe", exe, "boss")
        port = srv.server_address[1]

        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            c.request("GET", path)
            r = c.getresponse()
            body = r.read()
            c.close()
            return r.status, body

        st, body = get("/get/info")
        app = next(a for a in json.loads(body)["apps"] if a["id"] == "hfss")
        assert st == 200 and app["version"] == "3.3"
        f = app["downloads"][0]
        assert f["platform"] == "windows" and f["url"] == "/get/hfss/3.3/HFSSTool_v3.3_windows.exe" and f["sha256"]
        assert get(f["url"]) == (200, exe)
        st, body = get("/get/watch?app=hfss&version=3.2&wait=1")
        assert json.loads(body)["changed"] is True
        st, body = get("/get/watch?app=hfss&version=3.3&wait=0.2")
        assert json.loads(body)["changed"] is False
    finally:
        srv.shutdown()
