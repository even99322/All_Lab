"""辨識發佈 zip 是哪一個模塊、哪一版（發佈中心用：只要把 zip 丟進來，自動判斷要更新什麼）。

判斷順序：

1. zip 裡有 ``qel-module.json``（``{"id", "version", "name", "notes"}``）→ 照它；
2. 依內容判斷：

   | 內容 | 目標 | 版本來源 |
   |---|---|---|
   | ``labcontrol/__init__.py`` | 模塊 ``labcontrol``（量測模塊） | ``__version__`` |
   | ``labmonitor/__init__.py``（沒有 labcontrol） | 模塊 ``monitor``（平台監控） | ``__version__`` |
   | ``qel_launcher/__init__.py`` | 模塊 ``launcher``（量子工程啟動器） | ``__version__`` |
   | ``labhub/server.py``（只有 Hub） | 網站 ``hub``（NAS 中繼站） | ``labhub/__init__.py`` |
   | ``Dockerfile`` ＋ ``app/main.py`` ＋ ``app/config.py`` 的 ``VERSION`` | 網站 ``paperlib``（論文庫） | ``VERSION`` |
   | ``app/__init__.py`` 且 main.py 提到 LabLogViewer | 模塊 ``lablogviewer``（數據讀取模擬模塊） | ``__version__`` |

3. Windows / macOS 直接執行的安裝檔（啟動器打包工具產生）看檔名：
   ``QELLauncher_v0.0.17_windows.exe``、``QELLauncher_v0.0.17_macos-arm64.dmg`` → 模塊 ``launcher``、0.0.17、
   ``platform`` = windows / macos。發佈時加到那一版的檔案裡，平台網頁的「下載」按鈕會依作業系統給對的檔。

只用標準函式庫；zip 只讀，不解壓。
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

#: 目標 → (類型, 名稱)；web = 由 NAS 控制代理更新網站，module = 發佈到平台給啟動器安裝
TARGETS: Dict[str, tuple] = {
    "hub": ("web", "Hub 網站（NAS 中繼站）"),
    "paperlib": ("web", "論文庫網站"),
    "labcontrol": ("module", "量測模塊 Lab Control"),
    "lablogviewer": ("module", "數據讀取模擬模塊 LabLogViewer"),
    "monitor": ("module", "平台監控 Monitor"),
    "launcher": ("module", "量子工程平台 App（舊的啟動器，0.0.26 起不再使用）"),
    "setup": ("module", "Windows 安裝程式（量測＋讀檔一起裝，從 NAS 下載）"),
    "llvpets": ("module", "讀檔程式的寵物圖檔（選擇性下載）"),
}
MANIFEST = "qel-module.json"
#: 安裝檔檔名的開頭 → 模塊
BINARY_PREFIX = {"qellauncher": "launcher", "labcontrol": "labcontrol", "labcontrolmonitor": "monitor",
                 "labmonitor": "monitor", "lablogviewer": "lablogviewer", "qelsetup": "setup"}
BINARY_EXT = (".exe", ".msi", ".dmg", ".pkg")
_BIN = re.compile(r"^(?P<name>[A-Za-z]+)[_-]v?(?P<ver>\d+(?:\.\d+){1,3})[_-](?P<plat>windows|win64|win|macos|mac|osx)"
                  r"(?:[_-][A-Za-z0-9]+)*\.(?:exe|msi|dmg|pkg|zip)$", re.I)
_VER = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")
#: 沒有版本號的安裝檔名（LabLogViewer 的 GitHub Actions：LabLogViewer-Windows-setup.exe、LabLogViewer-macOS-universal.dmg）
_BIN_NOVER = re.compile(r"^(?P<name>[A-Za-z]+)[_-](?P<plat>windows|win64|win|macos|mac|osx)(?:[_-][A-Za-z0-9]+)*"
                        r"(?: \(\d+\))?\.(?:exe|msi|dmg|pkg|zip)$", re.I)


class PackageError(ValueError):
    pass


class NeedVersion(PackageError):
    """認得是哪個程式的安裝檔，但檔名與檔案裡都找不到版本號：請在控制台輸入版本號。"""


def pe_version(data: bytes) -> Optional[str]:
    """Windows 執行檔 / 安裝檔（Inno Setup、PyInstaller）版本資源裡的 ProductVersion（或 FileVersion）。"""
    if not data or data[:2] != b"MZ":
        return None
    for key in ("ProductVersion", "FileVersion"):
        tag = key.encode("utf-16-le") + b"\0\0"
        i = data.rfind(tag)
        if i < 0:
            continue
        j = i + len(tag)
        while j < len(data) - 1 and data[j:j + 2] == b"\0\0":      # 對齊用的 0
            j += 2
        end = data.find(b"\0\0", j)
        while end > 0 and (end - j) % 2:
            end = data.find(b"\0\0", end + 1)
        try:
            text = data[j:end].decode("utf-16-le").strip()
        except UnicodeDecodeError:
            continue
        m = re.match(r"\d+(?:\.\d+){0,3}", text)
        if m:
            v = m.group(0)
            parts = v.split(".")
            while len(parts) > 3 and parts[-1] == "0":                # 2.1.1.0 → 2.1.1
                parts.pop()
            return ".".join(parts)
    return None


def _open(src: Union[str, Path, bytes]) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else str(src))
    except zipfile.BadZipFile:
        raise PackageError("不是 zip 檔") from None


def _find(names: List[str], tail: str) -> Optional[str]:
    """最淺的、以 tail 結尾的路徑（tail 前面只能是資料夾邊界）。"""
    hits = [n for n in names if n == tail or n.endswith("/" + tail)]
    return min(hits, key=lambda n: n.count("/")) if hits else None


def _read(z: zipfile.ZipFile, name: Optional[str], limit: int = 2_000_000) -> str:
    if not name:
        return ""
    info = z.getinfo(name)
    if info.file_size > limit:
        return ""
    return z.read(name).decode("utf-8", "replace")


def _ver(text: str, rx: "re.Pattern[str]" = _VER) -> Optional[str]:
    m = rx.search(text or "")
    return m.group(1).strip() if m else None


def _notes(z: zipfile.ZipFile, names: List[str], version: str) -> str:
    """發佈說明：docs/releases/v<版本>.md 的「摘要」段落，或 update.txt 最後一段的標題。"""
    rel = _find(names, f"docs/releases/v{version}.md")
    text = _read(z, rel)
    if text:
        m = re.search(r"##\s*摘要[^\n]*\n(.*?)(?:\n##\s|\Z)", text, re.S)
        return (m.group(1) if m else text).strip()[:4000]
    upd = _read(z, _find(names, "update.txt"), 20_000_000)
    if upd:
        heads = re.findall(rf"^v{re.escape(version)}\s*-\s*(.+)$", upd, re.M)
        if heads:
            return heads[-1].strip()
    cl = _read(z, _find(names, "CHANGELOG.md"))
    m = re.search(rf"^##\s*{re.escape(version)}\b[^\n]*\n(.*?)(?:\n##\s|\n---|\Z)", cl, re.S | re.M)
    return m.group(1).strip()[:4000] if m else ""


def identify_binary(filename: str, data: Optional[bytes] = None, version: str = "") -> Optional[Dict[str, Any]]:
    """Windows / macOS 安裝檔（看檔名）→ {target, kind, name, version, platform}；不是就 None。

    檔名沒有版本號時（LabLogViewer-Windows-setup.exe）：Windows 執行檔讀版本資源，再不行就要 ``version``
    （控制台會請站長輸入），否則丟 NeedVersion。
    """
    name = Path(filename or "").name
    m = _BIN.match(name)
    ver = version or (m.group("ver") if m else "")
    if not m:
        m = _BIN_NOVER.match(name)
        if not m or m.group("name").lower() not in BINARY_PREFIX:
            return None
    if m.group("name").lower() not in BINARY_PREFIX:
        return None
    tid = BINARY_PREFIX[m.group("name").lower()]
    plat = "windows" if m.group("plat").lower().startswith("win") else "macos"
    label = {"windows": "Windows 執行檔", "macos": "macOS 安裝檔"}[plat]
    if not ver and data is not None and plat == "windows":
        ver = pe_version(data) or ""
    if not ver:
        raise NeedVersion(f"需要版本號：{name} 的檔名裡沒有版本（例如 {m.group('name')}_v2.1.1_{plat}.{name.rsplit('.', 1)[-1]}），"
                          "請輸入這個安裝檔的版本號")
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", ver):
        raise PackageError(f"版本號「{ver}」不對：只能是數字與點，例如 2.1.1")
    return {"target": tid, "kind": "module", "name": f"{TARGETS[tid][1]}（{label}）", "version": ver,
            "notes": "", "file": filename, "platform": plat}


def identify(src: Union[str, Path, bytes], filename: str = "", version: str = "") -> Dict[str, Any]:
    """→ {target, kind, name, version, notes, root, platform?}；認不出來丟 PackageError（缺版本號丟 NeedVersion）。"""
    fname = filename or (Path(src).name if isinstance(src, (str, Path)) else "")
    if fname.lower().endswith(BINARY_EXT):
        data = src if isinstance(src, bytes) else (Path(src).read_bytes() if Path(str(src)).is_file() else None)
        b = identify_binary(fname, data, version)
        if b is None:
            raise PackageError("認不出這個安裝檔：檔名要像 LabControl_v0.0.26_windows.exe、"
                               "LabLogViewer_v2.1.1_macos-universal.dmg")
        return b
    try:
        return _identify_zip(src, filename)
    except NeedVersion:
        raise
    except PackageError:
        b = identify_binary(fname, None, version)  # 例如 LabControl_v0.0.26_macos.zip（裡面是 .app）
        if b is not None:
            return b
        raise


def _identify_zip(src: Union[str, Path, bytes], filename: str = "") -> Dict[str, Any]:
    with _open(src) as z:
        names = [n for n in z.namelist() if not n.startswith("__MACOSX/") and not n.endswith("/")]
        if not names:
            raise PackageError("zip 是空的")
        man = _find(names, MANIFEST)
        if man and man.count("/") <= 1:
            try:
                d = json.loads(_read(z, man))
            except ValueError:
                raise PackageError(f"{MANIFEST} 格式錯誤") from None
            if d.get("kind") == "source":
                raise PackageError("這是原始碼包（給開發用），不用發佈")
            if d.get("kind") == "deploy":
                raise PackageError("這是 NAS 安裝包（用 Container Manager 建立專案用），不用發佈")
            tid, ver = str(d.get("id") or ""), str(d.get("version") or "")
            if d.get("base") and d.get("target"):
                # 模塊的更新包（只有改過的檔案）：Hub 拿平台上的 base 版完整 zip 合併成新版
                # （id 用 "<模塊>-patch"，舊版 Hub 不會把它誤當成完整的新版）
                tid = str(d["target"])
                if tid not in TARGETS or TARGETS[tid][0] != "module" or not ver:
                    raise PackageError(f"{MANIFEST} 的 target / version 不對")
                return {"target": tid, "kind": "module", "name": f"{TARGETS[tid][1]}（更新包，基底 {d['base']}）",
                        "version": ver, "base": str(d["base"]), "partial": True,
                        "notes": str(d.get("notes") or ""), "file": filename}
            if not re.fullmatch(r"[a-z0-9_-]{2,40}", tid) or not ver:
                raise PackageError(f"{MANIFEST} 需要 id 與 version")
            kind, name = TARGETS.get(tid, (d.get("kind") or "module", d.get("name") or tid))
            return {"target": tid, "kind": kind, "name": d.get("name") or name, "version": ver,
                    "notes": str(d.get("notes") or "") or _notes(z, names, ver), "file": filename}

        def found(tid: str, version: Optional[str]) -> Dict[str, Any]:
            if not version:
                raise PackageError(f"看得出是{TARGETS[tid][1]}，但讀不到版本號")
            kind, name = TARGETS[tid]
            return {"target": tid, "kind": kind, "name": name, "version": version,
                    "notes": _notes(z, names, version), "file": filename}

        lc = _find(names, "labcontrol/__init__.py")
        if lc:
            return found("labcontrol", _ver(_read(z, lc)))
        mon = _find(names, "labmonitor/__init__.py")
        if mon:
            return found("monitor", _ver(_read(z, mon)))
        la = _find(names, "qel_launcher/__init__.py")
        if la:
            return found("launcher", _ver(_read(z, la)))
        hub = _find(names, "labhub/server.py")
        if hub:
            return found("hub", _ver(_read(z, hub.rsplit("/", 1)[0] + "/__init__.py")))
        cfg = _find(names, "app/config.py")
        cfg_text = _read(z, cfg)
        if cfg and ((_find(names, "Dockerfile") and _find(names, "app/main.py")) or "PAPERLIB" in cfg_text):
            # 完整包（Dockerfile ＋ app/），或部分更新包（只有改過的 app/ 檔案，例如 paperlib 1.7.0 的更新 zip）
            v = _ver(cfg_text, re.compile(r"""^VERSION\s*=\s*["']([^"']+)["']""", re.M))
            if v:
                r = found("paperlib", v)
                r["partial"] = not _find(names, "Dockerfile")
                notes = [n for n in names if re.search(r"(^|/)UPDATE_NOTES[^/]*\.md$", n)]
                if notes and not r["notes"]:
                    r["notes"] = _read(z, notes[0]).strip()[:4000]
                if r["partial"]:
                    r["name"] = "論文庫網站（部分更新包）"
                return r
        pets = _find(names, "pets_manifest.json")
        if pets and pets.count("/") <= 1:
            try:
                return found("llvpets", str(json.loads(_read(z, pets)).get("version") or ""))
            except ValueError:
                raise PackageError("pets_manifest.json 格式錯誤") from None
        app = _find(names, "app/__init__.py")
        if app and "LabLogViewer" in _read(z, _find(names, "main.py")):
            return found("lablogviewer", _ver(_read(z, app)))
    raise PackageError("認不出這個 zip 是哪個模塊（可以在 zip 根目錄放 qel-module.json：{\"id\": …, \"version\": …}）")


def manifest(target: str, version: str, notes: str = "") -> str:
    kind, name = TARGETS.get(target, ("module", target))
    return json.dumps({"id": target, "version": version, "kind": kind, "name": name, "notes": notes},
                      ensure_ascii=False, indent=1)
