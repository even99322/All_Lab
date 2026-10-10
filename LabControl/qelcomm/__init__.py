"""qelcomm：量子工程平台的通信模塊（每個模塊都帶一份，只用 Python 標準函式庫，3.8 以上）。

所有模塊之間、模塊與 NAS 中繼站（Hub）之間的溝通都走這裡，模塊本身不直接碰網址與 token::

    from qelcomm import Session

    s = Session.current()                       # 啟動器登入後存的 session（或環境變數 QEL_GATEWAY / QEL_TOKEN）
    s = Session.login("http://100.114.33.20:8765", "alice", "密碼")    # 用論文庫帳號登入（第一次）

    s.tags()                                    # 所有模塊共用的標籤（含論文數、數據數、論文庫連結）
    s.papers(["transmon"])                      # 標籤對應的論文（每篇有 url，點了直接到論文庫）
    s.catalog(tags=["transmon"])                # 數據目錄
    s.upload("LAB-1", "2026/10/a.hdf5", path, tags=["transmon"], meta={...})
    s.download(item, "~/Downloads")             # 下載數據檔
    s.send("lablogviewer", "open", catalog_id=…) # 叫讀檔模塊開檔（同一個人登入中的任何電腦 / 手機）
    for m in s.listen("labcontrol"): ...        # 收別的模塊送來的指令

拖放（量測 ↔ 讀檔 ↔ 網頁）共用 :mod:`qelcomm.dnd` 的格式。
"""
from .session import (DEFAULT_GATEWAY_ENV, DEFAULT_TOKEN_ENV, Listener, QelAuthError, QelError, Session,
                      session_file)

__version__ = "0.0.16"
__all__ = ["Session", "Listener", "QelError", "QelAuthError", "session_file", "DEFAULT_GATEWAY_ENV",
           "DEFAULT_TOKEN_ENV", "__version__"]
