"""1.0.42：論文庫匯出給 GPT（CCU Magnonics 規範 1.1）。"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
import urllib.request
import zipfile

import pytest

from labhub import gptexport as gx

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_helpers():
    assert gx.norm_doi(" https://doi.org/10.1103/PhysRevA.76.042319 ") == "10.1103/physreva.76.042319"
    assert gx.norm_doi("doi:10.1/ABC") == "10.1/abc"
    assert gx.norm_arxiv("arXiv:2101.00001v2") == "2101.00001" and gx.norm_arxiv("https://arxiv.org/abs/2101.00001") == "2101.00001"
    assert gx.short_title("Non-generic CPA exceptional points in a waveguide!") == "non-generic-cpa-exceptional-points-waveguide"
    assert gx.short_title("磁振子") == "paper"
    assert gx.first_author(["A. One", "B. Two"]) == "A. One" and gx.first_author('["X"]') == "X"
    c = gx.auto_class({"tags": ["vna s21", "CPAEP", "mine"], "categories": [1]}, {1: "cavity-magnonics"})
    assert c["tags"] == ["CPAEP", "VNA-S21"] and c["other_tags"] == ["mine"] and c["topic"] == "Cavity Magnonics"
    with pytest.raises(Exception):
        gx.clean_class({"topic": "EP"})
    with pytest.raises(Exception):
        gx.clean_class({"tags": ["Magnonics"]})
    assert len(gx.PAPERS_COLS) == 24 and len(gx.TOPIC_VALUES) == 8


def _read_csv(z, name):
    raw = z.read(f"{gx.ROOT}/metadata/{name}")
    assert raw.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))), raw.decode("utf-8-sig").splitlines()[0]


def test_export_end_to_end(tmp_path):
    from labhub.server import Config, make_server
    from tests import fake_paperlib
    from tests.test_122 import _call

    psrv, pst, purl = fake_paperlib.start()
    srv, state = make_server(Config(tmp_path / "hub", token="dev", paperlib_url=purl), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        tok = _call(url + "/api/platform/login", "POST", {"username": "alice", "password": "alicepw"})[1]["token"]
        ck = f"labhub_token={tok}"
        code, d = _call(url + "/api/gptexport/papers", cookie=ck)
        assert code == 200 and {p["id"] for p in d["papers"]} == {11, 12, 13}
        p12 = next(p for p in d["papers"] if p["id"] == 12)
        assert p12["auto"]["topic"] == "Cavity Magnonics" and p12["mag"] == ""
        assert _call(url + "/api/gptexport/class/11", "PUT", {"topic": "EP"}, cookie=ck)[0] == 400
        code, r = _call(url + "/api/gptexport/class/11", "PUT",
                        {"topic": "Fundamentals", "tags": ["YIG", "Kittel mode"], "type": "Theory"}, cookie=ck)
        assert code == 200 and r["saved"]["by"] == "alice" and r["saved"]["tags"] == ["YIG", "Kittel mode"]

        def export(body):
            code, r = _call(url + "/api/gptexport/export", "POST", body, cookie=ck)
            assert code == 200, r
            for _ in range(100):
                j = _call(url + "/api/gptexport/status", cookie=ck)[1]["job"]
                if not j.get("running"):
                    break
                time.sleep(0.1)
            assert not j.get("error"), j
            name = j["result"]["name"]
            req = urllib.request.Request(url + f"/api/gptexport/exports/{name}", headers={"Cookie": ck})
            with OP.open(req, timeout=10) as resp:
                return zipfile.ZipFile(io.BytesIO(resp.read())), j["result"]

        z, res = export({"scope": "all", "pdf": "all"})
        names = z.namelist()
        assert f"{gx.ROOT}/README.md" in names and "README 版本：1.1" in z.read(f"{gx.ROOT}/README.md").decode()
        rows, head = _read_csv(z, "papers.csv")
        assert head.split(",")[:3] == ["Paper ID", "優先 A-D", "閱讀狀態"] and len(head.split(",")) == 24
        assert len(rows) == 2                                            # 11 與 13 的 DOI 相同 → 一篇
        by = {r["Paper ID"]: r for r in rows}
        a, b = by["MAG-0001"], by["MAG-0002"]
        assert a["論文標題 Title"].startswith("Charge-insensitive") and a["優先 A-D"] == "C" and a["閱讀狀態"] == "未讀"
        assert a["主要主題"] == "Fundamentals" and a["次級標籤"] == "YIG; Kittel mode" and a["論文類型"] == "Theory"
        assert a["DOI"] == "10.1103/physreva.76.042319" and a["PDF 檔名"].startswith("papers/MAG-0001__charge-insensitive")
        assert f"{gx.ROOT}/{a['PDF 檔名']}" in names and z.read(f"{gx.ROOT}/{a['PDF 檔名']}") == b"%PDF-1.4 koch"
        assert a["研究問題"].startswith("[AI interpretation]") and a["物理系統"].startswith("[論文庫人工填寫]")
        assert "AI 評級" in a["與本實驗室關聯"] and "已併為一筆" in a["重要限制 / 待確認"]
        assert b["主要主題"] == "Cavity Magnonics" and b["arXiv / URL"] == "https://arxiv.org/abs/2101.00001"
        assert b["PDF 檔名"] == "" and b["優先 A-D"] == ""
        ev, _ = _read_csv(z, "evidence.csv")
        assert [e["證據 ID"] for e in ev] == ["EVD-000001"] and ev[0]["原文頁/式/圖"] == "PDF p. 3"
        assert ev[0]["結論／公式"] == "E_J/E_C ≫ 1" and "Verified from PDF" in ev[0]["目前判斷"]
        notes, _ = _read_csv(z, "notes.csv")
        assert notes[0]["Paper ID"] == "MAG-0001" and notes[0]["人工核對狀態"] == "部分核對"
        warns, _ = _read_csv(z, "classification_warnings.csv")
        codes = {(w["Paper ID"], w["warning_code"]) for w in warns}
        assert ("MAG-0001", "POSSIBLE_DUPLICATE") in codes and ("MAG-0002", "PRIORITY_PENDING") in codes
        assert ("MAG-0002", "PDF_MISSING") in codes and ("MAG-0002", "DOI_MISSING") in codes
        m = json.loads(z.read(f"{gx.ROOT}/metadata/manifest.json"))
        assert m["schema_version"] == "1.1" and m["paper_count"] == 2 and m["pdf_count"] == 1
        assert m["validation"]["ok"], m["validation"]
        pa = next(x for x in m["papers"] if x["paper_id"] == "MAG-0001")
        import hashlib

        assert pa["sha256"] == hashlib.sha256(b"%PDF-1.4 koch").hexdigest() and pa["paperlib_ids"] == [11, 13]
        assert pa["content_access"] == "full_text" and pa["export_permission"] == "authorized"
        assert "## 摘要" in z.read(f"{gx.ROOT}/notes/MAG-0001.md").decode() and "私人" not in z.read(
            f"{gx.ROOT}/notes/MAG-0001.md").decode()
        assert res["summary"]["papers"] == 2 and res["summary"]["topics"]["Fundamentals"] == 1
        # 再匯出：ID 不變；只匯出分類 1、不附 PDF
        z2, _ = export({"scope": "cat:1", "pdf": "none"})
        rows2, _ = _read_csv(z2, "papers.csv")
        assert [r["Paper ID"] for r in rows2] == ["MAG-0002"] and not any(n.endswith(".pdf") for n in z2.namelist())
        z3, _ = export({"scope": "all", "pdf": "grades", "grades": ["A"]})
        assert not any(n.endswith(".pdf") for n in z3.namelist())                     # 合併後那篇是 C
        assert len(_call(url + "/api/gptexport/exports", cookie=ck)[1]["exports"]) == 3
        # 別人看不到 alice 的匯出（站長可以）
        tok2 = _call(url + "/api/platform/login", "POST", {"username": "admin", "password": "adminpw"})[1]["token"]
        assert len(_call(url + "/api/gptexport/exports", cookie=f"labhub_token={tok2}")[1]["exports"]) == 3
        assert _call(url + "/api/gptexport/exports", headers={"Authorization": "Bearer dev"})[0] in (401, 403)
        html = OP.open(urllib.request.Request(url + "/gptexport", headers={"Cookie": ck}), timeout=10).read().decode()
        assert "論文匯出" in html
    finally:
        srv.shutdown()
        psrv.shutdown()
