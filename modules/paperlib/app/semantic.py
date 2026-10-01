"""語意搜尋與相似論文。

兩種引擎：
1. 內建（不需任何設定）：TF-IDF 向量＋餘弦相似度。英文用單字與相鄰兩字片語，中文用字元雙連（bigram），
   所以中文描述也能對到中文的重點欄與筆記；有設定 AI 時，中文問題會先轉成英文關鍵字再搜。
2. 向量模型（選用）：OpenAI 相容的 /embeddings（OpenAI、Voyage、Jina、自架 Ollama 都可），
   中英文混搜、換句話說都找得到。在「管理 → 網站與 AI」設定後，背景工作會替每篇論文建立向量。
"""
import array
import hashlib
import json
import math
import re
import threading
from collections import Counter

from . import ai, db, settings
from .jobs import handler
from .translate import _post

STOP = set("""a an the and or of to in on for with by from at as is are was were be been being this that these those it its we our
they their them which who whom whose what when where how than then there here such can could may might will would shall should
do does did done not no nor but if into onto over under between among within without about above below after before also only
both each few more most other some any all very via using used use show shows shown study paper result results here present
propose proposed based new two one three first second et al fig figure table eq ref refs however thus therefore while
et i ii iii iv""".split())
_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")
_CJK = re.compile(r"[㐀-鿿]+")
_lock = threading.Lock()
_cache = {"docs": {}, "idf": {}, "vecs": {}, "stamp": None}


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def tokens(text: str) -> list[str]:
    t = (text or "").lower()
    out = []
    words = [_stem(w.strip("-")) for w in _WORD.findall(t)]
    words = [w for w in words if w and w not in STOP]
    out += words
    out += [f"{a} {b}" for a, b in zip(words, words[1:])]       # 片語：level attraction、exceptional point
    for run in _CJK.findall(t):
        out += [run[i:i + 2] for i in range(len(run) - 1)] or [run]
    return out


def head_text(con, pid: int) -> str:
    p = con.execute("SELECT title, abstract, keyinfo FROM papers WHERE id=?", (pid,)).fetchone()
    if p is None:
        return ""
    try:
        ki = " ".join(str(v) for v in json.loads(p["keyinfo"] or "{}").values())
    except ValueError:
        ki = ""
    return "\n".join([(p["title"] + " ") * 2, p["abstract"] or "", ki])


def doc_text(con, pid: int, body_chars: int = 12000) -> str:
    p = con.execute("SELECT title, abstract, keyinfo FROM papers WHERE id=?", (pid,)).fetchone()
    if p is None:
        return ""
    body = (con.execute("SELECT body FROM fts WHERE paper_id=?", (pid,)).fetchone() or {"body": ""})["body"] or ""
    from .refs import REF_HEAD
    heads = list(REF_HEAD.finditer(body))
    if heads and heads[-1].start() > len(body) * 0.5:
        body = body[:heads[-1].start()]
    try:
        ki = " ".join(str(v) for v in json.loads(p["keyinfo"] or "{}").values())
    except ValueError:
        ki = ""
    notes = " ".join(r["body"] for r in con.execute("SELECT body FROM annotations WHERE paper_id=? AND private=0 AND body!=''", (pid,)))
    return "\n".join([(p["title"] + " ") * 3, (p["abstract"] or "") * 2, ki, notes, body[:body_chars]])


def _sigs(con) -> dict:
    return {r["id"]: f"{r['updated_at']}|{r['n']}|{r['a']}" for r in con.execute(
        "SELECT p.id, p.updated_at, (SELECT length(body) FROM fts WHERE paper_id=p.id) n, "
        "(SELECT COUNT(*) || ':' || COALESCE(MAX(updated_at),'') FROM annotations WHERE paper_id=p.id AND private=0) a FROM papers p")}


