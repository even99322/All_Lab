"""1.0.8：Hub 網站裝成 App（跟論文庫一樣用瀏覽器「安裝應用程式」；Windows / Mac / Android / iPhone / iPad）。"""
from __future__ import annotations

import json
import struct
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_104 import _get
from tests.test_remote import TOKEN, hubsrv  # noqa: F401

COOKIE = {"Cookie": f"labhub_token={TOKEN}"}
ROOT = Path(__file__).resolve().parents[1]


def _png_size(body: bytes):
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", body[16:24])


def _base(hubsrv):
    return hubsrv[0].url if hasattr(hubsrv[0], "url") else hubsrv[0].base


def test_manifest_is_installable(hubsrv):
    base = _base(hubsrv)
    code, body, hdr = _get(base + "/manifest.webmanifest")
    assert code == 200 and "manifest+json" in hdr["Content-Type"]
    m = json.loads(body)
    assert m["id"] == "/portal" and m["start_url"] == "/portal" and m["scope"] == "/"
    assert m["display"] == "standalone" and m["name"] == "量子工程平台" and m["short_name"]
    pngs = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"] if i["type"] == "image/png"}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= pngs
    assert [s["url"] for s in m["shortcuts"]][:3] == ["/data", "/live", "/setup"]
    for icon in m["icons"]:                                         # 每個圖示都拿得到、尺寸對
        code, body, _ = _get(base + icon["src"])
        assert code == 200, icon["src"]
        if icon["type"] == "image/png":
            w, h = _png_size(body)
            assert f"{w}x{h}" == icon["sizes"]
    for path, size in (("/app-icon-180.png", 180), ("/app-icon-32.png", 32), ("/favicon.ico", 32),
                       ("/apple-touch-icon.png", 180)):
        code, body, _ = _get(base + path)
        assert code == 200 and _png_size(body) == (size, size), path


def test_service_worker_never_caches_api(hubsrv):
    code, body, hdr = _get(_base(hubsrv) + "/sw.js")
    text = body.decode("utf-8")
    assert code == 200 and hdr["Service-Worker-Allowed"] == "/" and "javascript" in hdr["Content-Type"]
    assert '"/api/"' in text and "/login" in text and "qel-app-v2" in text
    for page in ("/portal", "/data", "/live", "/setup", "/install"):
        assert f'"{page}"' in text


def test_every_page_is_the_same_app(hubsrv):
    base = _base(hubsrv)
    pages = ["/portal", "/data", "/live", "/setup", "/install", "/m", "/m/data", "/get"]
    for path in pages + ["/login"]:
        code, body, _ = _get(base + path, {} if path == "/login" else COOKIE)
        text = body.decode("utf-8")
        assert code == 200, path
        head = text.split("</head>")[0]
        assert head.count('rel="manifest"') == 1 and 'href="/manifest.webmanifest"' in head, path
        assert "/m/manifest" not in head and head.count('name="theme-color"') == 1, path
        assert 'rel="apple-touch-icon" sizes="180x180" href="/app-icon-180.png"' in head, path
        assert 'name="apple-mobile-web-app-capable" content="yes"' in head and 'id="qel-app"' in head, path
        assert "beforeinstallprompt" in head and "serviceWorker" in head, path
    for path in ("/portal", "/data", "/live", "/setup", "/install"):
        text = _get(base + path, COOKIE)[1].decode("utf-8")
        nav = text.split('class="qel-nav"')[1].split("</div>")[0]
        assert 'id="nav-install"' in nav and 'href="/install"' in nav, path
    text = _get(base + "/install", COOKIE)[1].decode("utf-8")
    assert 'class="i on" id="nav-install"' in text
    for label in ("Windows / Mac · Chrome", "Windows / Mac · Edge", "Mac · Safari", "Android", "iPhone / iPad",
                  "加入主畫面", "將此網站安裝為應用程式"):
        assert label in text, label


def test_install_page_needs_login(hubsrv):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        urllib.request.build_opener(NoRedirect).open(_base(hubsrv) + "/install", timeout=5)
        raise AssertionError("should redirect")
    except urllib.error.HTTPError as e:
        assert e.code == 303 and e.headers["Location"].startswith("/login")


def test_with_head_is_idempotent_and_replaces_old_tags():
    import labhub.server  # noqa: F401  （先載入 server，web_nav 才不會循環 import）
    from labhub.web_nav import with_head

    old = ('<html><head><meta name="theme-color" content="#000">\n<link rel="manifest" href="/m/manifest.webmanifest">\n'
           '<link rel="apple-touch-icon" href="/m/icon-180.png">\n<title>x</title></head><body></body></html>')
    once = with_head(old)
    assert with_head(once) == once
    assert "/m/manifest" not in once and "/m/icon-180" not in once and once.count('rel="manifest"') == 1
    assert with_head("no head here") == "no head here"


def test_static_files_ship_with_the_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"labhub" = ["static/*"]' in text
    static = ROOT / "labhub" / "static"
    for name in ("manifest.webmanifest", "sw.js", "install.html", "app-icon-192.png", "app-icon-512.png",
                 "app-icon-maskable-512.png", "app-icon-180.png", "app-icon-32.png"):
        assert (static / name).is_file(), name
