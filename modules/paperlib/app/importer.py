"""批次匯入：依 seed/manifest.json 把現有論文資料夾匯入論文庫。

用法（在 NAS 的容器內或本機）：
  python -m app.importer --manifest seed/manifest.json --source /import/Paper --source /import/paper [--enrich]

* 檔案以 SHA-256 對應 manifest，資料夾怎麼放、檔名是什麼都沒關係。
* 可重複執行：已存在的論文與檔案會跳過，只補上缺的。
* 原始資料夾只讀不改，檔案是複製進論文庫。
"""
import argparse
import json
import sys
import time
from pathlib import Path

from . import db, enrich, library


def scan(sources: list[Path]) -> dict[str, Path]:
    found = {}
    for root in sources:
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() == ".pdf":
                found.setdefault(library.sha256_file(p), p)
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="依 manifest 匯入論文")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--source", action="append", required=True, type=Path, help="舊論文資料夾，可給多個")
    ap.add_argument("--enrich", action="store_true", help="匯入後用 Crossref/arXiv 補齊作者與摘要（需連外網）")
    args = ap.parse_args(argv)

    db.init()
    man = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(f"掃描 {', '.join(map(str, args.source))} …", flush=True)
    found = scan(args.source)
    print(f"找到 {len(found)} 個 PDF", flush=True)

    con = db.get()
    catid = {}
    with db.tx():
        for c in man["categories"]:
            r = con.execute("SELECT id FROM categories WHERE key=? OR name=?", (c["key"], c["name"])).fetchone()
            if r is None:
                cur = con.execute("INSERT INTO categories(key, name, grp, description, color, sort) VALUES(?,?,?,?,?,?)",
                                  (c["key"], c["name"], c["group"], c["description"], c["color"], c["sort"]))
                catid[c["key"]] = cur.lastrowid
            else:
                catid[c["key"]] = r["id"]

    created = files_added = skipped = 0
    missing = []
    for p in man["papers"]:
        with db.tx():
            r = con.execute("SELECT id FROM papers WHERE citekey=? OR (doi!='' AND lower(doi)=lower(?))",
                            (p["citekey"], p["doi"] or "\x00")).fetchone()
            if r:
                pid = r["id"]
                skipped += 1
            else:
                ki = dict(p["keyinfo"])
                pid = library.create_paper(con, {
                    "citekey": p["citekey"], "title": p["title"], "authors": p["authors"],
                    "authors_complete": p["authors_complete"], "year": p["year"], "venue": p["venue"], "doi": p["doi"],
                    "arxiv": p["arxiv"], "kind": p["kind"], "status": p["status"], "keyinfo": ki,
                    "suggested_category_id": catid.get(p["suggested"]) if p.get("suggested") else None})
                db.set_categories(con, pid, [catid[k] for k in p["categories"]])
                db.set_tags(con, pid, p["tags"])
                created += 1
            for f in p["files"]:
                if con.execute("SELECT 1 FROM files WHERE sha256=? OR orig_sha256=?", (f["sha256"], f["sha256"])).fetchone():
                    continue
                src = found.get(f["sha256"])
                if src is None:
                    missing.append(f"{p['citekey']}：{', '.join(f['original_names'])}")
                    continue
                library.add_file(con, pid, src, src.name, f["role"], f.get("label", ""), sha=f["sha256"])
                files_added += 1
            db.fts_update(con, pid)
            db.log(con, None, "import", pid, p["citekey"])
        print(f"  {p['citekey']}", flush=True)

    print(f"\n新增論文 {created}、已存在 {skipped}、複製檔案 {files_added}、找不到檔案 {len(missing)}")
    for m in missing:
        print("  找不到：", m)

    if args.enrich:
        print("\n用 Crossref/arXiv 補齊書目 …")
        ok = fail = 0
        for r in con.execute("SELECT id, citekey, doi, arxiv FROM papers WHERE authors_complete=0 AND (doi!='' OR arxiv!='')").fetchall():
            try:
                info = enrich.lookup(r["doi"], r["arxiv"])
            except RuntimeError as e:
                fail += 1
                print(f"  ✗ {r['citekey']}：{e}")
                continue
            with db.tx():
                con.execute("UPDATE papers SET authors=?, authors_complete=1, abstract=CASE WHEN abstract='' THEN ? ELSE abstract END, "
                            "venue=CASE WHEN venue='' THEN ? ELSE venue END, updated_at=? WHERE id=?",
                            (json.dumps(info["authors"], ensure_ascii=False), info.get("abstract", ""), info.get("venue", ""),
                             db.now(), r["id"]))
                db.fts_update(con, r["id"])
            ok += 1
            time.sleep(0.2)
        print(f"補齊 {ok} 篇，失敗 {fail} 篇")
    # 交給網站的背景工作：分析引用、檢查掃描檔（網站執行中會在半分鐘內開始）
    from . import jobs
    jobs.enqueue("refs", "")
    jobs.enqueue("scan_text", "")
    print("已排入背景工作：分析參考文獻、檢查掃描檔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
