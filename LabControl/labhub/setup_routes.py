"""1.0.7：Hub 網頁「量測設置」（/setup）與網頁導覽用的路由。

    GET /install 1.0.8：把網站裝成 App（跟論文庫一樣）：一鍵安裝＋各裝置步驟；/app-icon-*.png、/favicon.ico 是 App 圖示
    GET /setup   量測電腦與儀器狀態、用範本編輯量測方案並送到量測電腦執行、量測電腦對應與 instruments.yaml（站長）

頁面本身只呼叫既有的 API：/api/state（量測電腦狀態）與 /api/nodes/<名稱>/commands（節點指令：catalog、templates、
check_scheme、run_scheme、pause / resume / stop、instruments_file、save_instruments、save_lab_nodes）。
權限跟 /nodes 一樣（裝置 token，或有量測權限的論文庫帳號）；改設定檔的指令節點只接受站長。
"""
from __future__ import annotations

from .server import Handler, route
from .web_nav import page


@route("GET", r"/setup/?", auth="any")
def _setup(h: Handler) -> None:
    page(h, "setup.html", "setup")


# ---- 1.0.8：裝成 App（跟論文庫一樣用瀏覽器「安裝應用程式」）---------------------------------
@route("GET", r"/install/?", auth="any")
def _install(h: Handler) -> None:
    page(h, "install.html", "install")


@route("GET", r"/app-icon-(32|180|192|512|maskable-512)\.png", auth=False)
def _app_icon(h: Handler, size: str) -> None:
    from .server import STATIC

    h._send(200, (STATIC / f"app-icon-{size}.png").read_bytes(), "image/png")


@route("GET", r"/(?:favicon\.ico|apple-touch-icon(?:-precomposed)?\.png)", auth=False)
def _favicon(h: Handler) -> None:
    """瀏覽器和 iOS 會自己來要這幾個固定網址。"""
    from .server import STATIC

    name = "app-icon-32.png" if "favicon" in h.path else "app-icon-180.png"
    h._send(200, (STATIC / name).read_bytes(), "image/png")
