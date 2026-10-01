"""選取文字翻譯。翻譯服務在「管理 → 網站設定」選擇，金鑰存在資料庫（不會傳回瀏覽器）。

支援：
- deepl        DeepL API（Free 金鑰結尾是 :fx，會自動用 api-free.deepl.com）
- google       Google Cloud Translation v2（API key）
- libre        LibreTranslate（可自架在 NAS 上，完全不出實驗室）
- anthropic    Claude（Anthropic API）
- openai       OpenAI 相容 API（OpenAI、或自架 Ollama／vLLM：網址填 http://<主機>:11434/v1）
"""
import hashlib
import json
import re
import urllib.error
import urllib.request

from . import db

PROVIDERS = {
    "": "未設定",
    "deepl": "DeepL",
    "google": "Google Cloud Translation",
    "libre": "LibreTranslate（自架）",
    "anthropic": "Claude（Anthropic API）",
    "openai": "OpenAI 相容（OpenAI／Ollama／vLLM）",
}
TARGETS = {"zh-TW": "繁體中文", "zh-CN": "簡體中文", "en": "英文", "ja": "日文"}
_DEEPL = {"zh-TW": "ZH-HANT", "zh-CN": "ZH-HANS", "en": "EN-US", "ja": "JA"}
_LIBRE = {"zh-TW": "zt", "zh-CN": "zh", "en": "en", "ja": "ja"}
_LLM = {"zh-TW": "繁體中文（台灣慣用語）", "zh-CN": "简体中文", "en": "English", "ja": "日本語"}
DEFAULT_MODEL = {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini"}
MAX_CHARS = 5000

SETTING_KEYS = ("tr_provider", "tr_key", "tr_url", "tr_model", "tr_target")


def settings(con) -> dict:
    s = {k: db.meta_get(con, k, "") or "" for k in SETTING_KEYS}
    s["tr_target"] = s["tr_target"] or "zh-TW"
    return s


def clean(text: str) -> str:
    """把 PDF 選取的文字整理成一段：接回行尾斷字、合併換行。"""
    t = text.replace("­", "")
    t = re.sub(r"(\w)[\-‐]\s*\n\s*([a-z])", r"\1\2", t)   # mag-\nnon → magnon
    t = re.sub(r"\s*\n\s*", " ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def _post(url: str, body: dict, headers: dict, timeout: float = 45.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "PaperLib/1.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"翻譯服務回應 {e.code}：{msg}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"連不到翻譯服務（{getattr(e, 'reason', e)}）。請確認 NAS 能連外網或服務網址正確") from None


def _llm_prompt(target: str) -> str:
    return (f"你是凝態物理（磁振子 magnon、腔量子電動力學、微波量測）領域的論文翻譯。"
            f"把使用者給的學術段落翻成{_LLM[target]}。"
            "專有名詞第一次出現時在譯詞後用括號保留英文，例如：磁振子（magnon）、例外點（exceptional point）。"
            "數學符號、公式、變數、單位、引用編號保持原樣。只輸出譯文，不要加任何說明。")


def _call(s: dict, text: str) -> str:
    prov, key, url, target = s["tr_provider"], s["tr_key"], s["tr_url"].rstrip("/"), s["tr_target"]
    model = s["tr_model"] or DEFAULT_MODEL.get(prov, "")
    if prov == "deepl":
        base = url or ("https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com")
        r = _post(base + "/v2/translate", {"text": [text], "target_lang": _DEEPL[target]},
                  {"Authorization": f"DeepL-Auth-Key {key}"})
        return r["translations"][0]["text"]
    if prov == "google":
        r = _post("https://translation.googleapis.com/language/translate/v2?key=" + key,
                  {"q": text, "target": target, "format": "text"}, {})
        return r["data"]["translations"][0]["translatedText"]
    if prov == "libre":
        if not url:
            raise RuntimeError("請先填 LibreTranslate 網址")
        body = {"q": text, "source": "auto", "target": _LIBRE[target], "format": "text"}
        if key:
            body["api_key"] = key
        return _post(url + "/translate", body, {})["translatedText"]
    if prov == "anthropic":
        r = _post((url or "https://api.anthropic.com") + "/v1/messages",
                  {"model": model, "max_tokens": 4096, "system": _llm_prompt(target),
                   "messages": [{"role": "user", "content": text}]},
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    if prov == "openai":
        h = {"Authorization": f"Bearer {key}"} if key else {}
        r = _post((url or "https://api.openai.com/v1") + "/chat/completions",
                  {"model": model, "temperature": 0.2,
                   "messages": [{"role": "system", "content": _llm_prompt(target)}, {"role": "user", "content": text}]}, h)
        return r["choices"][0]["message"]["content"].strip()
    raise RuntimeError("管理員還沒設定翻譯服務（管理 → 網站設定）")


def translate(con, text: str, use_cache: bool = True) -> dict:
    s = settings(con)
    text = clean(text)
    if not text:
        raise RuntimeError("沒有選到文字")
    if len(text) > MAX_CHARS:
        raise RuntimeError(f"一次最多翻譯 {MAX_CHARS} 字，請分段選取")
    sig = "|".join((s["tr_provider"], s["tr_model"], s["tr_target"], text))
    h = hashlib.sha256(sig.encode()).hexdigest()
    if use_cache:
        r = con.execute("SELECT out FROM translations WHERE hash=?", (h,)).fetchone()
        if r:
            return {"source": text, "text": r["out"], "provider": s["tr_provider"], "cached": True}
    out = _call(s, text)
    if not out:
        raise RuntimeError("翻譯服務沒有回傳內容")
    con.execute("INSERT OR REPLACE INTO translations(hash, provider, target, src, out, created_at) VALUES(?,?,?,?,?,?)",
                (h, s["tr_provider"], s["tr_target"], text, out, db.now()))
    return {"source": text, "text": out, "provider": s["tr_provider"], "cached": False}
