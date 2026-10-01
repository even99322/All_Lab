"""設定：全部由環境變數控制，方便在 NAS 的 Docker 介面調整。"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("PAPERLIB_DATA", "/data")).resolve()
DB_PATH = DATA_DIR / "library.db"
FILES_DIR = DATA_DIR / "files"        # 論文 PDF：files/<paper_id>/<citekey>[_SM].pdf
THUMBS_DIR = DATA_DIR / "thumbs"      # 首頁縮圖：thumbs/<file_id>.jpg
STAGING_DIR = DATA_DIR / "staging"    # 上傳暫存（確認後才入庫）
TRASH_DIR = DATA_DIR / "trash"        # 刪除的論文先搬到這裡，不直接刪檔
BACKUP_DIR = DATA_DIR / "backups"

VERSION = "1.5.1"   # 程式版本：更新後可以在「頭像選單」與 /api/site 看到

SITE_NAME = os.environ.get("PAPERLIB_SITE_NAME", "LAB-QEL論文庫")  # 網站「管理 → 網站設定」可再覆寫
MAX_UPLOAD_MB = int(os.environ.get("PAPERLIB_MAX_UPLOAD_MB", "300"))
SESSION_DAYS = int(os.environ.get("PAPERLIB_SESSION_DAYS", "30"))
# 用 Crossref 補齊書目時附上的聯絡信箱（Crossref 建議提供，可留空）
CROSSREF_MAILTO = os.environ.get("PAPERLIB_CROSSREF_MAILTO", "")
# 反向代理（HTTPS）時設為 1，cookie 會加上 Secure
SECURE_COOKIE = os.environ.get("PAPERLIB_SECURE_COOKIE", "0") == "1"
# 背景工作（OCR、追蹤、摘要、Zotero）；多個 uvicorn worker 時只能讓一個跑
RUN_JOBS = os.environ.get("PAPERLIB_JOBS", "1") == "1"


def ensure_dirs() -> None:
    for d in (DATA_DIR, FILES_DIR, THUMBS_DIR, STAGING_DIR, TRASH_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
