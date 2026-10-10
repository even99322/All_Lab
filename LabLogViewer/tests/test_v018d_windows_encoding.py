"""v0.18D: records load on Windows (cp950 / Big5 default encoding, Notepad BOM)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHINESE = {"available_tags": ["共振", "磁振子"], "assignments": {}}


def test_json_with_bom_is_read_not_treated_as_corrupt(tmp_path):
    from app.core.external_state import load_json_state

    path = tmp_path / "comments.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"note": "量測"}, ensure_ascii=False).encode("utf-8"))
    result = load_json_state(path, {})
    assert result.value == {"note": "量測"} and not result.recovered_from_corruption


def test_chinese_records_load_under_a_big5_default_encoding(tmp_path):
    """Reproduces the old Windows crash (tags.json read with the locale codec)."""
    (tmp_path / "tags.json").write_text(json.dumps(CHINESE, ensure_ascii=False), encoding="utf-8")
    script = (
        "import locale, sys; from pathlib import Path\n"
        "assert locale.getpreferredencoding(False).lower() in ('big5', 'cp950'), locale.getpreferredencoding(False)\n"
        "from app.core.tag_store import TagStore\n"
        f"store = TagStore(Path({str(tmp_path / 'tags.json')!r}), legacy_paths=[])\n"
        "print(sorted(store.list_tags()))\n"
    )
    env = {"LC_ALL": "zh_TW.BIG5", "LANG": "zh_TW.BIG5", "PYTHONUTF8": "0", "PYTHONIOENCODING": "utf-8", "HOME": str(tmp_path),
           "PATH": "/usr/bin:/bin"}
    completed = subprocess.run([sys.executable, "-X", "utf8=0", "-c", script], cwd=ROOT, env=env,
                               capture_output=True, text=True, encoding="utf-8")
    if "AssertionError" in completed.stderr and "getpreferredencoding" in completed.stderr:
        import pytest
        pytest.skip("Big5 locale not available on this machine")
    assert completed.returncode == 0, completed.stderr
    assert "共振" in completed.stdout
