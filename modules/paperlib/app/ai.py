"""AI 功能：用大型語言模型預填重點欄、回答「問論文庫」。

服務在「管理 → 網站與 AI」設定：Claude（Anthropic API）或 OpenAI 相容（含自架 Ollama）。
「沿用翻譯設定」時使用翻譯那組金鑰（翻譯服務須是 Claude 或 OpenAI 相容）。
"""
import json
import re

from . import db, settings
from .jobs import handler
from .translate import _post

KEYINFO_FIELDS = ["重點", "架設／平台", "關鍵參數", "可萃取特徵／觀測量", "與我們實驗的關係"]
DEFAULT_MODEL = {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini"}
PROVIDERS = {"": "未設定", "same": "沿用翻譯設定", "anthropic": "Claude（Anthropic API）", "openai": "OpenAI 相容（OpenAI／Ollama／vLLM）"}


def config(con) -> dict:
    s = settings.get(con)
    if s["ai_provider"] == "same":
        if s["tr_provider"] not in ("anthropic", "openai"):
            raise RuntimeError("翻譯服務不是 Claude 或 OpenAI 相容，無法沿用；請在「管理 → 網站與 AI」另外設定 AI 服務")
        return {"provider": s["tr_provider"], "key": s["tr_key"], "url": s["tr_url"].rstrip("/"),
                "model": s["tr_model"] or DEFAULT_MODEL[s["tr_provider"]], "max_chars": int(s["ai_max_chars"] or 60000),
                "lab": s["lab_context"]}
    if s["ai_provider"] not in ("anthropic", "openai"):
        raise RuntimeError("管理員還沒設定 AI 服務（管理 → 網站與 AI）")
    return {"provider": s["ai_provider"], "key": s["ai_key"], "url": s["ai_url"].rstrip("/"),
            "model": s["ai_model"] or DEFAULT_MODEL[s["ai_provider"]], "max_chars": int(s["ai_max_chars"] or 60000),
            "lab": s["lab_context"]}


def enabled(con) -> bool:
    try:
        config(con)
        return True
    except RuntimeError:
        return False


def chat(con, system: str, user: str, max_tokens: int = 2000) -> str:
    c = config(con)
    if c["provider"] == "anthropic":
        r = _post((c["url"] or "https://api.anthropic.com") + "/v1/messages",
                  {"model": c["model"], "max_tokens": max_tokens, "system": system,
                   "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": c["key"], "anthropic-version": "2023-06-01"}, timeout=180)
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    h = {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}
    r = _post((c["url"] or "https://api.openai.com/v1") + "/chat/completions",
              {"model": c["model"], "temperature": 0.2, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, h, timeout=180)
    return r["choices"][0]["message"]["content"].strip()


def vision(con, system: str, user: str, png: bytes, max_tokens: int = 1500) -> str:
    """看圖回答（公式轉 LaTeX 用）。模型要支援影像輸入：Claude 都可以；OpenAI 相容需選有視覺能力的模型。"""
    import base64
    c = config(con)
    b64 = base64.b64encode(png).decode()
    if c["provider"] == "anthropic":
        r = _post((c["url"] or "https://api.anthropic.com") + "/v1/messages",
                  {"model": c["model"], "max_tokens": max_tokens, "system": system,
                   "messages": [{"role": "user", "content": [
                       {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                       {"type": "text", "text": user}]}]},
                  {"x-api-key": c["key"], "anthropic-version": "2023-06-01"}, timeout=180)
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    h = {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}
    r = _post((c["url"] or "https://api.openai.com/v1") + "/chat/completions",
              {"model": c["model"], "temperature": 0, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": [
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                   {"type": "text", "text": user}]}]}, h, timeout=180)
    return r["choices"][0]["message"]["content"].strip()


