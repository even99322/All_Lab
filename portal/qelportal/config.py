"""設定：全部由環境變數控制（docker-compose.yml 的 environment）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class PortalConfig:
    data_dir: Path = Path("./portal-data")
    port: int = 8090
    host: str = "0.0.0.0"
    site_name: str = "QEL Lab"
    paperlib_url: str = "http://127.0.0.1:8080"          # 容器之間：http://paperlib:8080
    paperlib_public_url: str = ""                         # 給瀏覽器的論文庫網址；空白＝論文庫「網站網址」設定或同主機 :8080
    hub_url: str = "http://127.0.0.1:8765"               # Lab Control Hub
    hub_token: Optional[str] = None
    agent_url: str = ""                                   # 更新代理（監控程式用；空白＝不顯示）
    session_days: int = 30
    cookie_domain: str = ""                               # 例如 .lab.example.org（Cloudflare 子網域共用登入）
    sso: bool = True                                      # 網頁登入時一併登入論文庫（同主機不同埠 cookie 共用）
    secure_cookie: bool = False
    recheck_s: float = 600.0                              # 每隔幾秒向論文庫確認帳號仍然有效
    max_release_mb: int = 1024
    data_roots: Dict[str, str] = field(default_factory=dict)   # 數據路徑前綴 → 容器內掛載路徑（網頁下載用）
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "PortalConfig":
        roots: Dict[str, str] = {}
        for item in _env("QEL_DATA_ROOTS").split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                if k.strip() and v.strip():
                    roots[k.strip()] = v.strip()
        tok = _env("LABHUB_TOKEN")
        return cls(
            data_dir=Path(_env("QEL_PORTAL_DATA", "./portal-data")),
            port=int(_env("QEL_PORTAL_PORT", "8090")),
            site_name=_env("QEL_SITE_NAME", "QEL Lab"),
            paperlib_url=_env("PAPERLIB_URL", "http://127.0.0.1:8080").rstrip("/"),
            paperlib_public_url=_env("PAPERLIB_PUBLIC_URL").rstrip("/"),
            hub_url=_env("LABHUB_URL", "http://127.0.0.1:8765").rstrip("/"),
            hub_token=tok or None,
            agent_url=_env("QEL_AGENT_URL").rstrip("/"),
            session_days=int(_env("QEL_SESSION_DAYS", "30")),
            cookie_domain=_env("QEL_COOKIE_DOMAIN"),
            sso=_env("QEL_SSO", "on").lower() not in ("0", "off", "false"),
            secure_cookie=_env("QEL_SECURE_COOKIE", "0") == "1",
            recheck_s=float(_env("QEL_RECHECK_S", "600")),
            max_release_mb=int(_env("QEL_MAX_RELEASE_MB", "1024")),
            data_roots=roots,
            log_level=_env("QEL_LOG", "INFO"),
        )
