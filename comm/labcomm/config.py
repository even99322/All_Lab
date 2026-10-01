"""連線設定與登入狀態。

優先順序：
1. 環境變數 ``QEL_PORTAL_URL``、``QEL_TOKEN``（桌面大程式啟動模塊時會帶上）；
2. ``<QEL_HOME>/session.json``（桌面大程式登入後寫入，只有本人可讀）；
3. 預設網址 ``DEFAULT_PORTAL_URLS``。

``QEL_HOME`` 預設是 ``~/QELLab``（Windows：``C:\\Users\\<你>\\QELLab``）。
網址可以寫多個，用逗號分隔（例如內網、VPN），依序嘗試。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_PORTAL_URLS = "http://192.168.50.2:8090, http://100.114.33.20:8090"
SESSION_FILE = "session.json"


def qel_home() -> Path:
    p = os.environ.get("QEL_HOME")
    return Path(p).expanduser() if p else Path.home() / "QELLab"


def split_urls(text: str) -> List[str]:
    out = []
    for part in (text or "").replace(";", ",").split(","):
        u = part.strip().rstrip("/")
        if not u:
            continue
        if "://" not in u:
            u = "http://" + u
        out.append(u)
    return out


@dataclass
class CommConfig:
    portal_url: List[str] = field(default_factory=lambda: split_urls(DEFAULT_PORTAL_URLS))
    token: str = ""
    user: Optional[dict] = None
    timeout: float = 10.0


def _session_path() -> Path:
    return qel_home() / SESSION_FILE


def load_config() -> CommConfig:
    cfg = CommConfig()
    data = {}
    try:
        data = json.loads(_session_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if data.get("portal_url"):
        cfg.portal_url = split_urls(data["portal_url"]) or cfg.portal_url
    cfg.token = str(data.get("token") or "")
    cfg.user = data.get("user") if isinstance(data.get("user"), dict) else None
    env_url = os.environ.get("QEL_PORTAL_URL")
    if env_url:
        cfg.portal_url = split_urls(env_url) or cfg.portal_url
    if os.environ.get("QEL_TOKEN"):
        cfg.token = os.environ["QEL_TOKEN"]
    try:
        cfg.timeout = float(os.environ.get("QEL_TIMEOUT", cfg.timeout))
    except ValueError:
        pass
    return cfg


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    if sys.platform != "win32":
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_session(portal_url: "str | List[str]", token: str, user: Optional[dict] = None) -> Path:
    url = portal_url if isinstance(portal_url, str) else ", ".join(portal_url)
    p = _session_path()
    _write_private(p, json.dumps({"portal_url": url, "token": token, "user": user or {}},
                                 ensure_ascii=False, indent=2))
    return p


def clear_session() -> None:
    p = _session_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    data["token"] = ""
    data["user"] = {}
    _write_private(p, json.dumps(data, ensure_ascii=False, indent=2))
