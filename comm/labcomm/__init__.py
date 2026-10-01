"""labcomm：QEL Lab 平台的通信模塊。

所有模塊（論文、量測、讀檔、桌面大程式、監控程式）之間的溝通都只經過這個套件：

* ``PortalClient``    —— 連 NAS 上的大程式伺服器（portal）：登入、權限、共用標籤、數據登錄、論文、事件、Hub 中繼。
* ``local``           —— 同一台電腦上模塊之間的直接傳遞（例如把數據檔丟給量測模塊套用設置）。
* ``handoff``         —— 數據檔 ↔ 量測設置：寫入 / 讀出數據檔裡的量測方案與標籤。
* ``tags``            —— 全平台共用的標籤分類與名稱正規化（離線時用本機快取）。

只用 Python 標準函式庫（讀寫 HDF5 時才需要 h5py），可以單獨更新，不影響其他模塊。
協定版本 ``PROTOCOL`` 只在不相容時遞增；新增欄位不算不相容。
"""
from __future__ import annotations

__version__ = "1.0.0"
PROTOCOL = 1
MODULE_ID = "labcomm"

from .errors import CommError, NotLoggedIn, PermissionDenied, Unreachable  # noqa: E402
from .config import CommConfig, load_config, save_session, clear_session, qel_home  # noqa: E402
from .client import PortalClient  # noqa: E402
from . import actions, handoff, local, tags  # noqa: E402

__all__ = [
    "__version__", "PROTOCOL", "CommError", "NotLoggedIn", "PermissionDenied", "Unreachable",
    "CommConfig", "load_config", "save_session", "clear_session", "qel_home", "PortalClient",
    "actions", "handoff", "local", "tags", "connect",
]


def connect(config: "CommConfig | None" = None) -> PortalClient:
    """用目前的設定（環境變數 → 大程式寫的 session 檔）建立連線；沒有登入時 token 為空。"""
    cfg = config or load_config()
    return PortalClient(cfg.portal_url, cfg.token, timeout=cfg.timeout)
