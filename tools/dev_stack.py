"""在一台電腦上同時跑論文庫、量測中繼站（Hub）、大程式網站，開發與測試用。

    python tools/dev_stack.py                 # 資料放在 ./dev-data
    python tools/dev_stack.py --demo          # 第一次執行時建立示範帳號與數據

網址：大程式 http://127.0.0.1:8090/、論文庫 http://127.0.0.1:8080/、Hub http://127.0.0.1:8765/
示範帳號（--demo）：站長 boss / bosspass1、成員 amy / amypass12
需要：論文庫的套件（modules/paperlib/requirements.txt）。Hub 與大程式只用標準函式庫。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB_TOKEN = "dev-hub-token"


def wait(url: str, timeout: float = 60) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            opener.open(url, timeout=2).read()
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    raise SystemExit(f"{url} 沒有啟動")


def call(base: str, method: str, path: str, body=None, cookie: str = "", headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    h = {"Content-Type": "application/json", "X-PL": "1", "X-QEL": "1", **(headers or {})}
    if cookie:
        h["Cookie"] = cookie
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=h)
    r = opener.open(req, timeout=30)
    ck = "; ".join(c.split(";", 1)[0] for c in (r.headers.get_all("Set-Cookie") or []))
    return json.loads(r.read().decode() or "null"), ck


def demo(data: Path, pl: str, portal: str) -> None:
    if (data / ".demo-done").exists():
        return
    _, boss = call(pl, "POST", "/api/auth/setup", {"username": "boss", "password": "bosspass1", "display_name": "老闆"})
    call(pl, "PATCH", "/api/users/1", {"owner": True}, boss)
    call(pl, "POST", "/api/users", {"username": "amy", "password": "amypass12", "display_name": "Amy", "role": "member"}, boss)
    con = sqlite3.connect(str(data / "paperlib" / "library.db"))
    now = "2026-10-01T00:00:00+00:00"
    papers = [(1, "Bound states in the continuum in cavity magnonics", 2024, "BIC"),
              (2, "Cavity magnonics: a review", 2022, None),
              (3, "Mirror-symmetric YIG coupling on planar resonators", 2025, "Mirror"),
              (4, "Level attraction in open magnon-photon systems", 2023, "LA")]
    for pid, title, year, tag in papers:
        con.execute("INSERT INTO papers(id, citekey, title, authors, year, venue, added_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (pid, f"demo{pid}", title, json.dumps(["Wang, A.", "Chen, B."]), year, "Phys. Rev. B", now, now))
        if tag:
            con.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tag,))
            tid = con.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()[0]
            con.execute("INSERT INTO paper_tags(paper_id, tag_id) VALUES(?,?)", (pid, tid))
    con.commit()
    con.close()
    call(pl, "POST", "/api/admin/reindex", {}, boss)
    r, _ = call(portal, "POST", "/api/v1/auth/login", {"username": "boss", "password": "bosspass1", "client": "dev"})
    auth = {"Authorization": f"Bearer {r['token']}"}
    scheme = {"scheme": 2, "name": "BIC 2D 掃描", "graph": {"nodes": [
        {"kind": "set", "id": "a", "target": "magnet_A", "mode": "sweep", "start": 100, "stop": 150, "step": 0.5, "unit": "mA"},
        {"kind": "set", "id": "b", "target": "VNA1.power", "mode": "fixed", "value": -10, "unit": "dBm"},
        {"kind": "measure", "id": "c", "instrument": "VNA1", "traces": ["S21"]}], "links": []}}
    for i, (name, tags) in enumerate([("BIC_2D_1001.hdf5", ["BIC", "LA", "Best Data"]),
                                      ("Mirror_flux_1002.hdf5", ["Mirror", "Flux"]),
                                      ("debug_1003.hdf5", ["Debug"])]):
        call(portal, "POST", "/api/v1/datasets", {"name": name, "path": f"//nas/ccuqel/VNA_data/2026/10/{name}", "tags": tags,
                                                  "scheme": scheme if i < 2 else None,
                                                  "source": {"module": "labcontrol", "host": "QEL-PC1", "user": "boss"}},
             headers=auth)
    call(portal, "PUT", "/api/v1/tags/BIC/papers", {"paper_ids": [2]}, headers=auth)
    (data / ".demo-done").write_text("1")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "dev-data"))
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    data = Path(a.data).resolve()
    (data / "paperlib").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080",
                          "--proxy-headers", "--forwarded-allow-ips", "*", "--log-level", "warning"],
                         cwd=str(ROOT / "modules" / "paperlib"), env=dict(env, PAPERLIB_DATA=str(data / "paperlib"))),
        subprocess.Popen([sys.executable, "-m", "labhub", "--port", "8765", "--data", str(data / "labhub"),
                          "--token", HUB_TOKEN], cwd=str(ROOT / "modules" / "labcontrol"), env=env),
        subprocess.Popen([sys.executable, "-m", "qelportal"], cwd=str(ROOT / "portal"),
                         env=dict(env, QEL_PORTAL_DATA=str(data / "portal"), PAPERLIB_URL="http://127.0.0.1:8080",
                                  LABHUB_URL="http://127.0.0.1:8765", LABHUB_TOKEN=HUB_TOKEN,
                                  QEL_AGENT_URL=os.environ.get("QEL_AGENT_URL", ""))),
    ]
    try:
        wait("http://127.0.0.1:8080/api/site")
        wait("http://127.0.0.1:8765/api/ping")
        wait("http://127.0.0.1:8090/api/v1/ping")
        if a.demo:
            demo(data, "http://127.0.0.1:8080", "http://127.0.0.1:8090")
        print("大程式 http://127.0.0.1:8090/   論文庫 http://127.0.0.1:8080/   Hub http://127.0.0.1:8765/（Ctrl+C 結束）",
              flush=True)
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