def _json_from(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text
    start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("AI 沒有回傳 JSON")
    end = max(raw.rfind("}"), raw.rfind("]"))
    return json.loads(raw[start:end + 1])


def _paper_text(con, pid: int, limit: int) -> tuple[dict, str]:
    p = dict(con.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone())
    body = con.execute("SELECT body FROM fts WHERE paper_id=?", (pid,)).fetchone()
    body = (body["body"] if body else "") or ""
    # 參考文獻對預填沒有幫助，截掉以省字數
    from .refs import REF_HEAD
    heads = list(REF_HEAD.finditer(body))
    if heads and heads[-1].start() > len(body) * 0.5:
        body = body[:heads[-1].start()]
    return p, body[:limit]


def _lab(c) -> str:
    return f"\n實驗室背景（用來判斷「與我們實驗的關係」）：{c['lab']}" if c["lab"].strip() else ""


def suggest_keyinfo(con, pid: int) -> dict:
    c = config(con)
    p, body = _paper_text(con, pid, c["max_chars"])
    if len(body) < 200 and not p["abstract"]:
        raise RuntimeError("這篇沒有可讀的全文（可能是掃描檔，請先做 OCR）")
    cur = json.loads(p["keyinfo"] or "{}")
    fields = list(dict.fromkeys(list(cur.keys()) + KEYINFO_FIELDS))
    fields = [f for f in fields if f != "備註"]
    system = ("你是凝態物理實驗室（磁振子 magnon、腔／波導量子電動力學、非厄米物理、VNA 微波量測）的研究助理。"
              "閱讀論文後，用繁體中文（台灣用語）填寫重點欄。專有名詞保留英文，數值保留單位與符號。"
              "每欄 1–4 句，精確、具體，不要空泛；論文沒提到的欄位填空字串。只輸出一個 JSON 物件。" + _lab(c))
    user = (f"欄位：{json.dumps(fields, ensure_ascii=False)}\n"
            "欄位說明：重點＝主要結果與新意；架設／平台＝實驗或理論系統（元件、材料、幾何、溫度）；"
            "關鍵參數＝耦合強度、損耗、頻率、磁場等具體數值；可萃取特徵／觀測量＝從 S21／反射頻譜等能量到的特徵"
            "（例：anticrossing gap、線寬、EP 位置、非互易比）；與我們實驗的關係＝可借鏡或比較之處。\n\n"
            f"標題：{p['title']}\n期刊：{p['venue']} {p['year'] or ''}\n摘要：{p['abstract']}\n\n全文：\n{body}")
    out = _json_from(chat(con, system, user, 2500))
    if not isinstance(out, dict):
        raise ValueError("AI 回傳格式不符")
    return {str(k).strip(): str(v).strip() for k, v in out.items() if str(k).strip() and str(v).strip()}


# ------------------------------------------------------------------ 問論文庫
def _keywords(con, question: str) -> list[str]:
    system = ("把使用者的問題轉成用來全文搜尋英文物理論文的關鍵字。輸出 JSON 陣列，4–8 個英文關鍵字或短語"
              "（例：\"level attraction\", \"magnon polariton\", \"exceptional point\"），不要其他文字。")
    try:
        kws = _json_from(chat(con, system, question, 300))
        return [str(k).strip() for k in kws if str(k).strip()][:8]
    except Exception:  # noqa: BLE001 - 關鍵字失敗就用原問題的英文字
        return re.findall(r"[A-Za-z][A-Za-z\-]{3,}", question)[:8]


def _passages(body: str, kws: list[str], n: int = 3, width: int = 600) -> list[str]:
    out, used = [], []
    low = body.lower()
    for k in kws:
        for m in re.finditer(re.escape(k.lower()), low):
            a = max(0, m.start() - width // 2)
            if any(abs(a - u) < width for u in used):
                continue
            used.append(a)
            out.append(re.sub(r"\s+", " ", body[a:a + width]))
            if len(out) >= n:
                return out
    return out


def retrieve(con, kws: list[str], cat: int | None = None, limit: int = 8) -> list[int]:
    score = {}
    for k in kws:
        if len(k) < 3:
            continue
        q = '"' + k.replace('"', '""') + '"'
        try:
            rows = con.execute("SELECT paper_id, bm25(fts, 0, 10.0, 5.0, 2.0, 1.0, 4.0) r FROM fts WHERE fts MATCH ? "
                               "ORDER BY r LIMIT 30", (q,)).fetchall()
        except Exception:  # noqa: BLE001
            continue
        for rank, r in enumerate(rows):
            score[int(r["paper_id"])] = score.get(int(r["paper_id"]), 0) + 1.0 / (rank + 3)
    ids = sorted(score, key=lambda i: -score[i])
    if cat:
        inc = {r["paper_id"] for r in con.execute("SELECT paper_id FROM paper_categories WHERE category_id=?", (cat,))}
        ids = [i for i in ids if i in inc]
    return ids[:limit]


def ask(con, user_id: int, question: str, pid: int | None = None, cat: int | None = None) -> dict:
    c = config(con)
    question = question.strip()[:2000]
    if not question:
        raise RuntimeError("請輸入問題")
    blocks, refs = [], []
    if pid:
        p, body = _paper_text(con, pid, c["max_chars"])
        refs.append({"id": p["id"], "citekey": p["citekey"], "title": p["title"]})
        ki = json.loads(p["keyinfo"] or "{}")
        blocks.append(f"[{p['citekey']}] {p['title']}（{p['venue']} {p['year'] or ''}）\n重點欄：{json.dumps(ki, ensure_ascii=False)}\n"
                      f"摘要：{p['abstract']}\n全文：\n{body}")
        kws = []
    else:
        kws = _keywords(con, question)
        ids = retrieve(con, kws, cat)
        per = max(2000, min(9000, c["max_chars"] // max(1, len(ids) or 1)))
        for i in ids:
            p = con.execute("SELECT id, citekey, title, venue, year, abstract, keyinfo FROM papers WHERE id=?", (i,)).fetchone()
            body = (con.execute("SELECT body FROM fts WHERE paper_id=?", (i,)).fetchone() or {"body": ""})["body"] or ""
            notes = [r["body"] for r in con.execute("SELECT body FROM annotations WHERE paper_id=? AND private=0 AND body!='' LIMIT 8", (i,))]
            ps = _passages(body, kws, n=4, width=min(900, per // 4))
            blocks.append(f"[{p['citekey']}] {p['title']}（{p['venue']} {p['year'] or ''}）\n重點欄：{p['keyinfo']}\n"
                          f"摘要：{p['abstract'][:1500]}\n實驗室筆記：{' / '.join(notes)[:800]}\n相關段落：\n- " + "\n- ".join(ps))
            refs.append({"id": p["id"], "citekey": p["citekey"], "title": p["title"]})
        if not blocks:
            ans = "論文庫裡找不到和這個問題相關的論文（搜尋關鍵字：" + "、".join(kws) + "）。可以換個說法，或確認相關論文已上傳。"
            _save(con, user_id, question, ans, [], "")
            return {"answer": ans, "refs": [], "keywords": kws}
    system = ("你是凝態物理實驗室的研究助理，只根據提供的論文資料回答，用繁體中文（台灣用語），專有名詞保留英文。"
              "每個論點後面用 [citekey] 標出處（只能用資料中出現的 citekey）。資料不足以回答時要直說，並指出缺什麼。"
              "回答結構清楚，可用條列；需要比較時可用表格（Markdown）。" + _lab(c))
    user = "資料：\n\n" + "\n\n---\n\n".join(blocks) + f"\n\n問題：{question}"
    ans = chat(con, system, user[: c["max_chars"] + 4000], 2500)
    used = [r for r in refs if f"[{r['citekey']}]" in ans or r["citekey"] in ans]
    _save(con, user_id, question, ans, used or refs, f"paper:{pid}" if pid else (f"cat:{cat}" if cat else ""))
    return {"answer": ans, "refs": used or refs, "keywords": kws}


def _save(con, uid, q, a, refs, scope):
    con.execute("INSERT INTO ai_log(user_id, kind, question, answer, refs, scope, created_at) VALUES(?,?,?,?,?,?,?)",
                (uid, "ask", q, a, json.dumps(refs, ensure_ascii=False), scope, db.now()))


# ------------------------------------------------------------------ 整個論文庫：批次預填、分類綜述
PER_PAPER_USD = 0.025      # Claude Haiku 4.5 讀一篇全文＋寫重點欄的約略花費


def _batch_targets(con, cat: int | None, mode: str) -> list[int]:
    q = "SELECT p.id, p.keyinfo FROM papers p"
    a = []
    if cat:
        q += " JOIN paper_categories pc ON pc.paper_id=p.id AND pc.category_id=?"
        a.append(cat)
    out = []
    for r in con.execute(q + " ORDER BY p.id", a):
        ki = {k: v for k, v in json.loads(r["keyinfo"] or "{}").items() if str(v).strip()}
        missing = [f for f in KEYINFO_FIELDS if not ki.get(f)]
        if mode == "all" or (mode == "missing" and missing) or (mode == "empty" and not ki):
            out.append(r["id"])
    return out


def estimate(con, cat: int | None = None, mode: str = "missing") -> dict:
    ids = _batch_targets(con, cat, mode)
    s = settings.get(con)
    prov = s["tr_provider"] if s["ai_provider"] == "same" else s["ai_provider"]
    return {"papers": len(ids), "usd": round(len(ids) * PER_PAPER_USD, 2) if prov == "anthropic" else None,
            "minutes": round(len(ids) * (0.4 if prov in ("anthropic", "openai") and "11434" not in (s["ai_url"] + s["tr_url"]) else 1.5)) or 1}


def batch_prefill(con, cat: int | None, mode: str, progress) -> str:
    """逐篇讀全文、填重點欄。預設只補空白欄位，不覆蓋人寫的內容；AI 填的欄位會標記，之後人改過就取消標記。"""
    ids = _batch_targets(con, cat, mode)
    done = skipped = failed = 0
    errs = []
    for i, pid in enumerate(ids):
        progress(f"{i + 1}/{len(ids)} 篇（完成 {done}、略過 {skipped}、失敗 {failed}）")
        try:
            sug = suggest_keyinfo(con, pid)
        except RuntimeError as e:
            if "沒有可讀的全文" in str(e):
                skipped += 1
                continue
            if "還沒設定" in str(e) or "無法沿用" in str(e):
                raise
            failed += 1; errs.append(f"#{pid}：{e}")
            if failed >= 5 and done == 0:
                raise RuntimeError("連續失敗，已停止：" + "；".join(errs[-3:]))
            continue
        except (ValueError, KeyError, IndexError) as e:
            failed += 1; errs.append(f"#{pid}：{e}")
            continue
        with db.tx() as c:
            p = c.execute("SELECT keyinfo, ai_keys FROM papers WHERE id=?", (pid,)).fetchone()
            ki = json.loads(p["keyinfo"] or "{}")
            ai_keys = set(json.loads(p["ai_keys"] or "[]"))
            for k, v in sug.items():
                if mode == "all" and k in ai_keys or not str(ki.get(k, "")).strip():
                    ki[k] = v
                    ai_keys.add(k)
            c.execute("UPDATE papers SET keyinfo=?, ai_keys=?, updated_at=? WHERE id=?",
                      (json.dumps(ki, ensure_ascii=False), json.dumps(sorted(ai_keys), ensure_ascii=False), db.now(), pid))
            db.fts_update(c, pid)
        done += 1
    msg = f"處理 {len(ids)} 篇：填好 {done}、沒有全文略過 {skipped}、失敗 {failed}"
    return msg + (f"（{'；'.join(errs[:3])}）" if errs else "")


def _paper_digest(p, ki: dict) -> str:
    return (f"[{p['citekey']}] {p['title']}（{p['year'] or '?'}，{p['venue'] or ''}）\n"
            + "\n".join(f"  {k}：{v}" for k, v in ki.items() if v and k in KEYINFO_FIELDS + ['備註']))


REVIEW_SYSTEM = ("你是凝態物理實驗室的研究助理，負責把實驗室論文庫整理成給組員讀的綜述。只根據提供的資料寫，"
                 "用繁體中文（台灣用語），專有名詞保留英文，每個論點後面用 [citekey] 標出處（只能用資料裡的 citekey）。"
                 "資料不足的地方要直說。用 Markdown：## 小標、條列、需要比較時用表格。")


def review_category(con, cat_id: int | None, user_id=None) -> int:
    c = config(con)
    if cat_id:
        cat = con.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
        rows = con.execute("SELECT p.* FROM papers p JOIN paper_categories pc ON pc.paper_id=p.id WHERE pc.category_id=? "
                           "ORDER BY p.year, p.id", (cat_id,)).fetchall()
        name = cat["name"]
    else:
        rows = con.execute("SELECT p.* FROM papers p WHERE NOT EXISTS(SELECT 1 FROM paper_categories pc WHERE pc.paper_id=p.id) "
                           "ORDER BY p.year, p.id").fetchall()
        name = "未歸檔待讀"
    blocks, refs = [], []
    for p in rows:
        ki = json.loads(p["keyinfo"] or "{}")
        if not ki and p["abstract"]:
            ki = {"摘要": p["abstract"][:700]}
        blocks.append(_paper_digest(p, ki))
        refs.append({"id": p["id"], "citekey": p["citekey"], "title": p["title"]})
    if not blocks:
        raise RuntimeError(f"「{name}」沒有論文")
    text = "\n\n".join(blocks)[: c["max_chars"]]
    user = (f"分類：{name}（{cat['description'] if cat_id else '尚未分類的論文'}）\n共 {len(blocks)} 篇。以下是每篇的重點欄：\n\n{text}\n\n"
            "請寫這個分類的綜述，包含：\n## 一句話總結\n## 研究脈絡（依時間，誰先做了什麼、後來怎麼發展）\n"
            "## 架設與關鍵參數比較（表格：論文｜平台／架設｜關鍵參數｜觀測量）\n## 主要結果與共識\n"
            "## 還沒解決的問題、各篇之間的矛盾\n## 和我們實驗的關係與可以做的方向\n## 新成員建議閱讀順序（3–6 篇，附一句理由）")
    content = chat(con, REVIEW_SYSTEM + _lab(c), user, 4000)
    cur = con.execute("INSERT INTO ai_reports(scope, title, content, refs, n_papers, model, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (f"cat:{cat_id or 0}", f"{name} 綜述", content, json.dumps(refs, ensure_ascii=False), len(blocks),
                       c["model"], user_id, db.now()))
    return cur.lastrowid


def review_all(con, user_id=None, progress=lambda m: None) -> int:
    """整個論文庫總覽：用各分類最新的綜述（沒有就先產生）再彙整一次。"""
    c = config(con)
    cats = con.execute("SELECT id, name FROM categories ORDER BY sort, id").fetchall()
    parts, refs = [], []
    for i, cat in enumerate(cats):
        r = con.execute("SELECT content, refs FROM ai_reports WHERE scope=? ORDER BY id DESC LIMIT 1", (f"cat:{cat['id']}",)).fetchone()
        if r is None:
            if not con.execute("SELECT 1 FROM paper_categories WHERE category_id=?", (cat["id"],)).fetchone():
                continue
            progress(f"先產生「{cat['name']}」綜述（{i + 1}/{len(cats)}）")
            review_category(con, cat["id"], user_id)
            r = con.execute("SELECT content, refs FROM ai_reports WHERE scope=? ORDER BY id DESC LIMIT 1", (f"cat:{cat['id']}",)).fetchone()
        parts.append(f"# 分類：{cat['name']}\n{r['content']}")
        refs += json.loads(r["refs"])
    if not parts:
        raise RuntimeError("還沒有任何分類的論文")
    progress("彙整整個論文庫")
    per = max(3000, c["max_chars"] // len(parts))
    text = "\n\n".join(p[:per] for p in parts)
    user = (f"以下是實驗室論文庫各分類的綜述（共 {len(parts)} 類）：\n\n{text}\n\n請寫整個論文庫的總覽：\n"
            "## 實驗室的研究版圖（各分類在做什麼、彼此怎麼連結）\n## 跨分類的共同主題與關鍵物理（例如耗散耦合、EP、非互易、巨原子）\n"
            "## 平台與參數總表（依分類列出代表性架設與數量級）\n## 最重要的 10 篇論文（附理由）\n"
            "## 研究缺口與建議的下一步\n## 給新成員的入門路線")
    content = chat(con, REVIEW_SYSTEM + _lab(c), user, 5000)
    seen, uniq = set(), []
    for r in refs:
        if r["id"] not in seen:
            seen.add(r["id"]); uniq.append(r)
    cur = con.execute("INSERT INTO ai_reports(scope, title, content, refs, n_papers, model, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                      ("all", "整個論文庫總覽", content, json.dumps(uniq, ensure_ascii=False), len(uniq), c["model"], user_id, db.now()))
    return cur.lastrowid


@handler("ai_batch")
def _batch_job(con, arg, progress):
    a = json.loads(arg or "{}")
    return batch_prefill(con, a.get("cat"), a.get("mode", "missing"), progress)


@handler("ai_review")
def _review_job(con, arg, progress):
    a = json.loads(arg or "{}")
    scope = a.get("scope", "all")
    if scope == "all":
        review_all(con, a.get("user"), progress)
        return "已產生整個論文庫總覽"
    if scope == "cats":     # 重新產生所有分類的綜述，再做總覽
        cats = [r["id"] for r in con.execute("SELECT DISTINCT category_id FROM paper_categories")]
        for i, cid in enumerate(cats):
            progress(f"分類綜述 {i + 1}/{len(cats)}")
            review_category(con, cid, a.get("user"))
        review_all(con, a.get("user"), progress)
        return f"已產生 {len(cats)} 個分類綜述與總覽"
    review_category(con, int(scope) or None, a.get("user"))
    return "已產生分類綜述"
