"""連線 NAS 共用資料夾（Windows）。

密碼交給 Windows 認證管理員（cmdkey）保存，Lab Control 不會把密碼寫進任何檔案。
macOS 請用 Finder「前往 → 連接伺服器」輸入 smb://<NAS>/<share>（Lab APP 也會自動掛載）。
"""
from __future__ import annotations

import os
import subprocess
from typing import Tuple


def _run(cmd, timeout: float = 30) -> Tuple[int, str]:
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def login(host: str, share: str, user: str, password: str, remember: bool = True) -> str:
    if os.name != "nt":
        raise RuntimeError("macOS / Linux 請用 Finder「前往 → 連接伺服器」輸入 "
                           f"smb://{host}/{share}（或讓 Lab APP 自動掛載）")
    unc = f"\\\\{host}\\{share}"
    msgs = []
    if remember:
        rc, out = _run(["cmdkey", f"/add:{host}", f"/user:{user}", f"/pass:{password}"])
        msgs.append(out)
    _run(["net", "use", unc, "/delete", "/y"], timeout=10)          # 換帳號前先中斷舊連線
    rc, out = _run(["net", "use", unc, password, f"/user:{user}", f"/persistent:{'yes' if remember else 'no'}"])
    msgs.append(out)
    if rc != 0:
        raise RuntimeError(f"連線 {unc} 失敗：\n{out}")
    return "\n".join(m for m in msgs if m)
