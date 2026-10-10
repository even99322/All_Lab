"""Pet backpack: items the user keeps with the pet (Lv.1: 5 slots ... Lv.50: 50).

Measurement files are stored as references only (path + size + modification time), never copied.
Pictures and text (e.g. a fit summary) are stored as payloads in ``state/pet_backpack/``.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from app.core.external_state import atomic_write_json, default_state_path

MEASUREMENT_SUFFIXES = {".hdf5", ".h5", ".hdf", ".nc"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".pdf"}


class BackpackFull(Exception):
    pass


def folder() -> Path:
    return default_state_path("pet_backpack")


def _index() -> Path:
    return folder() / "index.json"


def items() -> list[dict]:
    try:
        data = json.loads(_index().read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(values: list[dict]) -> None:
    folder().mkdir(parents=True, exist_ok=True)
    atomic_write_json(_index(), values)


def _check(capacity: int, values: list[dict]) -> None:
    if len(values) >= capacity:
        raise BackpackFull()


def add_path(path: str, capacity: int) -> dict:
    """A file dropped on the pet: measurements by reference, pictures / other files copied."""
    values = items()
    _check(capacity, values)
    src = Path(path)
    item = {"id": uuid.uuid4().hex[:12], "title": src.name, "time": time.time()}
    if src.suffix.lower() in MEASUREMENT_SUFFIXES:
        st = src.stat()
        item.update(kind="measurement", path=str(src.resolve()), size=st.st_size, mtime=st.st_mtime)
    else:
        payload = folder() / f"{item['id']}{src.suffix.lower()}"
        folder().mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, payload)
        item.update(kind="image" if src.suffix.lower() in IMAGE_SUFFIXES else "file", payload=payload.name)
    values.append(item)
    _save(values)
    return item


def add_image(qimage, title: str, capacity: int) -> dict:
    values = items()
    _check(capacity, values)
    item = {"id": uuid.uuid4().hex[:12], "title": title, "time": time.time(), "kind": "image"}
    folder().mkdir(parents=True, exist_ok=True)
    payload = folder() / f"{item['id']}.png"
    qimage.save(str(payload))
    item["payload"] = payload.name
    values.append(item)
    _save(values)
    return item


def add_text(text: str, title: str, capacity: int) -> dict:
    values = items()
    _check(capacity, values)
    item = {"id": uuid.uuid4().hex[:12], "title": title, "time": time.time(), "kind": "text", "text": str(text)[:20000]}
    values.append(item)
    _save(values)
    return item


def payload_path(item: dict) -> Path | None:
    return folder() / item["payload"] if item.get("payload") else None


def remove(item_id: str) -> None:
    values = items()
    for item in values:
        if item["id"] == item_id and item.get("payload"):
            try:
                (folder() / item["payload"]).unlink()
            except OSError:
                pass
    _save([v for v in values if v["id"] != item_id])


def clear() -> None:
    for item in items():
        remove(item["id"])


def export(item_ids: list[str], target: str) -> int:
    """Copy payloads / referenced files of the chosen items into ``target``; never overwrites."""
    out = Path(target)
    count = 0
    for item in items():
        if item["id"] not in item_ids:
            continue
        if item["kind"] == "text":
            dst = _free(out / f"{Path(item['title']).stem}.txt")
            dst.write_text(item.get("text", ""), encoding="utf-8")
        else:
            src = payload_path(item) if item.get("payload") else Path(item.get("path", ""))
            if src is None or not src.exists():
                continue
            name = item["title"] if Path(item["title"]).suffix else f"{item['title']}{src.suffix}"
            dst = _free(out / name)
            shutil.copy2(src, dst)
        count += 1
    return count


def _free(path: Path) -> Path:
    """Same-name check before writing: add (2), (3)... instead of overwriting."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1
