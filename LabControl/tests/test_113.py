"""1.0.13：控制台「從 GitHub 取得新版」——Hub 自己下載雲端打包好的安裝檔與更新包，不用站長先下載再上傳。"""
from __future__ import annotations

import http.server
import json
import threading
import time

import pytest

from tests.test_publish import zbytes


def _fake_github(assets: dict, tag_assets: dict):
    """假的 GitHub：/repos/o/r/releases 與檔案下載（下載先 302 轉到另一個網址，像真的 GitHub）。"""
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.path.startswith("/repos/o/r/releases"):
                rel = [{"tag_name": tag, "name": tag.replace("-v", " "), "draft": False, "prerelease": False,
                        "published_at": "2026-10-21T00:00:00Z", "html_url": f"{base}/r/{tag}",
                        "assets": [{"name": n, "size": len(assets[n]),
                                    "browser_download_url": f"{base}/download/{tag}/{n}"} for n in names]}
                       for tag, names in tag_assets.items()]
                body = json.dumps(rel).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/download/"):
                self.send_response(302)
                self.send_header("Location", f"{base}/blob/{self.path.rsplit('/', 1)[1]}")
                self.end_headers()
            elif self.path.startswith("/blob/"):
                body = assets[self.path.rsplit("/", 1)[1]]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", hits


def test_console_fetches_new_versions_from_github(tmp_path, monkeypatch):
    from labhub.github_sync import guess
    from labhub.server import Config, make_server
    from tests import fake_paperlib

    assets = {
        "LabControl_v1.0.13_windows-setup.exe": b"MZ" + b"x" * 3000,
        "LabControl_v1.0.13_macos-arm64.dmg": b"dmg" * 1000,
        "LabLogViewer_v1.0.10_windows-setup.exe": b"MZ" + b"y" * 4000,
        "LabLogViewerPets_v1.0.0.zip": zbytes({"pets_manifest.json": json.dumps({"version": "1.0.0"})}),
        "QELSetup_v1.1.0_windows.exe": b"MZ" + b"s" * 100,
        "LabLogViewer_v1.0.9_windows-setup.exe": b"MZ" + b"o" * 10,     # 舊的那一版：不會選它
        "readme.txt": b"?",
    }
    tags = {"labcontrol-v1.0.13": ["LabControl_v1.0.13_windows-setup.exe", "LabControl_v1.0.13_macos-arm64.dmg",
                                   "QELSetup_v1.1.0_windows.exe", "readme.txt"],
            "lablogviewer-v1.0.10": ["LabLogViewer_v1.0.10_windows-setup.exe", "LabLogViewerPets_v1.0.0.zip"],
            "lablogviewer-v1.0.9": ["LabLogViewer_v1.0.9_windows-setup.exe"]}
    gsrv, gurl, hits = _fake_github(assets, tags)
    monkeypatch.setenv("LABHUB_GITHUB_API", gurl)
    monkeypatch.setenv("LABHUB_GITHUB_REPO", "o/r")
    assert guess("LabControlHub_v1.0.13.zip") == {"target": "hub", "version": "1.0.13",
                                                  "file": "LabControlHub_v1.0.13.zip", "partial": False}
    assert guess("LabControl_v1.0.13_windows-setup.exe")["platform"] == "windows" and guess("readme.txt") is None

    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "plat", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # 寵物圖檔線上已經有了 → 不用下載
        state.platform.add_release("llvpets", "1.0.0", "LabLogViewerPets_v1.0.0.zip",
                                   assets["LabLogViewerPets_v1.0.0.zip"], "boss")
        con = state.console
        v = con.github.view(force=True)
        assert [r["tag"] for r in v["releases"]] == ["labcontrol-v1.0.13", "lablogviewer-v1.0.10"]
        assert sorted(v["wanted"]) == sorted(["LabControl_v1.0.13_windows-setup.exe", "LabControl_v1.0.13_macos-arm64.dmg",
                                              "QELSetup_v1.1.0_windows.exe", "LabLogViewer_v1.0.10_windows-setup.exe"])
        pets = next(f for r in v["releases"] for f in r["files"] if f["name"].startswith("LabLogViewerPets"))
        assert pets["online"]
        # 只下載：四個檔案出現在暫存（待發佈），內容一樣
        job = con.github.fetch("boss", publish=False, wait=True)
        assert job["done"] and all(f["status"] == "已下載" for f in job["files"]), job
        items = {it["file"]: it for it in con.items()}
        assert set(items) == set(v["wanted"]) and all(it["status"] == "待發佈" for it in items.values())
        assert items["LabControl_v1.0.13_macos-arm64.dmg"]["size"] == len(assets["LabControl_v1.0.13_macos-arm64.dmg"])
        assert con.github.view()["wanted"] == []                         # 已在暫存：不會再下載一次
        # 發佈 → 平台有這些安裝檔；之後檢查：都是線上已有
        for it in items.values():
            con.publish(it["id"], "boss")
        mods = state.platform.modules()
        files = {f["name"] for f in mods["labcontrol"]["versions"][0]["files"]}
        assert {"LabControl_v1.0.13_windows-setup.exe", "LabControl_v1.0.13_macos-arm64.dmg"} <= files
        con.clear_done()
        con.github._cache = None
        v = con.github.view(force=True)
        assert v["wanted"] == [] and all(f["online"] for r in v["releases"] for f in r["files"])
        assert con.github.fetch("boss", wait=True)["message"] == "線上都是最新版"
        # 自動模式：有新版就下載並發佈
        assets["LabLogViewer_v1.0.11_windows-setup.exe"] = b"MZ" + b"z" * 500
        tags["lablogviewer-v1.0.11"] = ["LabLogViewer_v1.0.11_windows-setup.exe"]
        con.github._cache = None
        con.github._save_cfg(auto=True, auto_by="boss")
        r = con.github.auto_once()
        assert r["result"].startswith("1 / 1")
        mods = state.platform.modules()
        assert any(f["name"] == "LabLogViewer_v1.0.11_windows-setup.exe"
                   for ver in mods["lablogviewer"]["versions"] for f in ver["files"])
        assert con.github.config()["last_auto_result"].startswith("1 / 1")
        con.github._save_cfg(auto=False)
    finally:
        srv.shutdown()
        psrv.shutdown()
        gsrv.shutdown()


