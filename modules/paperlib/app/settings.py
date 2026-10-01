"""網站設定：全部存在資料庫 meta 表，在「管理」頁修改。金鑰類欄位不會傳回瀏覽器。"""
from . import config, db

DEFAULTS = {
    # 網站
    "site_name": "", "site_url": "",
    # 翻譯
    "tr_provider": "", "tr_key": "", "tr_url": "", "tr_model": "", "tr_target": "zh-TW",
    # AI（預填重點欄、問答）
    "ai_provider": "", "ai_key": "", "ai_url": "", "ai_model": "", "ai_max_chars": "60000", "lab_context": "",
    # 新論文追蹤
    "feed_enabled": "1", "feed_hour": "7",
    # 通知與每週摘要
    "smtp_host": "", "smtp_port": "587", "smtp_user": "", "smtp_pass": "", "smtp_from": "", "smtp_security": "starttls",
    "digest_enabled": "0", "digest_dow": "0", "digest_hour": "9", "webhook_url": "",
    # Zotero
    "zotero_type": "user", "zotero_id": "", "zotero_key": "", "zotero_collection": "", "zotero_auto": "0",
    "zotero_import": "1",
    # OCR
    "ocr_auto": "1", "ocr_langs": "eng",
    # 語意搜尋的向量模型（選用；空白＝內建 TF-IDF）
    "emb_provider": "", "emb_url": "", "emb_key": "", "emb_model": "",
    # 預印本：定期檢查是否已正式發表，找到就自動更新書目
    "pub_check": "1", "pub_auto": "1",
    # 期刊搜尋：OpenAlex 金鑰（免費，留空則改用 Crossref）
    "openalex_key": "",
    # 註冊：登入頁可以申請帳號，站長審核
    "register_enabled": "1",
}
SECRETS = {"tr_key", "ai_key", "smtp_pass", "zotero_key", "webhook_url", "emb_key", "openalex_key"}


def get(con=None) -> dict:
    con = con or db.get()
    rows = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
    s = {k: (rows.get(k) if rows.get(k) is not None else v) for k, v in DEFAULTS.items()}
    s["site_name"] = s["site_name"] or config.SITE_NAME
    s["zotero_collection"] = s["zotero_collection"] or s["site_name"]
    return s


def public(con=None) -> dict:
    s = get(con)
    out = {k: v for k, v in s.items() if k not in SECRETS}
    out.update({f"has_{k}": bool(s[k]) for k in SECRETS})
    return out


def update(con, b: dict) -> None:
    for k, v in b.items():
        if k not in DEFAULTS:
            continue
        v = "" if v is None else str(v).strip()
        if k in SECRETS and not v and not b.get(f"clear_{k}"):
            continue  # 空白＝沿用原本的金鑰
        if isinstance(b.get(k), bool):
            v = "1" if b[k] else "0"
        db.meta_set(con, k, v[:5000])
    for k in SECRETS:
        if b.get(f"clear_{k}"):
            db.meta_set(con, k, "")


def link(path: str, con=None) -> str:
    """給 email／Webhook／Zotero 用的完整網址；沒設定網站網址時回傳空字串。"""
    base = get(con)["site_url"].rstrip("/")
    return f"{base}/{path.lstrip('/')}" if base else ""