def _index(con):
    """回傳 (vecs, idf)。只重算有變動的論文。"""
    with _lock:
        sigs = _sigs(con)
        docs = _cache["docs"]
        changed = False
        for pid in list(docs):
            if pid not in sigs:
                del docs[pid]; changed = True
        for pid, sig in sigs.items():
            if docs.get(pid, (None,))[0] != sig:
                docs[pid] = (sig, Counter(tokens(doc_text(con, pid))), Counter(tokens(head_text(con, pid))))
                changed = True
        if changed or _cache["stamp"] is None:
            n = max(1, len(docs))
            df = Counter()
            for _, tf, _h in docs.values():
                df.update(tf.keys())
            idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items() if c < n * 0.6 or n < 5}

            def vec(tf, top_n):
                v = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items() if t in idf}
                top = dict(sorted(v.items(), key=lambda x: -x[1])[:top_n])
                norm = math.sqrt(sum(x * x for x in top.values())) or 1.0
                return {t: x / norm for t, x in top.items()}
            vecs = {pid: vec(tf, 400) for pid, (_, tf, _h) in docs.items()}
            heads = {pid: vec(hd, 150) for pid, (_, _t, hd) in docs.items()}
            _cache.update(idf=idf, vecs=vecs, heads=heads, stamp=len(docs))
        return _cache["vecs"], _cache["idf"]


def _score(qv, pid) -> float:
    """標題＋摘要＋重點欄的相似度佔 6 成，全文佔 4 成（避免短文件、雜訊搶走排名）。"""
    return 0.6 * _cos(qv, _cache["heads"].get(pid, {})) + 0.4 * _cos(qv, _cache["vecs"].get(pid, {}))


