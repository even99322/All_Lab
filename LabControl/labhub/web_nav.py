"""1.0.7：Hub 網頁共用的頂端導覽（http://<NAS>:8765 登入後的每一頁）。

    模塊下載 /portal   數據查看 /data   量測查看 /live   量測設置 /setup   論文庫 ↗（外部連結）
    控制台、帳號與權限（站長）                                            使用者 · 登出

各頁的 HTML 放 ``<!--QEL-NAV-->``，送出前由 ``page()`` 換成導覽列（依登入者決定顯示哪些項目）。只用標準函式庫。
"""
from __future__ import annotations

import html
from typing import List, Tuple

from .server import STATIC, Handler

STYLE = """<style id="qel-nav-style">
.qel-nav{position:relative;z-index:30;display:flex;align-items:center;gap:6px;flex-wrap:nowrap;overflow-x:auto;
 padding:6px 12px;padding-top:max(6px,env(safe-area-inset-top));background:#16181d;color:#e9ecef;scrollbar-width:none;
 font:14px/1.4 -apple-system,"Segoe UI","PingFang TC","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif}
.qel-nav::-webkit-scrollbar{display:none}
.qel-nav .b{display:flex;align-items:center;gap:6px;font-weight:700;margin-right:6px;white-space:nowrap;color:#fff;text-decoration:none}
.qel-nav .b img{width:20px;height:20px}
.qel-nav a.i{flex:none;padding:5px 11px;border-radius:8px;color:#aab1bb;font-weight:600;text-decoration:none;white-space:nowrap}
.qel-nav a.i:hover{color:#fff;background:#262a31}
.qel-nav a.i.on{color:#fff;background:#1971c2}
.qel-nav .sp{flex:1;min-width:8px}
.qel-nav .u{flex:none;color:#aab1bb;font-size:13px;white-space:nowrap}
.qel-nav .u a{color:#74c0fc;text-decoration:none;margin-left:8px}
.qel-nav .own{font-size:11px;padding:0 6px;border-radius:6px;background:#1971c2;color:#fff;margin-left:4px}
</style>"""


def items(h: Handler) -> List[Tuple[str, str, str, bool]]:
    """[(key, 文字, 網址, 外部連結?)]"""
    out = [("home", "模塊下載", "/portal#/", False), ("data", "數據查看", "/data", False),
           ("live", "量測查看", "/live", False), ("setup", "量測設置", "/setup", False)]
    plat = getattr(h.hub, "platform", None)
    paper = plat.paperlib_public if plat is not None and getattr(plat, "paperlib", None) is not None else ""
    if paper:
        out.append(("paper", "論文庫 ↗", paper, True))
    u = h.user()
    if u.get("owner"):
        out += [("console", "控制台", "/portal#/console", False), ("admin", "帳號與權限", "/portal#/admin", False)]
    return out


def nav_html(h: Handler, active: str) -> str:
    u = h.user()
    name = u.get("display_name") or u.get("username") or ("量測電腦" if h.principal() else "")
    links = []
    for key, text, url, external in items(h):
        attrs = ' target="_blank" rel="noopener"' if external else ""
        ident = f' id="nav-{key}"' if key in ("console", "admin") else ""
        links.append(f'<a class="i{" on" if key == active else ""}" data-v="{key}"{ident} href="{html.escape(url)}"'
                     f'{attrs}>{html.escape(text)}</a>')
    owner = '<span class="own">站長</span>' if u.get("owner") else ""
    return (STYLE + '<div class="qel-nav" id="nav"><a class="b" href="/portal#/"><img src="/icon.svg" alt="">量子工程平台</a>'
            + "".join(links) + f'<span class="sp"></span><span class="u">{html.escape(str(name))}{owner}'
            + '<a href="/logout">登出</a></span></div>')


def page(h: Handler, file: str, active: str) -> None:
    text = (STATIC / file).read_text(encoding="utf-8")
    h._html(text.replace("<!--QEL-NAV-->", nav_html(h, active), 1))
