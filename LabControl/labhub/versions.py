"""1.0.39：「版本紀錄」頁 /versions —— 各程式每一版改了什麼、什麼時候發佈、什麼時候上到這台 Hub。

    GET  /versions                 頁面（登入的人都看得到）
    GET  /api/versions             {apps: [{id, name, current, note, entries: [{version, date, published, url, title,
                                    text, detail, hub_time, hub_by, current}]}], github: {fetched, busy, error}}
    POST /api/versions/refresh     重新問 GitHub（背景做；60 秒內最多一次）

資料來源（依版本號合併）：
  1. labhub/version_history.json —— 打包 Hub 時從 repo 的修改紀錄產生（tools/version_history.py）；開發時直接讀 repo。
  2. GitHub Releases —— 發佈時間、標題、連結；打包時還沒有的新版用它的說明（拿掉檔案表）。每小時最多問一次，
     存在 data/versions_github.json，連不到 GitHub 就用上次的。
  3. 這台 Hub —— 平台模塊每一版發佈到 Hub 的時間與發佈人；Hub 自己：每一版第一次啟動的時間（data/versions_local.json）。
只用標準函式庫。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .server import Handler, route

BUNDLED = Path(__file__).resolve().parent / "version_history.json"
PREFIX = {"labcontrol": "labcontrol-v", "hub": "hub-v", "netmeter": "netmeter-v", "hfss": "hfss-v",
          "lablogviewer": "lablogviewer-v"}
MODULE = {"labcontrol": "labcontrol", "netmeter": "netmeter", "hfss": "hfss", "lablogviewer": "lablogviewer"}
GITHUB_EVERY_S = 3600.0
REFRESH_MIN_S = 60.0
MAX_PAGES = 5


def vkey(v: str) -> tuple:
    """1.0.10 > 1.0.9；0.19E > 0.19D；1.01 當 1.0.1（LabLogViewer 早期寫法）"""
    v = str(v or "").lstrip("vV")
    m = re.match(r"^(\d+(?:\.\d+)*)([A-Za-z]?)", v)
    if not m:
        return (0,)
    nums = [int(x) for x in m.group(1).split(".")]
    if len(nums) == 2 and len(m.group(1).split(".")[1]) == 2 and m.group(1).split(".")[1].startswith("0"):
        nums = [nums[0], 0, nums[1]]                              # 1.01 → 1.0.1
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums) + (ord(m.group(2).upper()) if m.group(2) else 0,)


def clean_body(body: str, title: str = "") -> str:
    """GitHub 發佈說明 → 只留內容（拿掉檔案表、固定的一句話、重複的標題、發佈日期 / 前一版那幾行）。"""
    out: List[str] = []
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if s.startswith("|") or s.startswith("平台網頁「控制台 → 從 GitHub 取得新版」"):
            continue
        if re.match(r"^#\s", s) or re.match(r"^-\s*(發佈日期|前一版)：", s):
            continue
        if title and s == title.strip():
            continue
        out.append(line.rstrip())
    t = "\n".join(out).strip()
    t = re.sub(r"\A[^\n]*（(Windows|macOS)[^\n]*）\s*\n", "", t).strip()      # 「XXX 1.0.5（Windows .exe、macOS .dmg）」
    t = re.sub(r"\n## 更新步驟[\s\S]*$", "", t).strip()
    t = re.sub(r"^## 摘要[^\n]*\n", "", t, flags=re.M).strip()
    return re.sub(r"\n{3,}", "\n\n", t)


class Versions:
    def __init__(self, hub: Any) -> None:
        self.hub = hub
        d = Path(hub.cfg.data_dir)
        self.gh_path = d / "versions_github.json"
        self.local_path = d / "versions_local.json"
        self.lock = threading.Lock()
        self._bundled: Optional[Dict[str, Any]] = None
        self._gh: Dict[str, Any] = self._load(self.gh_path) or {"releases": [], "fetched": 0}
        self._busy = False
        self._last_try = 0.0
        self.error = ""
        self._mark_hub_started()

    # ---- 檔案 ----
    @staticmethod
    def _load(p: Path) -> Optional[Dict[str, Any]]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write(p: Path, d: Dict[str, Any]) -> None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass

    def _mark_hub_started(self) -> None:
        d = self._load(self.local_path) or {}
        hub = d.setdefault("hub", {})
        if __version__ not in hub:
            hub[__version__] = time.time()
            self._write(self.local_path, d)

    def bundled(self) -> Dict[str, Any]:
        if self._bundled is None:
            data = self._load(BUNDLED)
            if data is None:                                    # 開發中（沒打包）：直接讀 repo
                try:
                    import sys

                    tools = Path(__file__).resolve().parents[1] / "tools"
                    sys.path.insert(0, str(tools))
                    try:
                        import version_history  # type: ignore

                        data = version_history.build()
                    finally:
                        sys.path.remove(str(tools))
                except Exception:  # noqa: BLE001
                    data = None
            self._bundled = data or {"apps": {}}
        return self._bundled

    # ---- GitHub ----
    def _github(self):
        con = getattr(self.hub, "console", None)
        return getattr(con, "github", None) if con is not None else None

    def refresh(self, force: bool = False, wait: bool = False) -> bool:
        """背景問 GitHub；回傳有沒有開始。"""
        now = time.time()
        with self.lock:
            if self._busy or now - self._last_try < (REFRESH_MIN_S if force else GITHUB_EVERY_S):
                return False
            if not force and now - float(self._gh.get("fetched") or 0) < GITHUB_EVERY_S:
                return False
            self._busy = True
            self._last_try = now
        th = threading.Thread(target=self._fetch, name="versions-github", daemon=True)
        th.start()
        if wait:
            th.join(60)
        return True

    def _fetch(self) -> None:
        try:
            gh = self._github()
            if gh is None:
                raise RuntimeError("沒有 GitHub 設定")
            rels: List[Dict[str, Any]] = []
            for page in range(1, MAX_PAGES + 1):
                with gh._open(f"{gh.api}/repos/{gh.repo}/releases?per_page=100&page={page}") as r:
                    data = json.loads(r.read().decode("utf-8"))
                if not isinstance(data, list):
                    raise RuntimeError("GitHub 回傳的格式不對")
                for x in data:
                    if x.get("draft") or x.get("prerelease"):
                        continue
                    rels.append({"tag": str(x.get("tag_name") or ""), "name": str(x.get("name") or ""),
                                 "date": x.get("published_at") or "", "url": x.get("html_url") or "",
                                 "body": str(x.get("body") or "")[:20000]})
                if len(data) < 100:
                    break
            with self.lock:
                self._gh = {"releases": rels, "fetched": time.time()}
                self.error = ""
            self._write(self.gh_path, self._gh)
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.error = f"連不到 GitHub：{getattr(e, 'reason', e)}"
        finally:
            with self.lock:
                self._busy = False

    # ---- 合併 ----
    def view(self) -> Dict[str, Any]:
        self.refresh()
        b = self.bundled().get("apps") or {}
        with self.lock:
            gh = list(self._gh.get("releases") or [])
            gh_info = {"fetched": self._gh.get("fetched") or 0, "busy": self._busy, "error": self.error}
        local = self._load(self.local_path) or {}
        mods: Dict[str, Any] = {}
        plat = getattr(self.hub, "platform", None)
        if plat is not None:
            try:
                mods = plat.modules()
            except Exception:  # noqa: BLE001
                mods = {}
        apps = []
        for aid, prefix in PREFIX.items():
            ba = b.get(aid) or {}
            ent: Dict[str, Dict[str, Any]] = {}

            def get(v: str) -> Dict[str, Any]:
                k = ".".join(str(x) for x in vkey(v)[:3]) + (chr(vkey(v)[3]) if vkey(v)[3] else "")
                return ent.setdefault(k, {"version": v})

            for e in ba.get("entries") or []:
                x = get(e["version"])
                x.update(text=e.get("text") or "", detail=e.get("detail") or "", date=e.get("date") or "")
            for r in gh:
                if not r["tag"].startswith(prefix):
                    continue
                x = get(r["tag"][len(prefix):])
                x.update(published=r["date"], url=r["url"], title=r["name"])
                if not x.get("text"):
                    x["text"] = clean_body(r["body"], r["name"])
            cur = None
            if aid == "hub":
                cur = __version__
                for v, t in (local.get("hub") or {}).items():
                    get(v)["hub_time"] = t
            elif aid in MODULE and MODULE[aid] in mods:
                m = mods[MODULE[aid]]
                cur = m.get("latest")
                for v in m.get("versions") or []:
                    x = get(v["version"])
                    if v.get("time"):
                        x["hub_time"] = v["time"]
                        x["hub_by"] = v.get("by") or ""
                    if v.get("notes") and not x.get("text"):
                        x["text"] = str(v["notes"])
            rows = sorted(ent.values(), key=lambda e: vkey(e["version"]), reverse=True)
            for e in rows:
                e["current"] = bool(cur) and vkey(e["version"]) == vkey(cur)
            apps.append({"id": aid, "name": ba.get("name") or aid, "current": cur, "note": ba.get("note") or "",
                         "entries": rows})

        return {"ok": True, "apps": apps, "github": gh_info, "hub": __version__}


def store(hub: Any) -> Versions:
    v = getattr(hub, "_versions", None)
    if v is None:
        v = hub._versions = Versions(hub)
    return v


@route("GET", r"/api/versions", auth="any")
def _api(h: Handler) -> None:
    h._json(store(h.hub).view())


@route("POST", r"/api/versions/refresh", auth="any")
def _refresh(h: Handler) -> None:
    st = store(h.hub)
    started = st.refresh(force=True)
    h._json({"ok": True, "started": started, "github": {"busy": st._busy, "error": st.error}})


@route("GET", r"/versions/?", auth="any")
def _page(h: Handler) -> None:
    from .web_nav import page

    page(h, "versions.html", "versions")

