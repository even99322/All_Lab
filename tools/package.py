"""把模塊打包成發佈用的 zip（dist/<模塊>_v<版本>.zip），每個模塊各自打包、各自發佈。

    python tools/package.py                 # 全部
    python tools/package.py labcontrol labcomm
    python tools/package.py --check         # 只檢查 module.json 的版本和程式裡的版本一致

zip 裡一律是「<模塊>/module.json + 程式」。打好的 zip：
* 桌面模塊與通信模塊（labcontrol、lablogviewer、labcomm、launcher、monitor）→ 監控程式「模塊發佈」或網頁「管理 → 模塊發佈」；
* NAS 服務（portal、paperlib、labhub、agent）→ 監控程式「服務更新」。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ["comm/module.json", "portal/module.json", "agent/module.json", "launcher/module.json",
             "monitor/module.json", "modules/labcontrol/module.json", "modules/labcontrol/labhub.module.json",
             "modules/lablogviewer/module.json", "modules/paperlib/module.json"]
ALWAYS_SKIP = ["__pycache__", "*.pyc", ".DS_Store", ".pytest_cache", "*.egg-info", ".git", "Thumbs.db"]


def manifests() -> Dict[str, Tuple[Path, Dict]]:
    out = {}
    for rel in MANIFESTS:
        p = ROOT / rel
        d = json.loads(p.read_text(encoding="utf-8"))
        out[d["id"]] = (p, d)
    return out


def code_version(base: Path, man: Dict) -> str:
    vf = man.get("version_from")
    if not vf:
        return str(man["version"])
    m = re.search(vf["regex"], (base / vf["file"]).read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"{man['id']}：在 {vf['file']} 找不到版本")
    return m.group(1)


def check() -> List[str]:
    errs = []
    for mid, (p, man) in manifests().items():
        v = code_version(p.parent, man)
        if v != str(man["version"]):
            errs.append(f"{mid}：module.json 是 {man['version']}，程式裡是 {v}（{p.relative_to(ROOT)}）")
    return errs


def _skip(rel: str, patterns: List[str]) -> bool:
    parts = rel.split("/")
    for pat in ALWAYS_SKIP:
        if any(fnmatch.fnmatch(x, pat) for x in parts):
            return True
    for pat in patterns:
        if rel == pat or rel.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def build(mid: str, out_dir: Path) -> Path:
    p, man = manifests()[mid]
    base = p.parent
    ver = code_version(base, man)
    if ver != str(man["version"]):
        raise SystemExit(f"{mid}：module.json 版本 {man['version']} 和程式的 {ver} 不一致，先改 module.json")
    pkg = man.get("package") or {}
    include = pkg.get("include")
    exclude = pkg.get("exclude") or []
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{mid}_v{ver}.zip"
    clean = {k: v for k, v in man.items() if k not in ("package",)}
    n = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{mid}/module.json", json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
        roots = [base / x for x in include] if include else [base]
        for r in roots:
            files = [r] if r.is_file() else sorted(x for x in r.rglob("*") if x.is_file())
            for f in files:
                rel = f.relative_to(base).as_posix()
                if rel in ("module.json", "labhub.module.json") or _skip(rel, exclude):
                    continue
                if f.suffix.lower() in (".bat", ".cmd"):          # Windows 的批次檔一定要 CRLF，不管在哪裡打包
                    z.writestr(f"{mid}/{rel}", f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
                else:
                    z.write(f, f"{mid}/{rel}")
                n += 1
    print(f"{dst}：{n} 個檔案，{dst.stat().st_size / 1e6:.1f} MB")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modules", nargs="*")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    a = ap.parse_args()
    errs = check()
    if errs:
        print("\n".join(errs))
        return 1
    if a.check:
        print("版本一致")
        return 0
    for mid in a.modules or list(manifests()):
        build(mid, Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