def _qvec(q_tokens, idf) -> dict:
    tf = Counter(t for t in q_tokens if t in idf)
    v = {t: (1 + math.log(c)) * idf[t] for t, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def _cos(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _shared(a: dict, b: dict, n=5) -> list[str]:
    common = sorted(((a[t] * b[t], t) for t in a.keys() & b.keys()), reverse=True)
    out = []
    for _, t in common:
        if any(t in o or o in t for o in out):
            continue
        out.append(t)
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ 向量模型（選用）
def emb_config(con) -> dict | None:
    s = settings.get(con)
    if s["emb_provider"] != "openai":
        return None
    return {"url": (s["emb_url"] or "https://api.openai.com/v1").rstrip("/"), "key": s["emb_key"],
            "model": s["emb_model"] or "text-embedding-3-small"}


def embed(con, texts: list[str]) -> list[list[float]]:
    c = emb_config(con)
    if not c:
        raise RuntimeError("沒有設定向量模型")
    h = {"Authorization": f"Bearer {c['key']}"} if c["key"] else {}
    r = _post(c["url"] + "/embeddings", {"model": c["model"], "input": texts}, h, timeout=120)
    data = sorted(r["data"], key=lambda d: d.get("index", 0))
    out = []
    for d in data:
        v = d["embedding"]
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _emb_text(con, pid: int) -> str:
    return doc_text(con, pid, body_chars=4000)[:7000]


def _emb_sig(con, pid: int, model: str) -> str:
    return hashlib.sha1((model + _emb_text(con, pid)).encode()).hexdigest()


def emb_status(con) -> dict:
    c = emb_config(con)
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    done = con.execute("SELECT COUNT(*) FROM embeddings WHERE model=?", (c["model"] if c else "",)).fetchone()[0] if c else 0
    return {"enabled": bool(c), "model": c["model"] if c else "", "done": done, "total": total}


def _emb_all(con, model: str) -> dict:
    out = {}
    for r in con.execute("SELECT paper_id, vec FROM embeddings WHERE model=?", (model,)):
        a = array.array("f")
        a.frombytes(r["vec"])
        out[r["paper_id"]] = a
    return out


@handler("embed")
def _embed_job(con, arg, progress):
    c = emb_config(con)
    if not c:
        return "沒有設定向量模型，略過"
    have = {r["paper_id"]: (r["model"], r["sig"]) for r in con.execute("SELECT paper_id, model, sig FROM embeddings")}
    todo = []
    for r in con.execute("SELECT id FROM papers ORDER BY id").fetchall():
        sig = _emb_sig(con, r["id"], c["model"])
        if have.get(r["id"]) != (c["model"], sig):
            todo.append((r["id"], sig))
    done = 0
    for i in range(0, len(todo), 16):
        chunk = todo[i:i + 16]
        progress(f"{i}/{len(todo)} 篇")
        vecs = embed(con, [_emb_text(con, pid) for pid, _ in chunk])
        with db.tx() as c2:
            for (pid, sig), v in zip(chunk, vecs):
                c2.execute("INSERT INTO embeddings(paper_id, model, dim, vec, sig, updated_at) VALUES(?,?,?,?,?,?) "
                           "ON CONFLICT(paper_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, vec=excluded.vec, "
                           "sig=excluded.sig, updated_at=excluded.updated_at",
                           (pid, c["model"], len(v), array.array("f", v).tobytes(), sig, db.now()))
        done += len(chunk)
    return f"建立或更新 {done} 篇論文的向量" if done else "所有論文的向量都是最新的"


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


# ------------------------------------------------------------------ 對外
def search(con, q: str, limit: int = 30, cat: int | None = None) -> dict:
    q = (q or "").strip()
    if not q:
        return {"items": [], "mode": "", "keywords": []}
    ec = emb_config(con)
    scores, mode, kws = {}, "tfidf", []
    if ec and con.execute("SELECT 1 FROM embeddings WHERE model=? LIMIT 1", (ec["model"],)).fetchone():
        try:
            qv = embed(con, [q])[0]
            for pid, v in _emb_all(con, ec["model"]).items():
                scores[pid] = _dot(qv, v)
            mode = "embedding"
        except Exception:  # noqa: BLE001 - 向量服務失敗時退回內建
            scores = {}
    vecs, idf = _index(con)
    qt = tokens(q)
    if mode == "tfidf" and _CJK.search(q) and ai.enabled(con):
        try:
            kws = ai._keywords(con, q)
            qt += tokens(" ".join(kws)) * 2
        except Exception:  # noqa: BLE001
            pass
    qv2 = _qvec(qt, idf)
    if mode == "tfidf":
        scores = {pid: _score(qv2, pid) for pid in vecs}
    if cat:
        inc = {r["paper_id"] for r in con.execute("SELECT paper_id FROM paper_categories WHERE category_id=?", (cat,))}
        scores = {k: v for k, v in scores.items() if k in inc}
    ranked = [(pid, s) for pid, s in sorted(scores.items(), key=lambda x: -x[1]) if s > (0.2 if mode == "embedding" else 0.02)][:limit]
    items = [{"id": pid, "score": round(s, 3), "why": _shared(qv2, vecs.get(pid, {}), 4)} for pid, s in ranked]
    return {"items": items, "mode": mode, "keywords": kws}


def similar(con, pid: int, limit: int = 6) -> list[dict]:
    vecs, _ = _index(con)
    me = vecs.get(pid)
    if not me:
        return []
    ec = emb_config(con)
    scores, mode = {}, "tfidf"
    if ec:
        allv = _emb_all(con, ec["model"])
        if pid in allv:
            mine = allv[pid]
            scores = {o: _dot(mine, v) for o, v in allv.items() if o != pid}
            mode = "embedding"
    if not scores:
        mh = _cache["heads"].get(pid, {})
        scores = {o: 0.5 * _cos(me, v) + 0.5 * _cos(mh, _cache["heads"].get(o, {})) for o, v in vecs.items() if o != pid}
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:limit]
    return [{"id": o, "score": round(s, 3), "why": _shared(me, vecs.get(o, {}), 4), "mode": mode} for o, s in ranked if s > 0.03]


def warm(con) -> None:
    _index(con)


@handler("similar_index")
def _warm_job(con, arg, progress):
    vecs, _ = _index(con)
    return f"已建立 {len(vecs)} 篇論文的相似度索引"
