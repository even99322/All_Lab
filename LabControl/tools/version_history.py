"""1.0.39：把 repo 裡各程式的修改紀錄整理成一個 JSON（Hub「版本紀錄」頁用；打包 Hub 時放進 labhub/version_history.json）。

來源：
    labcontrol    LabControl/CHANGELOG.md（## 版本 — 日期）＋ docs/releases/v<版本>.md（詳細）
    hub           LabControl/HUB_CHANGELOG.md ＋ docs/releases/hub/v<版本>.md（1.0.35 以前跟 Lab Control 一起發佈）
    netmeter      NetMeter/README.md（### v版本）
    hfss          HFSSTool/README.md（### v版本）
    lablogviewer  LabLogViewer/README.md（## v版本）
只用標準函式庫；``python tools/version_history.py [輸出.json]``。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
APPS = {
    "labcontrol": "Lab Control（量測）",
    "hub": "Hub（NAS 平台）",
    "netmeter": "QEL 流量監看",
    "hfss": "HFSS 自動化工具（模擬）",
    "lablogviewer": "LabLogViewer（讀檔）",
}
_VER = r"v?(\d+(?:\.\d+){1,3}[A-Za-z]?)"


def _sections(text: str, level: str) -> List[Dict[str, Any]]:
    """依「## 版本 — 日期」或「### v版本」切段；回傳 [{version, date, text}]（照檔案順序）。"""
    out: List[Dict[str, Any]] = []
    head = re.compile(rf"^{level}\s+{_VER}(?:\s*[—–-]+\s*(\d{{4}}-\d{{2}}-\d{{2}}))?[^\n]*$", re.M)
    ms = list(head.finditer(text))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        body = text[m.end():end]
        body = re.split(r"^#{1,%d}\s" % len(level), body, maxsplit=1, flags=re.M)[0]   # 下一個同級或更高的標題
        body = re.sub(r"\n-{3,}\s*$", "", body.strip()).strip()
        out.append({"version": m.group(1), "date": m.group(2) or "", "text": body})
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _detail(p: Path) -> str:
    t = _read(p)
    return re.sub(r"\A#[^\n]*\n+", "", t).strip()          # 拿掉第一行標題（頁面上已經有版本號）


def _strip_links(t: str) -> str:
    """CHANGELOG 的「詳見 [docs/…](docs/…)」在 Hub 上點不到 → 拿掉。"""
    return re.sub(r"\s*詳見\s*\[[^\]]*\]\([^)]*\)。?", "", t).strip()


def build(repo: Optional[Path] = None) -> Dict[str, Any]:
    r = Path(repo or REPO)
    lc = r / "LabControl"
    apps: Dict[str, Dict[str, Any]] = {k: {"name": v, "entries": []} for k, v in APPS.items()}
    for e in _sections(_read(lc / "CHANGELOG.md"), "##"):
        e["text"] = _strip_links(e["text"])
        e["detail"] = _detail(lc / "docs" / "releases" / f"v{e['version']}.md")
        apps["labcontrol"]["entries"].append(e)
    for e in _sections(_read(lc / "HUB_CHANGELOG.md"), "##"):
        e["text"] = _strip_links(e["text"])
        e["detail"] = _detail(lc / "docs" / "releases" / "hub" / f"v{e['version']}.md")
        apps["hub"]["entries"].append(e)
    apps["hub"]["note"] = "1.0.35 以前 Hub 跟 Lab Control 同一個版本號一起發佈，內容見 Lab Control 的紀錄。"
    for key, path, level in (("netmeter", r / "NetMeter" / "README.md", "###"),
                             ("hfss", r / "HFSSTool" / "README.md", "###"),
                             ("lablogviewer", r / "LabLogViewer" / "README.md", "##")):
        apps[key]["entries"] = _sections(_read(path), level)
    for a in apps.values():                                      # 同一版只留第一段
        seen = set()
        a["entries"] = [e for e in a["entries"] if not (e["version"] in seen or seen.add(e["version"]))]
    return {"apps": apps}


def main(argv: List[str]) -> int:
    data = build()
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
        n = {k: len(v["entries"]) for k, v in data["apps"].items()}
        print(f"{argv[1]}：{n}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