def test_console_github_routes_need_owner(tmp_path, monkeypatch):
    from labhub.server import Config, make_server
    from qelcomm import Session
    from tests import fake_paperlib

    gsrv, gurl, _hits = _fake_github({"LabControl_v1.0.13_windows-setup.exe": b"MZ1"},
                                     {"labcontrol-v1.0.13": ["LabControl_v1.0.13_windows-setup.exe"]})
    monkeypatch.setenv("LABHUB_GITHUB_API", gurl)
    monkeypatch.setenv("LABHUB_GITHUB_REPO", "o/r")
    psrv, _, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "plat", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        owner = Session.login(url, "admin", "adminpw", save=False)
        v = owner.get("/api/platform/console/github")
        assert v["wanted"] == ["LabControl_v1.0.13_windows-setup.exe"] and v["auto"] is False
        r = owner.request("POST", "/api/platform/console/github/fetch", {"publish": False})
        t0 = time.time()
        while not (owner.get("/api/platform/console/github")["job"] or {}).get("done") and time.time() - t0 < 10:
            time.sleep(0.05)
        assert r["ok"] and [it["file"] for it in owner.get("/api/platform/console/items")["items"]] == \
            ["LabControl_v1.0.13_windows-setup.exe"]
        assert owner.request("PUT", "/api/platform/console/github/auto", {"auto": True})["auto"] is True
        assert owner.request("PUT", "/api/platform/console/github/auto", {"auto": False})["auto"] is False
        user = Session.login(url, "alice", "alicepw", save=False)
        with pytest.raises(Exception):
            user.get("/api/platform/console/github")
    finally:
        srv.shutdown()
        psrv.shutdown()
        gsrv.shutdown()
