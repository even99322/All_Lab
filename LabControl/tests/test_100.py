"""1.0.0 正式版：Hub 把讀檔程式的舊版號（2.x）移開，1.0.0 才會是最新版；讀檔 feed 帶 line: 1；上傳進度。"""
from __future__ import annotations

from pathlib import Path


def _version_dir(base: Path, mid: str, version: str, name: str) -> None:
    d = base / "modules" / mid / version
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"MZ")


def test_hub_moves_old_lablogviewer_versions_once(tmp_path):
    from labhub.appupdates import app_info, lablogviewer_feed
    from labhub.platform import Platform

    data = tmp_path / "hub"
    for v in ("2.1.5", "2.1.6"):
        _version_dir(data, "lablogviewer", v, f"LabLogViewer_v{v}_windows-setup.exe")
    _version_dir(data, "labcontrol", "0.0.32", "LabControl_v0.0.32_windows-setup.exe")
    plat = Platform(data, tmp_path / "files")
    assert not (data / "modules" / "lablogviewer" / "2.1.6").exists()
    assert (data / "modules_legacy" / "lablogviewer" / "2.1.6").is_dir()
    assert (data / "modules" / "labcontrol" / "0.0.32").is_dir()            # 量測程式不受影響
    plat.add_release("lablogviewer", "1.0.0", "LabLogViewer_v1.0.0_windows-setup.exe", b"MZ new", "t")
    assert app_info(plat, "lablogviewer")["version"] == "1.0.0"
    feed = lablogviewer_feed(plat)
    assert feed["version"] == "1.0.0" and feed["line"] == 1
    # 第二次啟動：1.0.x 不會再被搬走
    plat.add_release("lablogviewer", "1.0.1", "LabLogViewer_v1.0.1_windows-setup.exe", b"MZ newer", "t")
    Platform(data, tmp_path / "files")
    assert (data / "modules" / "lablogviewer" / "1.0.1").is_dir()


def test_console_upload_shows_progress():
    html = (Path(__file__).resolve().parents[1] / "labhub" / "static" / "portal.html").read_text(encoding="utf-8")
    assert "xhr.upload.onprogress" in html and 'id="upl"' in html and "uplcancel" in html
