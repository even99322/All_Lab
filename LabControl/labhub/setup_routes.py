"""1.0.7：Hub 網頁「量測設置」（/setup）與網頁導覽用的路由。

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
