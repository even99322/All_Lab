"""1.0.7：Hub 網頁共用的頂端導覽（http://<NAS>:8765 登入後的每一頁）。

    模塊下載 /portal   數據查看 /data   量測查看 /live   電磁鐵 /magnet   量測設置 /setup   授權 /licenses   論文庫 ↗（外部連結）
    控制台、帳號與權限（站長）                                            使用者 · 登出

各頁的 HTML 放 ``<!--QEL-NAV-->``，送出前由 ``page()`` 換成導覽列（依登入者決定顯示哪些項目）。只用標準函式庫。

1.0.8：整個網站是一個可安裝的 App（跟論文庫一樣，用瀏覽器「安裝應用程式」）。``page()`` 也在 ``</head>`` 前補上
App 需要的標籤（manifest、圖示、iPhone/iPad 加入主畫面），導覽列有「⬇ 安裝 App」（已在 App 裡就不顯示），
並註冊 ``/sw.js``（只有 https 或 localhost 才會生效，http 也能裝，只是沒有離線畫面）。
"""
from __future__ import annotations

import html
import re
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
.qel-nav a.inst{color:#ffd43b}
.qel-nav a.inst:hover{background:#2b2a1f;color:#ffe066}
@media (display-mode:standalone),(display-mode:window-controls-overlay){.qel-nav a.inst{display:none}}
.qel-nav .own{font-size:11px;padding:0 6px;border-radius:6px;background:#1971c2;color:#fff;margin-left:4px}
</style>"""


# 每一頁都一樣的 App 標籤（manifest、圖示、iOS 加入主畫面）
HEAD = """<meta name="theme-color" content="#16181d">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="量子工程">
<meta name="application-name" content="量子工程平台">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/app-icon-32.png">
<link rel="apple-touch-icon" sizes="180x180" href="/app-icon-180.png">
"""

# 導覽列的安裝按鈕：瀏覽器提供安裝視窗就直接跳出，不然到 /install 看各裝置的步驟。
SCRIPT = """<script id="qel-app">(function(){
 var w=window; if(w.qelApp) return;
 var app=w.qelApp={prompt:null,standalone:!!(w.matchMedia&&(matchMedia('(display-mode: standalone)').matches||
   matchMedia('(display-mode: window-controls-overlay)').matches))||navigator.standalone===true};
 function upd(){var b=document.getElementById('nav-install');
   if(b){b.style.display=app.standalone?'none':'';b.textContent=app.installed&&!app.prompt?'✓ 已安裝 App':'⬇ 安裝 App';}
   document.dispatchEvent(new Event('qel-app'));}
 w.addEventListener('beforeinstallprompt',function(e){e.preventDefault();app.prompt=e;app.installed=false;mark(0);upd();});
 function mark(v){try{v?localStorage.setItem('qel.installed','1'):localStorage.removeItem('qel.installed');}catch(e){}}
 try{app.installed=localStorage.getItem('qel.installed')==='1';}catch(e){}
 if(app.standalone){app.installed=true;mark(1);}
 if(navigator.getInstalledRelatedApps)navigator.getInstalledRelatedApps().then(function(a){
   if(a.length&&!app.prompt){app.installed=true;mark(1);upd();}}).catch(function(){});
 w.addEventListener('appinstalled',function(){app.prompt=null;app.installed=true;mark(1);upd();});
 app.install=function(){if(!app.prompt)return Promise.resolve(false);var p=app.prompt;app.prompt=null;p.prompt();
   return p.userChoice.then(function(c){upd();return c&&c.outcome==='accepted';});};
 document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('#nav-install');
   if(a&&app.prompt){e.preventDefault();app.install();}});
 if('serviceWorker' in navigator&&w.isSecureContext)navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(function(){});
 document.addEventListener('DOMContentLoaded',upd);
})();</script>"""

_OLD_HEAD = re.compile(r'[ \t]*<(?:link rel="(?:manifest|icon|apple-touch-icon)"|meta name="(?:theme-color|mobile-web-app-capable|'
                       r'apple-mobile-web-app-[a-z-]+|application-name)")[^>]*>\n?')


def with_head(text: str) -> str:
    """把頁面自己的 manifest／圖示標籤換成全站共用的 HEAD＋SCRIPT（每頁裝起來都是同一個 App）。"""
    if 'id="qel-app"' in text or "</head>" not in text:
        return text
    text = _OLD_HEAD.sub("", text)
    return text.replace("</head>", HEAD + SCRIPT + "\n</head>", 1)


def items(h: Handler) -> List[Tuple[str, str, str, bool]]:
    """[(key, 文字, 網址, 外部連結?)]"""
    out = [("home", "模塊下載", "/portal#/", False), ("data", "數據查看", "/data", False),
           ("live", "量測查看", "/live", False), ("magnet", "電磁鐵", "/magnet", False),
           ("setup", "量測設置", "/setup", False), ("licenses", "授權", "/licenses", False)]
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
    links.append(f'<a class="i{" on" if active == "install" else " inst"}" id="nav-install" data-v="install" href="/install"'
                 ' title="把這個網站裝成 App">⬇ 安裝 App</a>')
    owner = '<span class="own">站長</span>' if u.get("owner") else ""
    return (STYLE + '<div class="qel-nav" id="nav"><a class="b" href="/portal#/"><img src="/icon.svg" alt="">量子工程平台</a>'
            + "".join(links) + f'<span class="sp"></span><span class="u">{html.escape(str(name))}{owner}'
            + '<a href="/logout">登出</a></span></div>')


def page(h: Handler, file: str, active: str) -> None:
    text = (STATIC / file).read_text(encoding="utf-8")
    h._html(with_head(text.replace("<!--QEL-NAV-->", nav_html(h, active), 1)))
