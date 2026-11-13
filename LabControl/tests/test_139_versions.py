"""1.0.39：「版本紀錄」頁——repo 的修改紀錄 ＋ GitHub 發佈時間 ＋ 這台 Hub 的上線時間。"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from labhub import versions as vs

ROOT = Path(__file__).resolve().parents[1]
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_history_from_repo():
    sys.path.insert(0, str(ROOT / "tools"))
    import version_history

    d = version_history.build()["apps"]
    assert set(d) == {"labcontrol", "hub", "netmeter", "hfss", "lablogviewer"}
    lc = {e["version"]: e for e in d["labcontrol"]["entries"]}
    assert lc["1.0.35"]["date"] == "2026-11-13" and "詳見" not in lc["1.0.35"]["text"] and lc["1.0.35"]["detail"]
    nm = {e["version"]: e for e in d["netmeter"]["entries"]}
    assert "1.0.3" in nm and "系統排程" in nm["1.0.3"]["text"] and "### v" not in nm["1.0.3"]["text"]
    assert any(e["version"] == "1.0.38" for e in d["hub"]["entries"])


def test_vkey_and_clean_body():
    assert vs.vkey("1.0.10") > vs.vkey("1.0.9") and vs.vkey("0.19E") > vs.vkey("0.19D") > vs.vkey("0.18A")
    assert vs.vkey("1.01") == vs.vkey("1.0.1") and vs.vkey("v3.9") == vs.vkey("3.9.0")
    body = ("平台網頁「控制台 → 從 GitHub 取得新版」會直接下載這些檔案並發佈；也可以下載後拖進控制台。\n\n"
            "| 檔案 | 大小 | sha256 |\n|---|---|---|\n| a.zip | 1 MB | `x` |\n")
    assert vs.clean_body(body) == ""
    hub = "# Lab Control Hub 1.0.38\n\n- 發佈日期：2026-11-13\n- 類型：新功能\n\n## 摘要（貼到版本說明）\n\n- **A**：b\n\n## 更新步驟\n\n1. x\n"
    assert vs.clean_body(hub) == "- 類型：新功能\n\n- **A**：b"
    assert vs.clean_body("QEL 流量監看 1.0.5（Windows .exe、macOS .dmg）\n\n\n改了 X。") == "改了 X。"


def test_api_merges_sources(tmp_path, monkeypatch):
    from labhub.server import Config, make_server
    from tests import fake_paperlib
    from tests.test_113 import _fake_github
    from tests.test_122 import _call

    gsrv, gurl, _ = _fake_github({}, {"hub-v1.0.38": [], "netmeter-v9.9.9": [], "labcontrol-v1.0.35": []})
    monkeypatch.setenv("LABHUB_GITHUB_API", gurl)
    monkeypatch.setenv("LABHUB_GITHUB_REPO", "o/r")
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st = vs.store(state)
        assert json.loads((tmp_path / "hub" / "versions_local.json").read_text())["hub"]     # Hub 啟動時間記下來了
        st.refresh(force=True, wait=True)
        assert not st.error and (tmp_path / "hub" / "versions_github.json").exists()
        # 平台模塊：發佈到 Hub 的時間
        z = tmp_path / "nm.zip"
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("a.txt", "x")
        state.platform.add_release("netmeter", "9.9.9", "QELNetMeter_v9.9.9.zip", z.read_bytes(), "alice", "新功能 X")
        tok = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]["token"]
        code, d = _call(url + "/api/versions", cookie=f"labhub_token={tok}")
        assert code == 200
        apps = {a["id"]: a for a in d["apps"]}
        hub = {e["version"]: e for e in apps["hub"]["entries"]}
        from labhub import __version__

        assert hub[__version__]["current"] and hub[__version__].get("hub_time")
        assert hub["1.0.38"]["published"] and hub["1.0.38"]["text"]                          # repo 的紀錄 ＋ GitHub 時間
        nm = {e["version"]: e for e in apps["netmeter"]["entries"]}
        assert nm["9.9.9"]["hub_by"] == "alice" and nm["9.9.9"]["published"] and nm["9.9.9"]["current"]
        assert nm["9.9.9"]["text"] == "新功能 X"                                                # 打包時沒有 → Hub 發佈說明
        assert [e["version"] for e in apps["labcontrol"]["entries"]][:2] == ["1.0.35", "1.0.34"]
        r = OP.open(urllib.request.Request(url + "/versions", headers={"Cookie": f"labhub_token={tok}"}), timeout=10)
        html = r.read().decode()
        assert "版本紀錄" in html and 'href="/versions"' in html
        assert _call(url + "/api/versions/refresh", "POST", {}, cookie=f"labhub_token={tok}")[0] == 200
    finally:
        srv.shutdown()
        psrv.shutdown()
        gsrv.shutdown()


def test_hub_zip_includes_history(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import make_release

    p = make_release.hub_zip(tmp_path, make_release.hub_version())
    with zipfile.ZipFile(p) as z:
        d = json.loads(z.read("LabControlHub/labhub/version_history.json"))
    assert len(d["apps"]["labcontrol"]["entries"]) > 50
